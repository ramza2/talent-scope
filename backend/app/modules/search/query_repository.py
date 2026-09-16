"""Read-only SQL for structured hard filters + keyword/vector channels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import Float, Select, and_, case, cast, exists, func, literal, or_, select, text, true, union_all
from sqlalchemy.orm import Session

from app.core.exceptions import SearchInvalidCodeError
from app.db.models.code import CodeMaster
from app.db.models.person import (
    Certification,
    Person,
    PersonExpertise,
    PersonJob,
    PersonProfile,
    PersonSkill,
)
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
from app.modules.search.query_schemas import (
    PreferredConditionBlock,
    SearchConditionBlock,
    SearchPeopleRequest,
)
from app.modules.search import ranking as search_ranking
from app.modules.search.ranking import (
    KEYWORD_TRIGRAM_THRESHOLD,
    SEMANTIC_OBJECT_TYPES,
    ChannelHit,
    semantic_ann_pool_size,
)

CODE_TYPE_JOB = "JOB"
CODE_TYPE_TECH = "TECH"
CODE_TYPE_EXP = "EXP"
CODE_TYPE_BIZ = "BIZ"
CODE_TYPE_CUSTOMER = "CUSTOMER_TYPE"


@dataclass(frozen=True)
class EligiblePersonRow:
    person_id: UUID
    name: str
    technical_grade: str | None
    career_months: int | None
    profile_updated_at: Any
    recent_project_date: date | None


class SearchQueryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------------------------------------------------------------- codes
    def validate_and_expand_codes(self, request: SearchPeopleRequest) -> dict[str, Any]:
        required = request.required
        preferred = request.preferred

        self._assert_codes(required.jobs, CODE_TYPE_JOB, "required.jobs")
        self._assert_codes(required.skills, CODE_TYPE_TECH, "required.skills")
        self._assert_codes(required.expertise, CODE_TYPE_EXP, "required.expertise")
        self._assert_codes(
            required.business_domains, CODE_TYPE_BIZ, "required.business_domains"
        )
        self._assert_codes(
            required.customer_types, CODE_TYPE_CUSTOMER, "required.customer_types"
        )

        self._assert_codes(preferred.jobs, CODE_TYPE_JOB, "preferred.jobs")
        self._assert_codes(preferred.skills, CODE_TYPE_TECH, "preferred.skills")
        self._assert_codes(preferred.expertise, CODE_TYPE_EXP, "preferred.expertise")
        self._assert_codes(
            preferred.business_domains, CODE_TYPE_BIZ, "preferred.business_domains"
        )
        self._assert_codes(
            preferred.customer_types, CODE_TYPE_CUSTOMER, "preferred.customer_types"
        )

        return {
            "required_jobs": self._expand_job_codes(required.jobs),
            "preferred_jobs": self._expand_job_codes(preferred.jobs),
            "required_skills": list(required.skills),
            "preferred_skills": list(preferred.skills),
            "required_expertise": list(required.expertise),
            "preferred_expertise": list(preferred.expertise),
            "required_biz": list(required.business_domains),
            "preferred_biz": list(preferred.business_domains),
            "required_customer": list(required.customer_types),
            "preferred_customer": list(preferred.customer_types),
        }

    def _assert_codes(self, codes: Sequence[str], code_type: str, field: str) -> None:
        if not codes:
            return
        rows = self.db.execute(
            select(CodeMaster.code, CodeMaster.code_type).where(
                CodeMaster.code.in_(list(codes))
            )
        ).all()
        found = {code: ctype for code, ctype in rows}
        missing = [c for c in codes if c not in found]
        wrong = [c for c in codes if c in found and found[c] != code_type]
        if missing or wrong:
            raise SearchInvalidCodeError(
                f"검색 코드가 올바르지 않습니다 ({field})."
            )

    def _expand_job_codes(self, roots: Sequence[str]) -> list[str]:
        if not roots:
            return []
        sql = text(
            """
            WITH RECURSIVE job_tree AS (
                SELECT cm.code,
                       cm.parent_code,
                       ARRAY[cm.code]::varchar[] AS path
                FROM code_master cm
                WHERE cm.code = ANY(:roots)
                  AND cm.code_type = 'JOB'
                UNION ALL
                SELECT child.code,
                       child.parent_code,
                       jt.path || child.code
                FROM code_master child
                INNER JOIN job_tree jt ON child.parent_code = jt.code
                WHERE child.code_type = 'JOB'
                  AND NOT child.code = ANY (jt.path)
            )
            SELECT DISTINCT code FROM job_tree ORDER BY code
            """
        )
        return list(self.db.execute(sql, {"roots": list(roots)}).scalars().all())

    # ---------------------------------------------------------- filters
    def effective_career_months_expr(self) -> Any:
        return func.coalesce(
            PersonProfile.career_confirmed_months,
            PersonProfile.career_calculated_months,
        )

    def _active_person_filter(self) -> Any:
        return and_(Person.status == "ACTIVE", Person.deleted_at.is_(None))

    def _project_alive(self) -> Any:
        return Project.deleted_at.is_(None)

    def _person_has_job(self, job_codes: Sequence[str]) -> Any:
        person_job = exists(
            select(1).where(
                PersonJob.person_id == Person.id,
                PersonJob.is_active.is_(True),
                PersonJob.job_code.in_(list(job_codes)),
            )
        )
        project_job = exists(
            select(1)
            .select_from(Project)
            .join(ProjectJob, ProjectJob.project_id == Project.id)
            .where(
                Project.person_id == Person.id,
                self._project_alive(),
                ProjectJob.job_code.in_(list(job_codes)),
            )
        )
        return or_(person_job, project_job)

    def _person_has_skill_any(self, tech_codes: Sequence[str]) -> Any:
        person_skill = exists(
            select(1).where(
                PersonSkill.person_id == Person.id,
                PersonSkill.tech_code.in_(list(tech_codes)),
            )
        )
        project_skill = exists(
            select(1)
            .select_from(Project)
            .join(ProjectSkill, ProjectSkill.project_id == Project.id)
            .where(
                Project.person_id == Person.id,
                self._project_alive(),
                ProjectSkill.tech_code.in_(list(tech_codes)),
            )
        )
        return or_(person_skill, project_skill)

    def _person_has_skill_all(self, tech_codes: Sequence[str]) -> Any:
        parts = []
        for code in tech_codes:
            person_skill = exists(
                select(1).where(
                    PersonSkill.person_id == Person.id,
                    PersonSkill.tech_code == code,
                )
            )
            project_skill = exists(
                select(1)
                .select_from(Project)
                .join(ProjectSkill, ProjectSkill.project_id == Project.id)
                .where(
                    Project.person_id == Person.id,
                    self._project_alive(),
                    ProjectSkill.tech_code == code,
                )
            )
            parts.append(or_(person_skill, project_skill))
        return and_(*parts) if parts else true()

    def _person_has_expertise(self, exp_codes: Sequence[str]) -> Any:
        person_exp = exists(
            select(1).where(
                PersonExpertise.person_id == Person.id,
                PersonExpertise.exp_code.in_(list(exp_codes)),
            )
        )
        project_exp = exists(
            select(1)
            .select_from(Project)
            .join(ProjectExpertise, ProjectExpertise.project_id == Project.id)
            .where(
                Project.person_id == Person.id,
                self._project_alive(),
                ProjectExpertise.exp_code.in_(list(exp_codes)),
            )
        )
        return or_(person_exp, project_exp)

    def _person_has_biz(self, biz_codes: Sequence[str]) -> Any:
        return exists(
            select(1)
            .select_from(Project)
            .join(
                ProjectBusinessDomain,
                ProjectBusinessDomain.project_id == Project.id,
            )
            .where(
                Project.person_id == Person.id,
                self._project_alive(),
                ProjectBusinessDomain.biz_code.in_(list(biz_codes)),
            )
        )

    def _person_has_customer(self, customer_codes: Sequence[str]) -> Any:
        return exists(
            select(1)
            .select_from(Project)
            .join(
                ProjectCustomerType,
                ProjectCustomerType.project_id == Project.id,
            )
            .where(
                Project.person_id == Person.id,
                self._project_alive(),
                ProjectCustomerType.customer_type_code.in_(list(customer_codes)),
            )
        )

    def _person_has_affiliation(self, affiliations: Sequence[str]) -> Any:
        parts = [
            PersonProfile.affiliation_company.ilike(f"%{aff}%") for aff in affiliations
        ]
        return or_(*parts) if parts else true()

    def _person_has_certification(self, certifications: Sequence[str]) -> Any:
        parts = []
        for token in certifications:
            pattern = f"%{token}%"
            parts.append(
                exists(
                    select(1).where(
                        Certification.person_id == Person.id,
                        or_(
                            Certification.certification_name.ilike(pattern),
                            Certification.issuer.ilike(pattern),
                        ),
                    )
                )
            )
        return or_(*parts) if parts else true()

    def _person_has_project_keywords(self, keywords: Sequence[str]) -> Any:
        parts = []
        for token in keywords:
            pattern = f"%{token}%"
            parts.append(
                exists(
                    select(1).where(
                        Project.person_id == Person.id,
                        self._project_alive(),
                        or_(
                            Project.project_name.ilike(pattern),
                            Project.customer_name.ilike(pattern),
                            Project.responsibilities.ilike(pattern),
                            Project.project_summary.ilike(pattern),
                        ),
                    )
                )
            )
        return or_(*parts) if parts else true()

    def apply_required_filters(
        self,
        stmt: Select[Any],
        *,
        required: SearchConditionBlock,
        expanded: dict[str, Any],
        skill_match_mode: str,
    ) -> Select[Any]:
        jobs = expanded["required_jobs"]
        skills = expanded["required_skills"]
        expertise = expanded["required_expertise"]
        biz = expanded["required_biz"]
        customer = expanded["required_customer"]

        if jobs:
            stmt = stmt.where(self._person_has_job(jobs))
        if skills:
            if skill_match_mode == "ALL":
                stmt = stmt.where(self._person_has_skill_all(skills))
            else:
                stmt = stmt.where(self._person_has_skill_any(skills))
        if expertise:
            stmt = stmt.where(self._person_has_expertise(expertise))
        if biz:
            stmt = stmt.where(self._person_has_biz(biz))
        if customer:
            stmt = stmt.where(self._person_has_customer(customer))

        grade_values = required.grade.values if required.grade else []
        if grade_values:
            stmt = stmt.where(PersonProfile.technical_grade.in_(grade_values))

        career = required.career
        if career is not None and (
            career.min_months is not None or career.max_months is not None
        ):
            months = self.effective_career_months_expr()
            stmt = stmt.where(months.is_not(None))
            if career.min_months is not None:
                stmt = stmt.where(months >= career.min_months)
            if career.max_months is not None:
                stmt = stmt.where(months <= career.max_months)

        if required.affiliations:
            stmt = stmt.where(self._person_has_affiliation(required.affiliations))
        if required.certifications:
            stmt = stmt.where(self._person_has_certification(required.certifications))
        if required.project_keywords:
            stmt = stmt.where(
                self._person_has_project_keywords(required.project_keywords)
            )
        return stmt

    def eligible_person_ids_subquery(
        self,
        *,
        required: SearchConditionBlock,
        expanded: dict[str, Any],
        skill_match_mode: str,
    ) -> Any:
        stmt = (
            select(Person.id.label("person_id"))
            .join(PersonProfile, PersonProfile.person_id == Person.id)
            .where(self._active_person_filter())
        )
        stmt = self.apply_required_filters(
            stmt,
            required=required,
            expanded=expanded,
            skill_match_mode=skill_match_mode,
        )
        return stmt.subquery()

    def list_eligible_persons(
        self,
        *,
        required: SearchConditionBlock,
        expanded: dict[str, Any],
        skill_match_mode: str,
    ) -> list[EligiblePersonRow]:
        career = self.effective_career_months_expr()
        recent_sq = (
            select(
                Project.person_id.label("person_id"),
                func.max(func.coalesce(Project.end_date, Project.start_date)).label(
                    "recent_project_date"
                ),
            )
            .where(self._project_alive())
            .group_by(Project.person_id)
        ).subquery("eligible_recent_project")
        stmt = (
            select(
                Person.id,
                PersonProfile.name,
                PersonProfile.technical_grade,
                career,
                PersonProfile.profile_updated_at,
                recent_sq.c.recent_project_date,
            )
            .join(PersonProfile, PersonProfile.person_id == Person.id)
            .outerjoin(recent_sq, recent_sq.c.person_id == Person.id)
            .where(self._active_person_filter())
        )
        stmt = self.apply_required_filters(
            stmt,
            required=required,
            expanded=expanded,
            skill_match_mode=skill_match_mode,
        )
        rows = self.db.execute(stmt).all()
        return [
            EligiblePersonRow(
                person_id=row[0],
                name=row[1],
                technical_grade=row[2],
                career_months=row[3],
                profile_updated_at=row[4],
                recent_project_date=row[5],
            )
            for row in rows
        ]

    def list_eligible_person_ids(
        self,
        *,
        required: SearchConditionBlock,
        expanded: dict[str, Any],
        skill_match_mode: str,
    ) -> list[UUID]:
        """Lightweight eligible IDs (no profile/project metadata)."""
        stmt = select(Person.id).where(self._active_person_filter())
        stmt = self.apply_required_filters(
            stmt,
            required=required,
            expanded=expanded,
            skill_match_mode=skill_match_mode,
        )
        return list(self.db.execute(stmt).scalars().all())

    def load_eligible_persons_by_ids(
        self, person_ids: Sequence[UUID]
    ) -> dict[UUID, EligiblePersonRow]:
        """Batch-load eligible metadata for a candidate ID set only."""
        if not person_ids:
            return {}
        career = self.effective_career_months_expr()
        recent_sq = (
            select(
                Project.person_id.label("person_id"),
                func.max(func.coalesce(Project.end_date, Project.start_date)).label(
                    "recent_project_date"
                ),
            )
            .where(
                self._project_alive(),
                Project.person_id.in_(list(person_ids)),
            )
            .group_by(Project.person_id)
        ).subquery("candidate_recent_project")
        stmt = (
            select(
                Person.id,
                PersonProfile.name,
                PersonProfile.technical_grade,
                career,
                PersonProfile.profile_updated_at,
                recent_sq.c.recent_project_date,
            )
            .join(PersonProfile, PersonProfile.person_id == Person.id)
            .outerjoin(recent_sq, recent_sq.c.person_id == Person.id)
            .where(
                self._active_person_filter(),
                Person.id.in_(list(person_ids)),
            )
        )
        rows = self.db.execute(stmt).all()
        return {
            row[0]: EligiblePersonRow(
                person_id=row[0],
                name=row[1],
                technical_grade=row[2],
                career_months=row[3],
                profile_updated_at=row[4],
                recent_project_date=row[5],
            )
            for row in rows
        }

    # ------------------------------------------------ preferred ratios
    def preferred_match_ratio(
        self,
        person_ids: Sequence[UUID],
        *,
        preferred: PreferredConditionBlock,
        expanded_preferred_jobs: Sequence[str],
    ) -> dict[UUID, float]:
        if not person_ids:
            return {}

        dimensions: list[str] = []
        if preferred.jobs:
            dimensions.append("jobs")
        if preferred.skills:
            dimensions.append("skills")
        if preferred.expertise:
            dimensions.append("expertise")
        if preferred.business_domains:
            dimensions.append("business_domains")
        if preferred.customer_types:
            dimensions.append("customer_types")
        if not dimensions:
            return {pid: 0.0 for pid in person_ids}

        scores: dict[UUID, int] = {pid: 0 for pid in person_ids}
        total = len(dimensions)

        if preferred.jobs and expanded_preferred_jobs:
            for pid in self._person_ids_matching_job(
                person_ids, expanded_preferred_jobs
            ):
                scores[pid] += 1
        if preferred.skills:
            for pid in self._person_ids_matching_skill_any(
                person_ids, preferred.skills
            ):
                scores[pid] += 1
        if preferred.expertise:
            for pid in self._person_ids_matching_expertise(
                person_ids, preferred.expertise
            ):
                scores[pid] += 1
        if preferred.business_domains:
            for pid in self._person_ids_matching_biz(
                person_ids, preferred.business_domains
            ):
                scores[pid] += 1
        if preferred.customer_types:
            for pid in self._person_ids_matching_customer(
                person_ids, preferred.customer_types
            ):
                scores[pid] += 1

        return {pid: scores[pid] / total for pid in person_ids}

    def _person_ids_matching_job(
        self, person_ids: Sequence[UUID], job_codes: Sequence[str]
    ) -> set[UUID]:
        pj = set(
            self.db.execute(
                select(PersonJob.person_id).where(
                    PersonJob.person_id.in_(list(person_ids)),
                    PersonJob.is_active.is_(True),
                    PersonJob.job_code.in_(list(job_codes)),
                )
            ).scalars()
        )
        pr = set(
            self.db.execute(
                select(Project.person_id)
                .join(ProjectJob, ProjectJob.project_id == Project.id)
                .where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                    ProjectJob.job_code.in_(list(job_codes)),
                )
            ).scalars()
        )
        return pj | pr

    def _person_ids_matching_skill_any(
        self, person_ids: Sequence[UUID], tech_codes: Sequence[str]
    ) -> set[UUID]:
        ps = set(
            self.db.execute(
                select(PersonSkill.person_id).where(
                    PersonSkill.person_id.in_(list(person_ids)),
                    PersonSkill.tech_code.in_(list(tech_codes)),
                )
            ).scalars()
        )
        pr = set(
            self.db.execute(
                select(Project.person_id)
                .join(ProjectSkill, ProjectSkill.project_id == Project.id)
                .where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                    ProjectSkill.tech_code.in_(list(tech_codes)),
                )
            ).scalars()
        )
        return ps | pr

    def _person_ids_matching_expertise(
        self, person_ids: Sequence[UUID], exp_codes: Sequence[str]
    ) -> set[UUID]:
        pe = set(
            self.db.execute(
                select(PersonExpertise.person_id).where(
                    PersonExpertise.person_id.in_(list(person_ids)),
                    PersonExpertise.exp_code.in_(list(exp_codes)),
                )
            ).scalars()
        )
        pr = set(
            self.db.execute(
                select(Project.person_id)
                .join(ProjectExpertise, ProjectExpertise.project_id == Project.id)
                .where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                    ProjectExpertise.exp_code.in_(list(exp_codes)),
                )
            ).scalars()
        )
        return pe | pr

    def _person_ids_matching_biz(
        self, person_ids: Sequence[UUID], biz_codes: Sequence[str]
    ) -> set[UUID]:
        return set(
            self.db.execute(
                select(Project.person_id)
                .join(
                    ProjectBusinessDomain,
                    ProjectBusinessDomain.project_id == Project.id,
                )
                .where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                    ProjectBusinessDomain.biz_code.in_(list(biz_codes)),
                )
            ).scalars()
        )

    def _person_ids_matching_customer(
        self, person_ids: Sequence[UUID], customer_codes: Sequence[str]
    ) -> set[UUID]:
        return set(
            self.db.execute(
                select(Project.person_id)
                .join(
                    ProjectCustomerType,
                    ProjectCustomerType.project_id == Project.id,
                )
                .where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                    ProjectCustomerType.customer_type_code.in_(list(customer_codes)),
                )
            ).scalars()
        )

    # ----------------------------------------------------- keyword channel

    def preferred_skill_hits_by_code(
        self, person_ids: Sequence[UUID], tech_codes: Sequence[str]
    ) -> dict[str, set[UUID]]:
        out: dict[str, set[UUID]] = {c: set() for c in tech_codes}
        if not person_ids or not tech_codes:
            return out
        person_rows = self.db.execute(
            select(PersonSkill.person_id, PersonSkill.tech_code).where(
                PersonSkill.person_id.in_(list(person_ids)),
                PersonSkill.tech_code.in_(list(tech_codes)),
            )
        ).all()
        for pid, code in person_rows:
            out.setdefault(code, set()).add(pid)
        project_rows = self.db.execute(
            select(Project.person_id, ProjectSkill.tech_code)
            .join(ProjectSkill, ProjectSkill.project_id == Project.id)
            .where(
                Project.person_id.in_(list(person_ids)),
                Project.deleted_at.is_(None),
                ProjectSkill.tech_code.in_(list(tech_codes)),
            )
        ).all()
        for pid, code in project_rows:
            out.setdefault(code, set()).add(pid)
        return out

    def preferred_expertise_hits_by_code(
        self, person_ids: Sequence[UUID], exp_codes: Sequence[str]
    ) -> dict[str, set[UUID]]:
        out: dict[str, set[UUID]] = {c: set() for c in exp_codes}
        if not person_ids or not exp_codes:
            return out
        person_rows = self.db.execute(
            select(PersonExpertise.person_id, PersonExpertise.exp_code).where(
                PersonExpertise.person_id.in_(list(person_ids)),
                PersonExpertise.exp_code.in_(list(exp_codes)),
            )
        ).all()
        for pid, code in person_rows:
            out.setdefault(code, set()).add(pid)
        project_rows = self.db.execute(
            select(Project.person_id, ProjectExpertise.exp_code)
            .join(ProjectExpertise, ProjectExpertise.project_id == Project.id)
            .where(
                Project.person_id.in_(list(person_ids)),
                Project.deleted_at.is_(None),
                ProjectExpertise.exp_code.in_(list(exp_codes)),
            )
        ).all()
        for pid, code in project_rows:
            out.setdefault(code, set()).add(pid)
        return out

    def preferred_biz_hits_by_code(
        self, person_ids: Sequence[UUID], biz_codes: Sequence[str]
    ) -> dict[str, set[UUID]]:
        out: dict[str, set[UUID]] = {c: set() for c in biz_codes}
        if not person_ids or not biz_codes:
            return out
        rows = self.db.execute(
            select(Project.person_id, ProjectBusinessDomain.biz_code)
            .join(ProjectBusinessDomain, ProjectBusinessDomain.project_id == Project.id)
            .where(
                Project.person_id.in_(list(person_ids)),
                Project.deleted_at.is_(None),
                ProjectBusinessDomain.biz_code.in_(list(biz_codes)),
            )
        ).all()
        for pid, code in rows:
            out.setdefault(code, set()).add(pid)
        return out

    def preferred_customer_hits_by_code(
        self, person_ids: Sequence[UUID], customer_codes: Sequence[str]
    ) -> dict[str, set[UUID]]:
        out: dict[str, set[UUID]] = {c: set() for c in customer_codes}
        if not person_ids or not customer_codes:
            return out
        rows = self.db.execute(
            select(Project.person_id, ProjectCustomerType.customer_type_code)
            .join(ProjectCustomerType, ProjectCustomerType.project_id == Project.id)
            .where(
                Project.person_id.in_(list(person_ids)),
                Project.deleted_at.is_(None),
                ProjectCustomerType.customer_type_code.in_(list(customer_codes)),
            )
        ).all()
        for pid, code in rows:
            out.setdefault(code, set()).add(pid)
        return out

    def keyword_channel_hits(
        self,
        *,
        keyword: str,
        eligible_subq: Any,
        limit: int,
    ) -> tuple[list[ChannelHit], bool]:
        """Person-level keyword hits: best item per person, then LIMIT persons.

        Ordering (item → person best):
        1) substring/exact indicator
        2) FTS rank
        3) trigram similarity
        4) source_weight
        5) item id

        Note: on mid-size corpora (~30k index rows) a single OR predicate with
        person-best window outperformed a 2-stage UNION prefilter in EXPLAIN/
        benchmark timings, so we keep the simpler shape. GIN usage is
        dataset/plan dependent; do not assert Index Scan in unit tests.
        """
        if limit <= 0:
            return [], False

        tsquery = func.websearch_to_tsquery("simple", keyword)
        fts_match = SearchIndexItem.search_tsv.op("@@")(tsquery)
        ilike_match = SearchIndexItem.search_text.ilike(f"%{keyword}%")
        trigram_sim = func.similarity(SearchIndexItem.search_text, keyword)
        trigram_match = trigram_sim >= KEYWORD_TRIGRAM_THRESHOLD

        exact_flag = case((ilike_match, 1), else_=0)
        fts_rank = case(
            (fts_match, func.ts_rank_cd(SearchIndexItem.search_tsv, tsquery)),
            else_=0.0,
        )

        item_order = (
            exact_flag.desc(),
            fts_rank.desc(),
            trigram_sim.desc(),
            SearchIndexItem.source_weight.desc(),
            SearchIndexItem.id.asc(),
        )
        ranked = (
            select(
                SearchIndexItem.id.label("id"),
                SearchIndexItem.person_id.label("person_id"),
                SearchIndexItem.object_type.label("object_type"),
                SearchIndexItem.object_id.label("object_id"),
                exact_flag.label("exact_flag"),
                fts_rank.label("fts_rank"),
                trigram_sim.label("trigram_sim"),
                SearchIndexItem.source_weight.label("source_weight"),
                func.row_number()
                .over(partition_by=SearchIndexItem.person_id, order_by=item_order)
                .label("rn"),
            )
            .where(
                SearchIndexItem.is_active.is_(True),
                SearchIndexItem.person_id.in_(select(eligible_subq.c.person_id)),
                or_(fts_match, ilike_match, trigram_match),
            )
        ).subquery("keyword_ranked_items")

        stmt = (
            select(
                ranked.c.id,
                ranked.c.person_id,
                ranked.c.object_type,
                ranked.c.object_id,
                ranked.c.exact_flag,
                ranked.c.fts_rank,
                ranked.c.trigram_sim,
                ranked.c.source_weight,
            )
            .where(ranked.c.rn == 1)
            .order_by(
                ranked.c.exact_flag.desc(),
                ranked.c.fts_rank.desc(),
                ranked.c.trigram_sim.desc(),
                ranked.c.source_weight.desc(),
                ranked.c.id.asc(),
            )
            .limit(limit + 1)
        )
        rows = self.db.execute(stmt).all()
        truncated = len(rows) > limit
        rows = rows[:limit]

        hits: list[ChannelHit] = []
        for rank, row in enumerate(rows, start=1):
            hits.append(
                ChannelHit(
                    person_id=row.person_id,
                    rank=rank,
                    item_id=row.id,
                    object_type=row.object_type,
                    object_id=row.object_id,
                    raw_score=float(row.fts_rank or 0.0),
                    source_weight=float(row.source_weight or 1.0),
                )
            )
        return hits, truncated

    def semantic_channel_hits(
        self,
        *,
        query_vector: list[float],
        eligible_subq: Any,
        limit: int,
        force_exact: bool = False,
    ) -> tuple[list[ChannelHit], bool]:
        """Person-level semantic hits with an exact-first production policy.

        Correctness invariants preserved:
        - hard-filter eligible persons only
        - current embedding_model / embedding_version only
        - score = cosine_similarity * source_weight
        - person-best before channel LIMIT (no DOCUMENT_CHUNK crowd-out)
        - deterministic id ASC tie-break

        Path selection:
        - force_exact=True: return exact immediately (no eligible COUNT)
        - eligible_count <= SEMANTIC_EXACT_ELIGIBLE_THRESHOLD: exact
        - else: typed-pool ANN

        The production threshold is intentionally set high until a measured
        large-scale crossover proves ANN is faster with acceptable recall.
        Tests/PERF can still force ANN by monkeypatching the threshold to 0.
        """
        if limit <= 0:
            return [], False

        if force_exact:
            return self._semantic_channel_hits_exact(
                query_vector=query_vector,
                eligible_subq=eligible_subq,
                limit=limit,
            )

        eligible_count = int(
            self.db.execute(
                select(func.count()).select_from(eligible_subq)
            ).scalar_one()
            or 0
        )
        threshold = search_ranking.SEMANTIC_EXACT_ELIGIBLE_THRESHOLD
        if eligible_count <= threshold:
            return self._semantic_channel_hits_exact(
                query_vector=query_vector,
                eligible_subq=eligible_subq,
                limit=limit,
            )
        return self._semantic_channel_hits_ann(
            query_vector=query_vector,
            eligible_subq=eligible_subq,
            limit=limit,
        )

    def semantic_channel_hits_exact(
        self,
        *,
        query_vector: list[float],
        eligible_subq: Any,
        limit: int,
    ) -> tuple[list[ChannelHit], bool]:
        """Public exact person-best semantic channel (benchmark recall reference)."""
        return self._semantic_channel_hits_exact(
            query_vector=query_vector,
            eligible_subq=eligible_subq,
            limit=limit,
        )

    def _semantic_channel_hits_exact(
        self,
        *,
        query_vector: list[float],
        eligible_subq: Any,
        limit: int,
    ) -> tuple[list[ChannelHit], bool]:
        model = current_embedding_model()
        version = effective_embedding_version()
        distance = SearchIndexItem.embedding.cosine_distance(query_vector)
        similarity = func.greatest(literal(0.0), literal(1.0) - distance)
        effective_score = similarity * cast(SearchIndexItem.source_weight, Float)

        item_order = (effective_score.desc(), SearchIndexItem.id.asc())
        ranked = (
            select(
                SearchIndexItem.id.label("id"),
                SearchIndexItem.person_id.label("person_id"),
                SearchIndexItem.object_type.label("object_type"),
                SearchIndexItem.object_id.label("object_id"),
                distance.label("distance"),
                effective_score.label("effective_score"),
                SearchIndexItem.source_weight.label("source_weight"),
                func.row_number()
                .over(partition_by=SearchIndexItem.person_id, order_by=item_order)
                .label("rn"),
            )
            .where(
                SearchIndexItem.is_active.is_(True),
                SearchIndexItem.embedding.is_not(None),
                SearchIndexItem.embedding_model == model,
                SearchIndexItem.embedding_version == version,
                SearchIndexItem.person_id.in_(select(eligible_subq.c.person_id)),
            )
        ).subquery("semantic_ranked_items")

        stmt = (
            select(
                ranked.c.id,
                ranked.c.person_id,
                ranked.c.object_type,
                ranked.c.object_id,
                ranked.c.distance,
                ranked.c.effective_score,
                ranked.c.source_weight,
            )
            .where(ranked.c.rn == 1)
            .order_by(ranked.c.effective_score.desc(), ranked.c.id.asc())
            .limit(limit + 1)
        )
        rows = self.db.execute(stmt).all()
        truncated = len(rows) > limit
        rows = rows[:limit]
        return self._rows_to_semantic_hits(rows), truncated

    def _semantic_channel_hits_ann(
        self,
        *,
        query_vector: list[float],
        eligible_subq: Any,
        limit: int,
    ) -> tuple[list[ChannelHit], bool]:
        model = current_embedding_model()
        version = effective_embedding_version()
        distance = SearchIndexItem.embedding.cosine_distance(query_vector)
        pool_size = semantic_ann_pool_size(person_limit=limit)

        # HNSW-friendly candidate pools. Eligibility remains a hard-filter SoT
        # and is applied before person-best ranking, after the bounded KNN pool.
        pool_stmts = []
        for object_type in SEMANTIC_OBJECT_TYPES:
            pool_stmts.append(
                select(
                    SearchIndexItem.id.label("id"),
                    SearchIndexItem.person_id.label("person_id"),
                    SearchIndexItem.object_type.label("object_type"),
                    SearchIndexItem.object_id.label("object_id"),
                    SearchIndexItem.source_weight.label("source_weight"),
                    distance.label("distance"),
                )
                .where(
                    SearchIndexItem.is_active.is_(True),
                    SearchIndexItem.embedding.is_not(None),
                    SearchIndexItem.embedding_model == model,
                    SearchIndexItem.embedding_version == version,
                    SearchIndexItem.object_type == object_type,
                )
                .order_by(distance.asc(), SearchIndexItem.id.asc())
                .limit(pool_size)
            )

        pool = union_all(*pool_stmts).subquery("semantic_ann_pool")
        eligible_pool = (
            select(
                pool.c.id,
                pool.c.person_id,
                pool.c.object_type,
                pool.c.object_id,
                pool.c.source_weight,
                pool.c.distance,
            )
            .where(pool.c.person_id.in_(select(eligible_subq.c.person_id)))
        ).subquery("semantic_ann_eligible_pool")
        similarity = func.greatest(literal(0.0), literal(1.0) - eligible_pool.c.distance)
        effective_score = similarity * cast(eligible_pool.c.source_weight, Float)
        item_order = (effective_score.desc(), eligible_pool.c.id.asc())
        ranked = (
            select(
                eligible_pool.c.id,
                eligible_pool.c.person_id,
                eligible_pool.c.object_type,
                eligible_pool.c.object_id,
                eligible_pool.c.distance,
                effective_score.label("effective_score"),
                eligible_pool.c.source_weight,
                func.row_number()
                .over(partition_by=eligible_pool.c.person_id, order_by=item_order)
                .label("rn"),
            )
        ).subquery("semantic_ann_person_best")

        stmt = (
            select(
                ranked.c.id,
                ranked.c.person_id,
                ranked.c.object_type,
                ranked.c.object_id,
                ranked.c.distance,
                ranked.c.effective_score,
                ranked.c.source_weight,
            )
            .where(ranked.c.rn == 1)
            .order_by(ranked.c.effective_score.desc(), ranked.c.id.asc())
            .limit(limit + 1)
        )
        rows = self.db.execute(stmt).all()
        rows = rows[:limit]
        # Typed ANN pools are bounded, so ANN is always conservative about
        # candidate completeness. This path is benchmark/experimental until a
        # production crossover is measured.
        truncated = True
        return self._rows_to_semantic_hits(rows), truncated

    def _rows_to_semantic_hits(self, rows: Sequence[Any]) -> list[ChannelHit]:
        hits: list[ChannelHit] = []
        for rank, row in enumerate(rows, start=1):
            hits.append(
                ChannelHit(
                    person_id=row.person_id,
                    rank=rank,
                    item_id=row.id,
                    object_type=row.object_type,
                    object_id=row.object_id,
                    raw_score=float(row.effective_score or 0.0),
                    source_weight=float(row.source_weight or 1.0),
                )
            )
        return hits

    def load_profiles(self, person_ids: Sequence[UUID]) -> dict[UUID, PersonProfile]:
        if not person_ids:
            return {}
        rows = self.db.execute(
            select(PersonProfile).where(PersonProfile.person_id.in_(list(person_ids)))
        ).scalars()
        return {row.person_id: row for row in rows}

    def load_code_names(self, codes: Sequence[str]) -> dict[str, str]:
        if not codes:
            return {}
        rows = self.db.execute(
            select(CodeMaster.code, CodeMaster.name).where(
                CodeMaster.code.in_(list(codes))
            )
        ).all()
        return {code: name for code, name in rows}
