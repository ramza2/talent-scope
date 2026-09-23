"""Hybrid people search orchestration (structured + keyword + semantic)."""

from __future__ import annotations

import logging
import time

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
    SearchRelaxation,
)
from app.modules.search.schemas import GRADE_LABELS
from app.modules.search.ranking import (
    EXPERTISE_DISPLAY_CAP,
    SEARCH_RANKING_POLICY_VERSION,
    SKILL_DISPLAY_CAP,
    ChannelHit,
    MergedCandidate,
    channel_candidate_limit,
    compute_final_relevance,
    compute_retrieval_relevance,
    merge_channel_hits,
    relevance_to_score,
)
from app.modules.search.project_ranking import (
    JobConditionGroup,
    ProjectRankingRepository,
    build_project_query_signals,
)
from app.modules.search.result_enrichment import SearchResultEnricher


logger = logging.getLogger(__name__)

_MAX_RELAXATIONS = 3
_CODE_FIELD_LABELS: dict[str, str] = {
    "jobs": "직무",
    "skills": "기술",
    "expertise": "전문분야",
    "business_domains": "사업분야",
    "customer_types": "고객유형",
}
_TEXT_FIELD_LABELS: dict[str, str] = {
    "affiliations": "소속",
    "certifications": "자격",
    "project_keywords": "프로젝트 키워드",
}


def _executable_query_dict(request: SearchPeopleRequest) -> dict[str, Any]:
    return {
        "required": request.required.model_dump(),
        "preferred": request.preferred.model_dump(),
        "skill_match_mode": request.skill_match_mode,
        "semantic_query": request.semantic_query,
        "keyword_query": request.keyword_query,
        "sort": request.sort,
    }


def build_search_relaxations(request: SearchPeopleRequest) -> list[SearchRelaxation]:
    """Build up to 3 deterministic relaxation suggestions for a zero-hit query.

    Does not mutate ``request``. Does not run search / LLM / embedding.
    """
    out: list[SearchRelaxation] = []
    seen: set[str] = set()

    def _add(relaxation_id: str, label: str, candidate: SearchPeopleRequest) -> bool:
        if len(out) >= _MAX_RELAXATIONS:
            return False
        suggested = _executable_query_dict(candidate)
        fingerprint = repr(suggested)
        if fingerprint in seen:
            return True
        seen.add(fingerprint)
        out.append(
            SearchRelaxation(
                id=relaxation_id,
                label=label,
                suggested_query=suggested,
            )
        )
        return len(out) < _MAX_RELAXATIONS

    if len(request.required.skills) >= 2 and request.skill_match_mode == "ALL":
        candidate = request.model_copy(deep=True)
        candidate.skill_match_mode = "ANY"
        if not _add(
            "skill_match_any",
            "필수 기술을 모두 만족하는 조건을 하나 이상 만족으로 완화",
            candidate,
        ):
            return out

    career = request.required.career
    if career is not None and career.min_months is not None and career.min_months > 0:
        old_min = career.min_months
        new_min = max(0, old_min - 12)
        candidate = request.model_copy(deep=True)
        assert candidate.required.career is not None
        candidate.required.career.min_months = new_min
        if not _add(
            "career_min_minus_12",
            f"최소 경력을 {old_min}개월에서 {new_min}개월로 완화",
            candidate,
        ):
            return out

    if request.required.grade is not None and request.required.grade.values:
        candidate = request.model_copy(deep=True)
        candidate.required.grade = None
        if not _add(
            "drop_required_grade",
            "기술등급 필수조건 제거",
            candidate,
        ):
            return out

    for field, label in _CODE_FIELD_LABELS.items():
        codes = list(getattr(request.required, field) or [])
        if not codes:
            continue
        code = codes[0]
        candidate = request.model_copy(deep=True)
        setattr(candidate.required, field, codes[1:])
        preferred_codes = list(getattr(candidate.preferred, field) or [])
        if code not in preferred_codes:
            preferred_codes.append(code)
        setattr(candidate.preferred, field, preferred_codes)
        if not _add(
            f"required_to_preferred_{field}_{code}",
            f"필수 {label} {code}을(를) 우대조건으로 완화",
            candidate,
        ):
            return out

    for field, label in _TEXT_FIELD_LABELS.items():
        values = list(getattr(request.required, field) or [])
        if not values:
            continue
        value = values[0]
        candidate = request.model_copy(deep=True)
        setattr(candidate.required, field, values[1:])
        if not _add(
            f"drop_required_{field}",
            f'필수 {label} 조건 "{value}" 제거',
            candidate,
        ):
            return out

    if request.keyword_query:
        candidate = request.model_copy(deep=True)
        candidate.keyword_query = None
        _add(
            "drop_keyword_query",
            "키워드 검색어 제거",
            candidate,
        )

    return out


class SearchQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SearchQueryRepository(db)
        self.people_repo = PeopleRepository(db)
        self.project_ranker = ProjectRankingRepository(db)
        self.enricher = SearchResultEnricher(db)

    def search_people(self, request: SearchPeopleRequest) -> SearchPeopleResponse:
        t_total = time.perf_counter()
        expanded = self.repo.validate_and_expand_codes(request)
        preferred_present = self._preferred_present(request.preferred)
        required_present = self._required_present(request)
        has_retrieval = bool(request.keyword_query or request.semantic_query)

        t0 = time.perf_counter()
        eligible_subq = self.repo.eligible_person_ids_subquery(
            required=request.required,
            expanded=expanded,
            skill_match_mode=request.skill_match_mode,
        )
        eligible_by_id: dict[UUID, EligiblePersonRow]
        if has_retrieval:
            # Hard filters still apply via eligible_subq inside channel SQL.
            # Defer profile/project metadata until after candidate merge.
            eligible_by_id = {}
        else:
            eligible_rows = self.repo.list_eligible_persons(
                required=request.required,
                expanded=expanded,
                skill_match_mode=request.skill_match_mode,
            )
            eligible_by_id = {row.person_id: row for row in eligible_rows}
        eligible_ms = (time.perf_counter() - t0) * 1000.0

        limit = channel_candidate_limit(page=request.page, page_size=request.page_size)
        candidate_limit_reached = False

        keyword_hits: list[ChannelHit] = []
        semantic_hits: list[ChannelHit] = []
        query_vector: list[float] | None = None
        keyword_ms = 0.0
        embedding_ms = 0.0
        semantic_ms = 0.0

        if request.keyword_query:
            t0 = time.perf_counter()
            keyword_hits, kw_trunc = self.repo.keyword_channel_hits(
                keyword=request.keyword_query,
                eligible_subq=eligible_subq,
                limit=limit,
            )
            keyword_ms = (time.perf_counter() - t0) * 1000.0
            candidate_limit_reached = candidate_limit_reached or kw_trunc

        if request.semantic_query:
            t0 = time.perf_counter()
            query_vector = self._embed_semantic_query(request.semantic_query)
            embedding_ms = (time.perf_counter() - t0) * 1000.0
            t0 = time.perf_counter()
            semantic_hits, sem_trunc = self.repo.semantic_channel_hits(
                query_vector=query_vector,
                eligible_subq=eligible_subq,
                limit=limit,
                force_exact=required_present,
            )
            semantic_ms = (time.perf_counter() - t0) * 1000.0
            candidate_limit_reached = candidate_limit_reached or sem_trunc

        if has_retrieval:
            merged = merge_channel_hits(
                keyword_hits=keyword_hits,
                semantic_hits=semantic_hits,
            )
            t0 = time.perf_counter()
            eligible_by_id = self.repo.load_eligible_persons_by_ids(list(merged.keys()))
            eligible_ms += (time.perf_counter() - t0) * 1000.0
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
        t0 = time.perf_counter()
        preferred_ratios = self.repo.preferred_match_ratio(
            person_ids,
            preferred=request.preferred,
            expanded_preferred_jobs=expanded["preferred_jobs"],
        )
        preferred_ms = (time.perf_counter() - t0) * 1000.0

        required_job_groups = self._job_condition_groups(request.required.jobs)
        preferred_job_groups = self._job_condition_groups(request.preferred.jobs)
        project_signals = build_project_query_signals(
            request,
            required_job_groups=required_job_groups,
            preferred_job_groups=preferred_job_groups,
        )
        t0 = time.perf_counter()
        project_summaries = self.project_ranker.summarize_persons(
            person_ids,
            request=request,
            signals=project_signals,
            query_vector=query_vector,
        )
        project_ms = (time.perf_counter() - t0) * 1000.0

        no_query = (
            not required_present
            and not preferred_present
            and not request.keyword_query
            and not request.semantic_query
        )

        for pid, cand in merged.items():
            row = eligible_by_id[pid]
            ratio = preferred_ratios.get(pid, 0.0)
            cand.preferred_match_ratio = ratio
            kw_rank = cand.keyword_hit.rank if cand.keyword_hit else None
            sem_rank = cand.semantic_hit.rank if cand.semantic_hit else None
            retrieval = compute_retrieval_relevance(
                keyword_rank=kw_rank,
                semantic_rank=sem_rank,
            )
            summary = project_summaries.get(pid)
            project_score = (
                None
                if not project_signals.active
                else (summary.project_relevance if summary else 0.0)
            )
            # No related projects → recency inactive (None), not undated bonus 0.60.
            recency_score = (
                None
                if not project_signals.active
                else (summary.recency_score if summary else None)
            )
            if no_query:
                relevance = 0.0
            else:
                relevance = compute_final_relevance(
                    required_score=1.0 if required_present else None,
                    retrieval_score=retrieval,
                    preferred_score=ratio if preferred_present else None,
                    project_score=project_score,
                    recency_score=recency_score,
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
                "ranking_policy": SEARCH_RANKING_POLICY_VERSION,
            }

        ordered = self._sort_candidates(list(merged.values()), sort=request.sort)
        total = len(ordered)
        total_pages = math.ceil(total / request.page_size) if total else 0
        start = (request.page - 1) * request.page_size
        page_slice = ordered[start : start + request.page_size]

        t0 = time.perf_counter()
        results = self._build_results(
            page_slice,
            eligible_by_id=eligible_by_id,
            request=request,
            project_summaries=project_summaries,
            keyword_hits=keyword_hits,
            semantic_hits=semantic_hits,
            required_job_groups=required_job_groups,
            preferred_job_groups=preferred_job_groups,
        )
        enrichment_ms = (time.perf_counter() - t0) * 1000.0
        total_ms = (time.perf_counter() - t_total) * 1000.0

        # Aggregate timing only — never log query text, vectors, names, or snippets.
        logger.info(
            "search_people completed",
            extra={
                "ranking_policy": SEARCH_RANKING_POLICY_VERSION,
                "page": request.page,
                "page_size": request.page_size,
                "eligible_count": len(eligible_by_id) if not has_retrieval else None,
                "merged_candidate_count": len(merged),
                "keyword_enabled": bool(request.keyword_query),
                "semantic_enabled": bool(request.semantic_query),
                "candidate_limit_reached": candidate_limit_reached,
                "eligible_ms": round(eligible_ms, 2),
                "keyword_ms": round(keyword_ms, 2),
                "embedding_ms": round(embedding_ms, 2),
                "semantic_ms": round(semantic_ms, 2),
                "preferred_ms": round(preferred_ms, 2),
                "project_ms": round(project_ms, 2),
                "enrichment_ms": round(enrichment_ms, 2),
                "total_ms": round(total_ms, 2),
            },
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
            relaxations=(
                build_search_relaxations(request)
                if request.suggest_relaxations and total == 0
                else []
            ),
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

    @staticmethod
    def _required_present(request: SearchPeopleRequest) -> bool:
        req = request.required
        return bool(
            req.jobs
            or req.skills
            or req.expertise
            or req.business_domains
            or req.customer_types
            or (req.grade and req.grade.values)
            or (
                req.career
                and (req.career.min_months is not None or req.career.max_months is not None)
            )
            or req.affiliations
            or req.certifications
            or req.project_keywords
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

    def _job_condition_groups(self, roots: list[str]) -> list[JobConditionGroup]:
        groups: list[JobConditionGroup] = []
        for root in roots:
            expanded = self.repo._expand_job_codes([root])
            codes = frozenset(expanded) if expanded else frozenset({root})
            groups.append(JobConditionGroup(root_code=root, codes=codes))
        return groups

    def _build_results(
        self,
        page_slice: list[MergedCandidate],
        *,
        eligible_by_id: dict[UUID, EligiblePersonRow],
        request: SearchPeopleRequest,
        project_summaries: dict,
        keyword_hits: list[ChannelHit],
        semantic_hits: list[ChannelHit],
        required_job_groups: list[JobConditionGroup],
        preferred_job_groups: list[JobConditionGroup],
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
        pref_by_root = {g.root_code: g for g in preferred_job_groups}
        for root in request.preferred.jobs:
            group = pref_by_root.get(root)
            expanded_root = list(group.codes) if group else self.repo._expand_job_codes([root])
            preferred_job_root_hits[root] = self.repo._person_ids_matching_job(
                person_ids, expanded_root
            )

        preferred_skill_by_code = self.repo.preferred_skill_hits_by_code(
            person_ids, request.preferred.skills
        )
        preferred_exp_by_code = self.repo.preferred_expertise_hits_by_code(
            person_ids, request.preferred.expertise
        )
        preferred_biz_by_code = self.repo.preferred_biz_hits_by_code(
            person_ids, request.preferred.business_domains
        )
        preferred_cust_by_code = self.repo.preferred_customer_hits_by_code(
            person_ids, request.preferred.customer_types
        )

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

        scaffold_matches: dict[UUID, list[MatchItem]] = {}
        for cand in page_slice:
            scaffold_matches[cand.person_id] = self._build_matches(
                person_id=cand.person_id,
                request=request,
                code_names=code_names,
                preferred_job_root_hits=preferred_job_root_hits,
                preferred_skill_by_code=preferred_skill_by_code,
                preferred_exp_by_code=preferred_exp_by_code,
                preferred_biz_by_code=preferred_biz_by_code,
                preferred_cust_by_code=preferred_cust_by_code,
            )

        channel_hits: dict[UUID, list[ChannelHit]] = {pid: [] for pid in person_ids}
        for hit in [*keyword_hits, *semantic_hits]:
            if hit.person_id in channel_hits:
                channel_hits[hit.person_id].append(hit)

        enriched = self.enricher.enrich(
            person_ids=person_ids,
            request=request,
            scaffold_matches=scaffold_matches,
            project_summaries=project_summaries,
            channel_hits=channel_hits,
            required_job_groups=required_job_groups,
            preferred_job_groups=preferred_job_groups,
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
            enrichment = enriched.get(cand.person_id)
            results.append(
                SearchPersonResult(
                    person_id=cand.person_id,
                    score=cand.score,
                    person=summary,
                    matches=enrichment.matches if enrichment else scaffold_matches[cand.person_id],
                    top_projects=enrichment.top_projects if enrichment else [],
                    evidence=enrichment.evidence if enrichment else [],
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
