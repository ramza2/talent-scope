"""Deterministic project relevance / recency for search ranking (read-only)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import Float, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from app.db.models.project import (
    Project,
    ProjectBusinessDomain,
    ProjectCustomerType,
    ProjectExpertise,
    ProjectJob,
    ProjectSkill,
)
from app.db.models.search import SearchIndexItem
from app.modules.search.embedding_policy import (
    current_embedding_model,
    effective_embedding_version,
)
from app.modules.search.query_schemas import SearchPeopleRequest
from app.modules.search.ranking import (
    EXPERTISE_EXPLICIT_FACTOR,
    EXPERTISE_INFERRED_FACTOR,
    KEYWORD_TRIGRAM_THRESHOLD,
    PROJECT_BEST_WEIGHT,
    PROJECT_COUNT_CAP,
    PROJECT_COUNT_WEIGHT,
    PROJECT_DURATION_MONTHS_CAP,
    PROJECT_DURATION_WEIGHT,
    PROJECT_WEIGHT_KEYWORD,
    PROJECT_WEIGHT_SEMANTIC,
    PROJECT_WEIGHT_STRUCTURED,
    TOP_PROJECTS_LIMIT,
    clamp01,
    normalize_weighted_parts,
)

REQUIRED_CONDITION_WEIGHT = 1.0
PREFERRED_CONDITION_WEIGHT = 0.5


@dataclass(frozen=True)
class JobConditionGroup:
    """One requested JOB root and its expanded descendant code set."""

    root_code: str
    codes: frozenset[str]

    def matches(self, project_job_codes: set[str]) -> bool:
        return bool(project_job_codes.intersection(self.codes))


@dataclass(frozen=True)
class ProjectQuerySignals:
    """Project ranking signals with required / preferred kept separate."""

    required_job_groups: tuple[JobConditionGroup, ...] = ()
    preferred_job_groups: tuple[JobConditionGroup, ...] = ()
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    required_expertise: tuple[str, ...] = ()
    preferred_expertise: tuple[str, ...] = ()
    required_business_domains: tuple[str, ...] = ()
    preferred_business_domains: tuple[str, ...] = ()
    required_customer_types: tuple[str, ...] = ()
    preferred_customer_types: tuple[str, ...] = ()
    project_keywords: tuple[str, ...] = ()
    keyword_query: str | None = None
    semantic_query: str | None = None

    @property
    def has_structured(self) -> bool:
        return bool(
            self.required_job_groups
            or self.preferred_job_groups
            or self.required_skills
            or self.preferred_skills
            or self.required_expertise
            or self.preferred_expertise
            or self.required_business_domains
            or self.preferred_business_domains
            or self.required_customer_types
            or self.preferred_customer_types
            or self.project_keywords
        )

    @property
    def active(self) -> bool:
        return bool(self.has_structured or self.keyword_query or self.semantic_query)


@dataclass
class ProjectScoreDetail:
    project_id: UUID
    person_id: UUID
    project_name: str
    start_date: date | None
    end_date: date | None
    duration_months: int | None
    score: float
    related: bool
    role_codes: list[str] = field(default_factory=list)


@dataclass
class PersonProjectSummary:
    person_id: UUID
    project_relevance: float | None
    recency_score: float | None
    related_count: int = 0
    related_duration_months: int = 0
    latest_related_date: date | None = None
    top_project_ids: list[UUID] = field(default_factory=list)
    project_scores: dict[UUID, float] = field(default_factory=dict)


def _job_groups_from_roots(
    roots: Sequence[str],
    *,
    expand_root,
) -> tuple[JobConditionGroup, ...]:
    groups: list[JobConditionGroup] = []
    for root in roots:
        expanded = list(expand_root(root))
        codes = frozenset(expanded) if expanded else frozenset({root})
        groups.append(JobConditionGroup(root_code=root, codes=codes))
    return tuple(groups)


def build_project_query_signals(
    request: SearchPeopleRequest,
    *,
    required_job_groups: Sequence[JobConditionGroup] | None = None,
    preferred_job_groups: Sequence[JobConditionGroup] | None = None,
    expand_job_root=None,
) -> ProjectQuerySignals:
    """Build separated required/preferred project signals.

    Prefer prebuilt ``*_job_groups``. When omitted, ``expand_job_root(root)``
    must return the descendant code list for that root (including the root).
    """
    req = request.required
    pref = request.preferred
    if required_job_groups is None:
        if expand_job_root is None:
            raise ValueError("required_job_groups or expand_job_root is required")
        required_job_groups = _job_groups_from_roots(req.jobs, expand_root=expand_job_root)
    if preferred_job_groups is None:
        if expand_job_root is None:
            raise ValueError("preferred_job_groups or expand_job_root is required")
        preferred_job_groups = _job_groups_from_roots(pref.jobs, expand_root=expand_job_root)

    return ProjectQuerySignals(
        required_job_groups=tuple(required_job_groups),
        preferred_job_groups=tuple(preferred_job_groups),
        required_skills=tuple(dict.fromkeys(req.skills)),
        preferred_skills=tuple(dict.fromkeys(pref.skills)),
        required_expertise=tuple(dict.fromkeys(req.expertise)),
        preferred_expertise=tuple(dict.fromkeys(pref.expertise)),
        required_business_domains=tuple(dict.fromkeys(req.business_domains)),
        preferred_business_domains=tuple(dict.fromkeys(pref.business_domains)),
        required_customer_types=tuple(dict.fromkeys(req.customer_types)),
        preferred_customer_types=tuple(dict.fromkeys(pref.customer_types)),
        project_keywords=tuple(req.project_keywords),
        keyword_query=request.keyword_query,
        semantic_query=request.semantic_query,
    )


def _expertise_factor(evidence_type: str | None) -> float:
    if (evidence_type or "EXPLICIT").upper() == "INFERRED":
        return EXPERTISE_INFERRED_FACTOR
    return EXPERTISE_EXPLICIT_FACTOR


def _code_set_ratio(have: set[str], requested: Sequence[str]) -> float:
    if not requested:
        return 0.0
    matched = len(have.intersection(requested))
    return matched / len(requested)


def _job_group_ratio(groups: Sequence[JobConditionGroup], job_codes: set[str]) -> float:
    if not groups:
        return 0.0
    hits = sum(1 for group in groups if group.matches(job_codes))
    return hits / len(groups)


def _expertise_ratio(
    requested: Sequence[str],
    expertise_rows: list[tuple[str, str]],
) -> float:
    if not requested:
        return 0.0
    wanted = set(requested)
    best_by_code: dict[str, float] = {}
    for code, evidence_type in expertise_rows:
        if code not in wanted:
            continue
        best_by_code[code] = max(
            best_by_code.get(code, 0.0), _expertise_factor(evidence_type)
        )
    return sum(best_by_code.get(code, 0.0) for code in requested) / len(requested)


def _keyword_blob_ratio(
    keywords: Sequence[str],
    *,
    project_name: str,
    customer_name: str | None,
    responsibilities: str | None,
    project_summary: str | None,
) -> float:
    if not keywords:
        return 0.0
    blob = " ".join(
        [
            project_name or "",
            customer_name or "",
            responsibilities or "",
            project_summary or "",
        ]
    ).casefold()
    hits = sum(1 for kw in keywords if kw.casefold() in blob)
    return hits / len(keywords)


def compute_project_structured_score(
    *,
    signals: ProjectQuerySignals,
    job_codes: set[str],
    skill_codes: set[str],
    expertise_rows: list[tuple[str, str]],
    biz_codes: set[str],
    customer_codes: set[str],
    project_name: str,
    customer_name: str | None,
    responsibilities: str | None,
    project_summary: str | None,
) -> float | None:
    """Absolute required/preferred quality factors, then unweighted mean.

    Priority is applied to each active category's match ratio *before* averaging
    so preferred-only perfect match stays 0.5 (not renormalized back to 1.0).

    Examples:
    - required-only perfect → 1.0
    - preferred-only perfect → 0.5
    - required + preferred both perfect → (1.0 + 0.5) / 2 = 0.75
    - preferred EXP INFERRED → 0.5 * 0.70
    """
    if not signals.has_structured:
        return None

    # Absolute qualities (priority already folded in); mean — not weight-normalize.
    parts: list[float] = []

    if signals.required_job_groups:
        parts.append(
            REQUIRED_CONDITION_WEIGHT
            * clamp01(_job_group_ratio(signals.required_job_groups, job_codes))
        )
    if signals.preferred_job_groups:
        parts.append(
            PREFERRED_CONDITION_WEIGHT
            * clamp01(_job_group_ratio(signals.preferred_job_groups, job_codes))
        )

    if signals.required_skills:
        parts.append(
            REQUIRED_CONDITION_WEIGHT
            * clamp01(_code_set_ratio(skill_codes, signals.required_skills))
        )
    if signals.preferred_skills:
        parts.append(
            PREFERRED_CONDITION_WEIGHT
            * clamp01(_code_set_ratio(skill_codes, signals.preferred_skills))
        )

    if signals.required_expertise:
        parts.append(
            REQUIRED_CONDITION_WEIGHT
            * clamp01(_expertise_ratio(signals.required_expertise, expertise_rows))
        )
    if signals.preferred_expertise:
        parts.append(
            PREFERRED_CONDITION_WEIGHT
            * clamp01(_expertise_ratio(signals.preferred_expertise, expertise_rows))
        )

    if signals.required_business_domains:
        parts.append(
            REQUIRED_CONDITION_WEIGHT
            * clamp01(_code_set_ratio(biz_codes, signals.required_business_domains))
        )
    if signals.preferred_business_domains:
        parts.append(
            PREFERRED_CONDITION_WEIGHT
            * clamp01(_code_set_ratio(biz_codes, signals.preferred_business_domains))
        )

    if signals.required_customer_types:
        parts.append(
            REQUIRED_CONDITION_WEIGHT
            * clamp01(
                _code_set_ratio(customer_codes, signals.required_customer_types)
            )
        )
    if signals.preferred_customer_types:
        parts.append(
            PREFERRED_CONDITION_WEIGHT
            * clamp01(
                _code_set_ratio(customer_codes, signals.preferred_customer_types)
            )
        )

    if signals.project_keywords:
        parts.append(
            REQUIRED_CONDITION_WEIGHT
            * clamp01(
                _keyword_blob_ratio(
                    signals.project_keywords,
                    project_name=project_name,
                    customer_name=customer_name,
                    responsibilities=responsibilities,
                    project_summary=project_summary,
                )
            )
        )

    if not parts:
        return None
    return sum(parts) / len(parts)


def project_recent_sort_key(project: Project) -> tuple:
    """Context top-project fallback: COALESCE(end_date, start_date) DESC, id ASC."""
    recent = project.end_date or project.start_date or date.min
    return (-recent.toordinal(), str(project.id))


def compute_project_base_score(
    *,
    structured: float | None,
    keyword: float | None,
    semantic: float | None,
) -> float:
    parts: list[tuple[float, float]] = []
    if structured is not None:
        parts.append((PROJECT_WEIGHT_STRUCTURED, structured))
    if keyword is not None:
        parts.append((PROJECT_WEIGHT_KEYWORD, keyword))
    if semantic is not None:
        parts.append((PROJECT_WEIGHT_SEMANTIC, semantic))
    if not parts:
        return 0.0
    return normalize_weighted_parts(parts)


def compute_person_project_relevance(
    related_scores: Sequence[float],
    related_duration_months: int,
) -> float:
    if not related_scores:
        return 0.0
    best = max(related_scores)
    count_score = min(len(related_scores), PROJECT_COUNT_CAP) / PROJECT_COUNT_CAP
    duration_score = (
        min(max(int(related_duration_months), 0), PROJECT_DURATION_MONTHS_CAP)
        / PROJECT_DURATION_MONTHS_CAP
    )
    return normalize_weighted_parts(
        [
            (PROJECT_BEST_WEIGHT, best),
            (PROJECT_COUNT_WEIGHT, count_score),
            (PROJECT_DURATION_WEIGHT, duration_score),
        ]
    )


def compute_recency_score(latest_related_date: date | None, *, as_of: date) -> float:
    """Score for a person who has at least one related project.

    ``latest_related_date is None`` means related projects exist but dates are
    unknown → 0.60. Callers must not invoke this when related_count == 0.
    """
    if latest_related_date is None:
        return 0.60
    if latest_related_date > as_of:
        return 1.0
    years = (as_of - latest_related_date).days / 365.25
    if years <= 2:
        return 1.0
    if years <= 5:
        return 0.85
    if years <= 10:
        return 0.70
    return 0.55


def resolve_project_duration_months(
    *,
    duration_months: int | None,
    start_date: date | None,
    end_date: date | None,
    as_of: date,
) -> int:
    if duration_months is not None and duration_months >= 0:
        return int(duration_months)
    if start_date is None:
        return 0
    end = end_date or as_of
    if end < start_date:
        return 0
    months = (end.year - start_date.year) * 12 + (end.month - start_date.month)
    if end.day < start_date.day:
        months -= 1
    return max(months, 0)


def format_project_period(start_date: date | None, end_date: date | None) -> str | None:
    if start_date is None and end_date is None:
        return None
    if start_date is not None and end_date is not None:
        return (
            f"{start_date.year:04d}.{start_date.month:02d}"
            f" ~ {end_date.year:04d}.{end_date.month:02d}"
        )
    if start_date is not None:
        return f"{start_date.year:04d}.{start_date.month:02d} ~ 현재"
    assert end_date is not None
    return f"~ {end_date.year:04d}.{end_date.month:02d}"


class ProjectRankingRepository:
    """Batch project relevance for candidate persons (read-only)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def summarize_persons(
        self,
        person_ids: Sequence[UUID],
        *,
        request: SearchPeopleRequest,
        signals: ProjectQuerySignals,
        query_vector: list[float] | None,
        as_of: date | None = None,
    ) -> dict[UUID, PersonProjectSummary]:
        del request  # signals already carry required/preferred separation
        as_of_date = as_of or date.today()
        if not person_ids:
            return {}
        if not signals.active:
            return {
                pid: PersonProjectSummary(
                    person_id=pid,
                    project_relevance=None,
                    recency_score=None,
                )
                for pid in person_ids
            }

        projects = self._load_projects(person_ids)
        empty = {
            pid: PersonProjectSummary(
                person_id=pid,
                project_relevance=0.0,
                recency_score=None,
            )
            for pid in person_ids
        }
        if not projects:
            return empty

        project_ids = [p.id for p in projects]
        jobs = self._load_codes(ProjectJob, ProjectJob.job_code, project_ids)
        skills = self._load_codes(ProjectSkill, ProjectSkill.tech_code, project_ids)
        expertise = self._load_expertise(project_ids)
        biz = self._load_codes(
            ProjectBusinessDomain, ProjectBusinessDomain.biz_code, project_ids
        )
        cust = self._load_codes(
            ProjectCustomerType, ProjectCustomerType.customer_type_code, project_ids
        )
        keyword_scores = self._project_keyword_scores(
            person_ids=person_ids,
            project_ids=project_ids,
            keyword=signals.keyword_query,
        )
        semantic_scores = self._project_semantic_scores(
            person_ids=person_ids,
            project_ids=project_ids,
            query_vector=query_vector if signals.semantic_query else None,
        )

        by_person: dict[UUID, list[ProjectScoreDetail]] = {pid: [] for pid in person_ids}
        for project in projects:
            structured = compute_project_structured_score(
                signals=signals,
                job_codes=jobs.get(project.id, set()),
                skill_codes=skills.get(project.id, set()),
                expertise_rows=expertise.get(project.id, []),
                biz_codes=biz.get(project.id, set()),
                customer_codes=cust.get(project.id, set()),
                project_name=project.project_name,
                customer_name=project.customer_name,
                responsibilities=project.responsibilities,
                project_summary=project.project_summary,
            )
            # Query channel present → component active for every project (miss = 0.0).
            keyword_component = (
                keyword_scores.get(project.id, 0.0) if signals.keyword_query else None
            )
            semantic_component = (
                semantic_scores.get(project.id, 0.0) if signals.semantic_query else None
            )
            if (
                structured is None
                and keyword_component is None
                and semantic_component is None
            ):
                base = 0.0
                related = False
            else:
                base = compute_project_base_score(
                    structured=structured,
                    keyword=keyword_component,
                    semantic=semantic_component,
                )
                related = base > 0.0
            by_person.setdefault(project.person_id, []).append(
                ProjectScoreDetail(
                    project_id=project.id,
                    person_id=project.person_id,
                    project_name=project.project_name,
                    start_date=project.start_date,
                    end_date=project.end_date,
                    duration_months=project.duration_months,
                    score=base,
                    related=related,
                    role_codes=sorted(jobs.get(project.id, set())),
                )
            )

        out: dict[UUID, PersonProjectSummary] = {}
        for pid in person_ids:
            details = by_person.get(pid, [])
            related = [d for d in details if d.related]
            related_scores = [d.score for d in related]
            duration_sum = sum(
                resolve_project_duration_months(
                    duration_months=d.duration_months,
                    start_date=d.start_date,
                    end_date=d.end_date,
                    as_of=as_of_date,
                )
                for d in related
            )
            latest: date | None = None
            for d in related:
                candidate = d.end_date or d.start_date
                if candidate is None:
                    continue
                if latest is None or candidate > latest:
                    latest = candidate

            if related_scores:
                project_relevance = compute_person_project_relevance(
                    related_scores, duration_sum
                )
                # Undated related → 0.60; no related → None (inactive).
                recency = compute_recency_score(latest, as_of=as_of_date)
            else:
                project_relevance = 0.0
                recency = None

            pool = related if related else details
            ranked = sorted(
                pool,
                key=lambda d: (
                    -d.score,
                    -(d.end_date or d.start_date or date.min).toordinal(),
                    str(d.project_id),
                ),
            )
            out[pid] = PersonProjectSummary(
                person_id=pid,
                project_relevance=project_relevance,
                recency_score=recency,
                related_count=len(related),
                related_duration_months=duration_sum,
                latest_related_date=latest,
                top_project_ids=[d.project_id for d in ranked[:TOP_PROJECTS_LIMIT]],
                project_scores={d.project_id: d.score for d in details},
            )
        return out

    def _load_projects(self, person_ids: Sequence[UUID]) -> list[Project]:
        return list(
            self.db.execute(
                select(Project)
                .where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                )
                .order_by(Project.person_id.asc(), Project.id.asc())
            )
            .scalars()
            .all()
        )

    def _load_codes(self, model, code_col, project_ids: Sequence[UUID]) -> dict[UUID, set[str]]:
        if not project_ids:
            return {}
        rows = self.db.execute(
            select(model.project_id, code_col).where(
                model.project_id.in_(list(project_ids))
            )
        ).all()
        out: dict[UUID, set[str]] = {}
        for project_id, code in rows:
            out.setdefault(project_id, set()).add(code)
        return out

    def _load_expertise(
        self, project_ids: Sequence[UUID]
    ) -> dict[UUID, list[tuple[str, str]]]:
        if not project_ids:
            return {}
        rows = self.db.execute(
            select(
                ProjectExpertise.project_id,
                ProjectExpertise.exp_code,
                ProjectExpertise.evidence_type,
            ).where(ProjectExpertise.project_id.in_(list(project_ids)))
        ).all()
        out: dict[UUID, list[tuple[str, str]]] = {}
        for project_id, code, evidence_type in rows:
            out.setdefault(project_id, []).append((code, evidence_type))
        return out

    def _project_keyword_scores(
        self,
        *,
        person_ids: Sequence[UUID],
        project_ids: Sequence[UUID],
        keyword: str | None,
    ) -> dict[UUID, float]:
        if not keyword or not project_ids:
            return {}
        tsquery = func.websearch_to_tsquery("simple", keyword)
        fts_match = SearchIndexItem.search_tsv.op("@@")(tsquery)
        ilike_match = SearchIndexItem.search_text.ilike(f"%{keyword}%")
        trigram_sim = func.similarity(SearchIndexItem.search_text, keyword)
        trigram_match = trigram_sim >= KEYWORD_TRIGRAM_THRESHOLD
        fts_rank = func.coalesce(
            func.ts_rank_cd(SearchIndexItem.search_tsv, tsquery), 0.0
        )
        exact_flag = case((ilike_match, 1.0), else_=0.0)
        score_expr = func.greatest(
            exact_flag,
            cast(fts_rank, Float),
            cast(trigram_sim, Float),
        )
        ranked = (
            select(
                SearchIndexItem.object_id.label("project_id"),
                score_expr.label("score"),
                func.row_number()
                .over(
                    partition_by=SearchIndexItem.object_id,
                    order_by=(score_expr.desc(), SearchIndexItem.id.asc()),
                )
                .label("rn"),
            ).where(
                SearchIndexItem.is_active.is_(True),
                SearchIndexItem.object_type == "PROJECT",
                SearchIndexItem.object_id.in_(list(project_ids)),
                SearchIndexItem.person_id.in_(list(person_ids)),
                or_(fts_match, ilike_match, trigram_match),
            )
        ).subquery()
        rows = self.db.execute(
            select(ranked.c.project_id, ranked.c.score).where(ranked.c.rn == 1)
        ).all()
        return {project_id: clamp01(float(score or 0.0)) for project_id, score in rows}

    def _project_semantic_scores(
        self,
        *,
        person_ids: Sequence[UUID],
        project_ids: Sequence[UUID],
        query_vector: list[float] | None,
    ) -> dict[UUID, float]:
        if not query_vector or not project_ids:
            return {}
        model = current_embedding_model()
        version = effective_embedding_version()
        distance = SearchIndexItem.embedding.cosine_distance(query_vector)
        similarity = func.greatest(literal(0.0), literal(1.0) - distance)
        effective = similarity * cast(SearchIndexItem.source_weight, Float)
        ranked = (
            select(
                SearchIndexItem.object_id.label("project_id"),
                effective.label("score"),
                func.row_number()
                .over(
                    partition_by=SearchIndexItem.object_id,
                    order_by=(effective.desc(), SearchIndexItem.id.asc()),
                )
                .label("rn"),
            ).where(
                SearchIndexItem.is_active.is_(True),
                SearchIndexItem.object_type == "PROJECT",
                SearchIndexItem.object_id.in_(list(project_ids)),
                SearchIndexItem.person_id.in_(list(person_ids)),
                SearchIndexItem.embedding.is_not(None),
                SearchIndexItem.embedding_model == model,
                SearchIndexItem.embedding_version == version,
            )
        ).subquery()
        rows = self.db.execute(
            select(ranked.c.project_id, ranked.c.score).where(ranked.c.rn == 1)
        ).all()
        return {project_id: clamp01(float(score or 0.0)) for project_id, score in rows}
