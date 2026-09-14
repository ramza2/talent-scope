"""Hybrid people search orchestration (structured + keyword + semantic)."""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.core.config import get_settings
from app.core.exceptions import SearchEmbeddingUnavailableError
from app.modules.people.repository import PeopleRepository
from app.modules.search.embedding_policy import prepare_embedding_input
from app.modules.search.query_repository import EligiblePersonRow, SearchQueryRepository
from app.modules.search.query_schemas import (
    MatchItem,
    PreferredConditionBlock,
    SearchMeta,
    SearchPeopleRequest,
    SearchPeopleResponse,
    SearchPersonResult,
    SearchPersonSummary,
)
from app.modules.search.schemas import GRADE_LABELS
from app.modules.search.ranking import (
    EXPERTISE_DISPLAY_CAP,
    SKILL_DISPLAY_CAP,
    ChannelHit,
    MergedCandidate,
    channel_candidate_limit,
    compute_hybrid_relevance,
    merge_channel_hits,
    relevance_to_score,
)


class SearchQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SearchQueryRepository(db)
        self.people_repo = PeopleRepository(db)

    def search_people(self, request: SearchPeopleRequest) -> SearchPeopleResponse:
        expanded = self.repo.validate_and_expand_codes(request)
        preferred_present = self._preferred_present(request.preferred)

        eligible_subq = self.repo.eligible_person_ids_subquery(
            required=request.required,
            expanded=expanded,
            skill_match_mode=request.skill_match_mode,
        )
        eligible_rows = self.repo.list_eligible_persons(
            required=request.required,
            expanded=expanded,
            skill_match_mode=request.skill_match_mode,
        )
        eligible_by_id = {row.person_id: row for row in eligible_rows}

        limit = channel_candidate_limit(page=request.page, page_size=request.page_size)
        candidate_limit_reached = False

        keyword_hits: list[ChannelHit] = []
        semantic_hits: list[ChannelHit] = []

        if request.keyword_query:
            keyword_hits, kw_trunc = self.repo.keyword_channel_hits(
                keyword=request.keyword_query,
                eligible_subq=eligible_subq,
                limit=limit,
            )
            candidate_limit_reached = candidate_limit_reached or kw_trunc

        if request.semantic_query:
            query_vector = self._embed_semantic_query(request.semantic_query)
            semantic_hits, sem_trunc = self.repo.semantic_channel_hits(
                query_vector=query_vector,
                eligible_subq=eligible_subq,
                limit=limit,
            )
            candidate_limit_reached = candidate_limit_reached or sem_trunc

        if request.keyword_query or request.semantic_query:
            merged = merge_channel_hits(
                keyword_hits=keyword_hits,
                semantic_hits=semantic_hits,
            )
            # Drop anyone who slipped past eligibility (defensive).
            merged = {
                pid: cand
                for pid, cand in merged.items()
                if pid in eligible_by_id
            }
        else:
            merged = {
                pid: MergedCandidate(person_id=pid) for pid in eligible_by_id
            }

        person_ids = list(merged.keys())
        preferred_ratios = self.repo.preferred_match_ratio(
            person_ids,
            preferred=request.preferred,
            expanded_preferred_jobs=expanded["preferred_jobs"],
        )

        for pid, cand in merged.items():
            row = eligible_by_id[pid]
            ratio = preferred_ratios.get(pid, 0.0)
            cand.preferred_match_ratio = ratio
            kw_rank = cand.keyword_hit.rank if cand.keyword_hit else None
            sem_rank = cand.semantic_hit.rank if cand.semantic_hit else None
            relevance = compute_hybrid_relevance(
                keyword_rank=kw_rank,
                semantic_rank=sem_rank,
                preferred_match_ratio=ratio,
                preferred_present=preferred_present,
            )
            cand.relevance = relevance
            cand.score = relevance_to_score(relevance)
            cand.sort_keys = {
                "name": row.name or "",
                "career_months": row.career_months,
                "profile_updated_at": row.profile_updated_at,
                "recent_project_date": row.recent_project_date,
                "preferred_match_ratio": ratio,
                "score": cand.score,
                "person_id": pid,
            }

        ordered = self._sort_candidates(list(merged.values()), sort=request.sort)
        total = len(ordered)
        total_pages = math.ceil(total / request.page_size) if total else 0
        start = (request.page - 1) * request.page_size
        page_slice = ordered[start : start + request.page_size]

        results = self._build_results(
            page_slice,
            eligible_by_id=eligible_by_id,
            request=request,
        )

        return SearchPeopleResponse(
            data=results,
            meta=SearchMeta(
                page=request.page,
                page_size=request.page_size,
                total=total,
                total_pages=total_pages,
                candidate_limit_reached=candidate_limit_reached,
            ),
            query=self._echo_query(request),
            relaxations=[],
        )

    def _embed_semantic_query(self, semantic_query: str) -> list[float]:
        settings = get_settings()
        if not settings.embedding_enabled:
            raise SearchEmbeddingUnavailableError()
        try:
            prepared = prepare_embedding_input(semantic_query)
        except ValueError:
            raise SearchEmbeddingUnavailableError() from None

        # Provider call must happen outside long DB lock windows (read-only here).
        try:
            from app.ai.providers.embedding import get_embedding_provider

            vector = get_embedding_provider().embed_text(prepared)
        except (AIProviderError, AIResponseValidationError, Exception):
            # Never leak provider/SQL details to clients.
            raise SearchEmbeddingUnavailableError() from None

        if not isinstance(vector, list) or not vector:
            raise SearchEmbeddingUnavailableError()
        return [float(v) for v in vector]

    @staticmethod
    def _preferred_present(preferred: PreferredConditionBlock) -> bool:
        return bool(
            preferred.jobs
            or preferred.skills
            or preferred.expertise
            or preferred.business_domains
            or preferred.customer_types
        )

    def _sort_candidates(
        self, candidates: list[MergedCandidate], *, sort: str
    ) -> list[MergedCandidate]:
        def _ts(value: Any) -> float:
            if value is None:
                return 0.0
            try:
                return float(value.timestamp())
            except Exception:
                return 0.0

        if sort == "CAREER_DESC":

            def key_career(c: MergedCandidate) -> tuple:
                months = c.sort_keys.get("career_months")
                null_flag = 1 if months is None else 0
                return (null_flag, -(months or 0), -c.score, str(c.person_id))

            return sorted(candidates, key=key_career)

        if sort == "UPDATED_DESC":

            def key_updated(c: MergedCandidate) -> tuple:
                updated = c.sort_keys.get("profile_updated_at")
                return (-_ts(updated), -c.score, str(c.person_id))

            return sorted(candidates, key=key_updated)

        if sort == "NAME_ASC":

            def key_name(c: MergedCandidate) -> tuple:
                return (str(c.sort_keys.get("name") or ""), str(c.person_id))

            return sorted(candidates, key=key_name)

        if sort == "RECENT_PROJECT_DESC":

            def key_recent(c: MergedCandidate) -> tuple:
                recent = c.sort_keys.get("recent_project_date")
                if recent is None:
                    return (1, 0, -c.score, str(c.person_id))
                return (0, -recent.toordinal(), -c.score, str(c.person_id))

            return sorted(candidates, key=key_recent)

        def key_relevance(c: MergedCandidate) -> tuple:
            updated = c.sort_keys.get("profile_updated_at")
            return (
                -c.score,
                -float(c.preferred_match_ratio),
                -_ts(updated),
                str(c.person_id),
            )

        return sorted(candidates, key=key_relevance)

    def _build_results(
        self,
        page_slice: list[MergedCandidate],
        *,
        eligible_by_id: dict[UUID, EligiblePersonRow],
        request: SearchPeopleRequest,
    ) -> list[SearchPersonResult]:
        person_ids = [c.person_id for c in page_slice]
        if not person_ids:
            return []

        jobs_map = self.people_repo.jobs_for_people(person_ids)
        skills_map = self.people_repo.skills_for_people(
            person_ids, limit_per_person=SKILL_DISPLAY_CAP
        )
        exp_map = self.people_repo.expertise_for_people(
            person_ids, limit_per_person=EXPERTISE_DISPLAY_CAP
        )

        preferred_job_root_hits: dict[str, set[UUID]] = {}
        for root in request.preferred.jobs:
            expanded_root = self.repo._expand_job_codes([root])
            preferred_job_root_hits[root] = self.repo._person_ids_matching_job(
                person_ids, expanded_root
            )

        preferred_skill_by_code: dict[str, set[UUID]] = {
            code: self.repo._person_ids_matching_skill_any(person_ids, [code])
            for code in request.preferred.skills
        }
        preferred_exp_by_code: dict[str, set[UUID]] = {
            code: self.repo._person_ids_matching_expertise(person_ids, [code])
            for code in request.preferred.expertise
        }
        preferred_biz_by_code: dict[str, set[UUID]] = {
            code: self.repo._person_ids_matching_biz(person_ids, [code])
            for code in request.preferred.business_domains
        }
        preferred_cust_by_code: dict[str, set[UUID]] = {
            code: self.repo._person_ids_matching_customer(person_ids, [code])
            for code in request.preferred.customer_types
        }

        code_names = self.repo.load_code_names(
            list(
                {
                    *request.required.jobs,
                    *request.required.skills,
                    *request.required.expertise,
                    *request.required.business_domains,
                    *request.required.customer_types,
                    *request.preferred.jobs,
                    *request.preferred.skills,
                    *request.preferred.expertise,
                    *request.preferred.business_domains,
                    *request.preferred.customer_types,
                }
            )
        )

        results: list[SearchPersonResult] = []
        for cand in page_slice:
            row = eligible_by_id[cand.person_id]
            primary_jobs = [
                name
                for job, name in jobs_map.get(cand.person_id, [])
                if job.job_type == "PRIMARY"
            ]
            if not primary_jobs:
                primary_jobs = [name for _, name in jobs_map.get(cand.person_id, [])[:3]]

            summary = SearchPersonSummary(
                name=row.name,
                technical_grade=row.technical_grade,
                career_months=row.career_months,
                primary_jobs=primary_jobs,
                skills=[name for _, name in skills_map.get(cand.person_id, [])],
                expertise=[name for _, name in exp_map.get(cand.person_id, [])],
            )
            matches = self._build_matches(
                person_id=cand.person_id,
                request=request,
                code_names=code_names,
                preferred_job_root_hits=preferred_job_root_hits,
                preferred_skill_by_code=preferred_skill_by_code,
                preferred_exp_by_code=preferred_exp_by_code,
                preferred_biz_by_code=preferred_biz_by_code,
                preferred_cust_by_code=preferred_cust_by_code,
            )
            results.append(
                SearchPersonResult(
                    person_id=cand.person_id,
                    score=cand.score,
                    person=summary,
                    matches=matches,
                    top_projects=[],
                    evidence=[],
                )
            )
        return results


    def _build_matches(
        self,
        *,
        person_id: UUID,
        request: SearchPeopleRequest,
        code_names: dict[str, str],
        preferred_job_root_hits: dict[str, set[UUID]],
        preferred_skill_by_code: dict[str, set[UUID]],
        preferred_exp_by_code: dict[str, set[UUID]],
        preferred_biz_by_code: dict[str, set[UUID]],
        preferred_cust_by_code: dict[str, set[UUID]],
    ) -> list[MatchItem]:
        """Build match scaffold.

        Required same-field OR values are aggregated into one MATCH condition
        (candidate already passed hard filters). Preferred stays per-code.
        skill_match_mode=ALL keeps per-skill REQUIRED MATCH items.
        """
        items: list[MatchItem] = []
        req = request.required

        def _label(code: str) -> str:
            return code_names.get(code) or code

        def _grade_label(grade: str) -> str:
            return GRADE_LABELS.get(grade, grade)

        def _req(condition: str) -> None:
            items.append(
                MatchItem(
                    condition=condition,
                    type="REQUIRED",
                    status="MATCH",
                    evidence_count=0,
                )
            )

        def _req_or_group(labels: list[str]) -> None:
            if not labels:
                return
            if len(labels) == 1:
                _req(labels[0])
            else:
                _req(" OR ".join(labels))

        # Same-field OR → single aggregated REQUIRED MATCH.
        _req_or_group([_label(c) for c in req.jobs])
        if request.skill_match_mode == "ALL":
            for code in req.skills:
                _req(_label(code))
        else:
            _req_or_group([_label(c) for c in req.skills])
        _req_or_group([_label(c) for c in req.expertise])
        _req_or_group([_label(c) for c in req.business_domains])
        _req_or_group([_label(c) for c in req.customer_types])
        if req.grade and req.grade.values:
            _req_or_group([_grade_label(g) for g in req.grade.values])
        if req.career and (
            req.career.min_months is not None or req.career.max_months is not None
        ):
            lo = req.career.min_months
            hi = req.career.max_months
            _req(f"career:{lo or ''}-{hi if hi is not None else ''}")
        _req_or_group(list(req.affiliations))
        _req_or_group(list(req.certifications))
        _req_or_group(list(req.project_keywords))

        pref = request.preferred
        for code in pref.jobs:
            status = (
                "MATCH"
                if person_id in preferred_job_root_hits.get(code, set())
                else "NO_MATCH"
            )
            items.append(
                MatchItem(
                    condition=_label(code),
                    type="PREFERRED",
                    status=status,
                    evidence_count=0,
                )
            )
        for code in pref.skills:
            status = (
                "MATCH"
                if person_id in preferred_skill_by_code.get(code, set())
                else "NO_MATCH"
            )
            items.append(
                MatchItem(
                    condition=_label(code),
                    type="PREFERRED",
                    status=status,
                    evidence_count=0,
                )
            )
        for code in pref.expertise:
            status = (
                "MATCH"
                if person_id in preferred_exp_by_code.get(code, set())
                else "NO_MATCH"
            )
            items.append(
                MatchItem(
                    condition=_label(code),
                    type="PREFERRED",
                    status=status,
                    evidence_count=0,
                )
            )
        for code in pref.business_domains:
            status = (
                "MATCH"
                if person_id in preferred_biz_by_code.get(code, set())
                else "NO_MATCH"
            )
            items.append(
                MatchItem(
                    condition=_label(code),
                    type="PREFERRED",
                    status=status,
                    evidence_count=0,
                )
            )
        for code in pref.customer_types:
            status = (
                "MATCH"
                if person_id in preferred_cust_by_code.get(code, set())
                else "NO_MATCH"
            )
            items.append(
                MatchItem(
                    condition=_label(code),
                    type="PREFERRED",
                    status=status,
                    evidence_count=0,
                )
            )
        return items

    @staticmethod
    def _echo_query(request: SearchPeopleRequest) -> dict[str, Any]:
        return {
            "required": request.required.model_dump(),
            "preferred": request.preferred.model_dump(),
            "skill_match_mode": request.skill_match_mode,
            "semantic_query": request.semantic_query,
            "keyword_query": request.keyword_query,
            "sort": request.sort,
            "page": request.page,
            "page_size": request.page_size,
            "suggest_relaxations": request.suggest_relaxations,
        }
