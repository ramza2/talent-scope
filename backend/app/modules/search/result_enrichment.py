"""Page-level search enrichment: evidence counts, top projects, evidence items."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.code import CodeMaster
from app.db.models.person import (
    Certification,
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
from app.modules.evidence.materializer import (
    RELATION_TYPE_SUPPORTS,
    project_relation_field_name,
)
from app.modules.search.project_ranking import (
    JobConditionGroup,
    PersonProjectSummary,
    format_project_period,
)
from app.modules.search.query_schemas import (
    EvidenceItem,
    MatchItem,
    SearchPeopleRequest,
    TopProjectItem,
)
from app.modules.search.ranking import (
    SEARCH_EVIDENCE_LIMIT,
    TOP_PROJECTS_LIMIT,
    ChannelHit,
)
from app.modules.search.search_evidence_repository import (
    EvidenceRow,
    EvidenceTargetKey,
    SearchEvidenceRepository,
    clip_snippet,
)

SOURCE_CONFIRMED_PROFILE = "CONFIRMED_PROFILE"
SOURCE_CONFIRMED_PROJECT = "CONFIRMED_PROJECT"
SOURCE_DOCUMENT_CHUNK = "DOCUMENT_CHUNK"

PROJECT_KEYWORD_FIELDS = (
    "project_name",
    "customer_name",
    "responsibilities",
    "project_summary",
)

TOP_PROJECT_EVIDENCE_CAP = 5


@dataclass(frozen=True)
class MatchEvidenceSpec:
    """Internal match → evidence target derivation (not API-exposed)."""

    category: str
    match_type: str  # REQUIRED | PREFERRED
    requested: tuple[str, ...] = ()
    job_groups: tuple[frozenset[str], ...] = ()
    skill_mode: str | None = None  # ANY | ALL for skills


@dataclass
class PersonEnrichment:
    matches: list[MatchItem]
    top_projects: list[TopProjectItem]
    evidence: list[EvidenceItem]


def build_match_evidence_specs(
    request: SearchPeopleRequest,
    *,
    required_job_groups: Sequence[JobConditionGroup],
    preferred_job_groups: Sequence[JobConditionGroup],
) -> list[MatchEvidenceSpec]:
    """Mirror ``SearchQueryService._build_matches`` order without parsing labels."""
    specs: list[MatchEvidenceSpec] = []
    req = request.required
    pref = request.preferred

    if req.jobs:
        specs.append(
            MatchEvidenceSpec(
                category="jobs",
                match_type="REQUIRED",
                requested=tuple(req.jobs),
                job_groups=tuple(g.codes for g in required_job_groups),
            )
        )
    if request.skill_match_mode == "ALL":
        for code in req.skills:
            specs.append(
                MatchEvidenceSpec(
                    category="skills",
                    match_type="REQUIRED",
                    requested=(code,),
                    skill_mode="ALL",
                )
            )
    elif req.skills:
        specs.append(
            MatchEvidenceSpec(
                category="skills",
                match_type="REQUIRED",
                requested=tuple(req.skills),
                skill_mode="ANY",
            )
        )
    if req.expertise:
        specs.append(
            MatchEvidenceSpec(
                category="expertise",
                match_type="REQUIRED",
                requested=tuple(req.expertise),
            )
        )
    if req.business_domains:
        specs.append(
            MatchEvidenceSpec(
                category="business_domains",
                match_type="REQUIRED",
                requested=tuple(req.business_domains),
            )
        )
    if req.customer_types:
        specs.append(
            MatchEvidenceSpec(
                category="customer_types",
                match_type="REQUIRED",
                requested=tuple(req.customer_types),
            )
        )
    if req.grade and req.grade.values:
        specs.append(
            MatchEvidenceSpec(
                category="grade",
                match_type="REQUIRED",
                requested=tuple(req.grade.values),
            )
        )
    if req.career and (
        req.career.min_months is not None or req.career.max_months is not None
    ):
        specs.append(MatchEvidenceSpec(category="career", match_type="REQUIRED"))
    if req.affiliations:
        specs.append(
            MatchEvidenceSpec(
                category="affiliations",
                match_type="REQUIRED",
                requested=tuple(req.affiliations),
            )
        )
    if req.certifications:
        specs.append(
            MatchEvidenceSpec(
                category="certifications",
                match_type="REQUIRED",
                requested=tuple(req.certifications),
            )
        )
    if req.project_keywords:
        specs.append(
            MatchEvidenceSpec(
                category="project_keywords",
                match_type="REQUIRED",
                requested=tuple(req.project_keywords),
            )
        )

    pref_groups_by_root = {g.root_code: g.codes for g in preferred_job_groups}
    for root in pref.jobs:
        specs.append(
            MatchEvidenceSpec(
                category="jobs",
                match_type="PREFERRED",
                requested=(root,),
                job_groups=(pref_groups_by_root.get(root, frozenset({root})),),
            )
        )
    for code in pref.skills:
        specs.append(
            MatchEvidenceSpec(
                category="skills",
                match_type="PREFERRED",
                requested=(code,),
                skill_mode="ANY",
            )
        )
    for code in pref.expertise:
        specs.append(
            MatchEvidenceSpec(
                category="expertise",
                match_type="PREFERRED",
                requested=(code,),
            )
        )
    for code in pref.business_domains:
        specs.append(
            MatchEvidenceSpec(
                category="business_domains",
                match_type="PREFERRED",
                requested=(code,),
            )
        )
    for code in pref.customer_types:
        specs.append(
            MatchEvidenceSpec(
                category="customer_types",
                match_type="PREFERRED",
                requested=(code,),
            )
        )
    return specs


class SearchResultEnricher:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.evidence_repo = SearchEvidenceRepository(db)

    def enrich(
        self,
        *,
        person_ids: Sequence[UUID],
        request: SearchPeopleRequest,
        scaffold_matches: dict[UUID, list[MatchItem]],
        project_summaries: dict[UUID, PersonProjectSummary],
        channel_hits: dict[UUID, list[ChannelHit]],
        required_job_groups: Sequence[JobConditionGroup],
        preferred_job_groups: Sequence[JobConditionGroup],
        expanded_required_jobs: Sequence[str] | None = None,
        expanded_preferred_jobs: Sequence[str] | None = None,
    ) -> dict[UUID, PersonEnrichment]:
        del expanded_required_jobs, expanded_preferred_jobs
        if not person_ids:
            return {}

        specs = build_match_evidence_specs(
            request,
            required_job_groups=required_job_groups,
            preferred_job_groups=preferred_job_groups,
        )

        job_map: dict[UUID, dict[str, list[UUID]]] = defaultdict(lambda: defaultdict(list))
        for row in self.db.execute(
            select(PersonJob).where(
                PersonJob.person_id.in_(list(person_ids)),
                PersonJob.is_active.is_(True),
            )
        ).scalars():
            job_map[row.person_id][row.job_code].append(row.id)

        skill_map: dict[UUID, dict[str, list[UUID]]] = defaultdict(lambda: defaultdict(list))
        for row in self.db.execute(
            select(PersonSkill).where(PersonSkill.person_id.in_(list(person_ids)))
        ).scalars():
            skill_map[row.person_id][row.tech_code].append(row.id)

        exp_map: dict[UUID, dict[str, list[UUID]]] = defaultdict(lambda: defaultdict(list))
        for row in self.db.execute(
            select(PersonExpertise).where(PersonExpertise.person_id.in_(list(person_ids)))
        ).scalars():
            exp_map[row.person_id][row.exp_code].append(row.id)

        cert_rows: dict[UUID, list[Certification]] = defaultdict(list)
        for row in self.db.execute(
            select(Certification).where(Certification.person_id.in_(list(person_ids)))
        ).scalars():
            cert_rows[row.person_id].append(row)

        profiles: dict[UUID, PersonProfile] = {}
        for row in self.db.execute(
            select(PersonProfile).where(PersonProfile.person_id.in_(list(person_ids)))
        ).scalars():
            profiles[row.person_id] = row

        projects = list(
            self.db.execute(
                select(Project).where(
                    Project.person_id.in_(list(person_ids)),
                    Project.deleted_at.is_(None),
                )
            ).scalars()
        )
        projects_by_person: dict[UUID, list[Project]] = defaultdict(list)
        for project in projects:
            projects_by_person[project.person_id].append(project)
        project_by_id = {p.id: p for p in projects}
        all_project_ids = [p.id for p in projects]

        proj_job_codes: dict[UUID, set[str]] = defaultdict(set)
        for project_id, code in self.db.execute(
            select(ProjectJob.project_id, ProjectJob.job_code).where(
                ProjectJob.project_id.in_(all_project_ids)
            )
        ).all() if all_project_ids else []:
            proj_job_codes[project_id].add(code)

        proj_skill_codes: dict[UUID, set[str]] = defaultdict(set)
        for project_id, code in self.db.execute(
            select(ProjectSkill.project_id, ProjectSkill.tech_code).where(
                ProjectSkill.project_id.in_(all_project_ids)
            )
        ).all() if all_project_ids else []:
            proj_skill_codes[project_id].add(code)

        proj_exp_codes: dict[UUID, set[str]] = defaultdict(set)
        for project_id, code in self.db.execute(
            select(ProjectExpertise.project_id, ProjectExpertise.exp_code).where(
                ProjectExpertise.project_id.in_(all_project_ids)
            )
        ).all() if all_project_ids else []:
            proj_exp_codes[project_id].add(code)

        proj_biz_codes: dict[UUID, set[str]] = defaultdict(set)
        for project_id, code in self.db.execute(
            select(
                ProjectBusinessDomain.project_id, ProjectBusinessDomain.biz_code
            ).where(ProjectBusinessDomain.project_id.in_(all_project_ids))
        ).all() if all_project_ids else []:
            proj_biz_codes[project_id].add(code)

        proj_cust_codes: dict[UUID, set[str]] = defaultdict(set)
        for project_id, code in self.db.execute(
            select(
                ProjectCustomerType.project_id,
                ProjectCustomerType.customer_type_code,
            ).where(ProjectCustomerType.project_id.in_(all_project_ids))
        ).all() if all_project_ids else []:
            proj_cust_codes[project_id].add(code)

        # --- choose top projects ---
        chosen: dict[UUID, list[UUID]] = {}
        for pid in person_ids:
            summary = project_summaries.get(pid)
            if summary and summary.top_project_ids:
                chosen[pid] = list(summary.top_project_ids)[:TOP_PROJECTS_LIMIT]
            else:
                recent = sorted(
                    projects_by_person.get(pid, []),
                    key=lambda p: (
                        p.end_date is None,
                        -(p.end_date.toordinal() if p.end_date else 0),
                        -(p.start_date.toordinal() if p.start_date else 0),
                        str(p.id),
                    ),
                )
                chosen[pid] = [p.id for p in recent[:TOP_PROJECTS_LIMIT]]

        targets: list[EvidenceTargetKey] = []
        match_targets: dict[UUID, list[list[EvidenceTargetKey]]] = {}

        for pid in person_ids:
            scaffold = scaffold_matches.get(pid, [])
            per_match: list[list[EvidenceTargetKey]] = []
            for idx, spec in enumerate(specs):
                keys = self._resolve_spec_targets(
                    spec=spec,
                    person_id=pid,
                    job_map=job_map,
                    skill_map=skill_map,
                    exp_map=exp_map,
                    cert_rows=cert_rows,
                    profile=profiles.get(pid),
                    projects=projects_by_person.get(pid, []),
                    proj_job_codes=proj_job_codes,
                    proj_skill_codes=proj_skill_codes,
                    proj_exp_codes=proj_exp_codes,
                    proj_biz_codes=proj_biz_codes,
                    proj_cust_codes=proj_cust_codes,
                )
                item = scaffold[idx] if idx < len(scaffold) else None
                if item is None or item.status != "MATCH":
                    keys = []
                per_match.append(keys)
                targets.extend(keys)
            match_targets[pid] = per_match

        evidence_rows = self.evidence_repo.list_supporting_evidence(
            targets, person_ids=person_ids
        )
        by_target: dict[tuple[str, UUID, str | None], list[EvidenceRow]] = defaultdict(
            list
        )
        for row in evidence_rows:
            by_target[(row.target_type, row.target_id, row.field_name)].append(row)

        selected_project_ids = list({i for ids in chosen.values() for i in ids})
        project_evidence_rows = self.evidence_repo.list_supporting_project_evidence(
            selected_project_ids, person_ids=person_ids
        )
        project_evidence_by_id: dict[UUID, list[EvidenceRow]] = defaultdict(list)
        for row in project_evidence_rows:
            if row.person_id in set(person_ids):
                project_evidence_by_id[row.target_id].append(row)

        chunk_ids: list[UUID] = []
        for pid in person_ids:
            for hit in channel_hits.get(pid, []):
                if hit.object_type == "DOCUMENT_CHUNK" and hit.object_id is not None:
                    chunk_ids.append(hit.object_id)
        chunk_rows = self.evidence_repo.list_document_chunks(
            chunk_ids, person_ids=person_ids
        )
        chunks_by_person: dict[UUID, list] = defaultdict(list)
        for row in chunk_rows:
            chunks_by_person[row.person_id].append(row)

        roles = self._project_roles(selected_project_ids)

        out: dict[UUID, PersonEnrichment] = {}
        for pid in person_ids:
            scaffold = scaffold_matches.get(pid, [])
            per_match = match_targets.get(pid, [])
            matches: list[MatchItem] = []
            for idx, item in enumerate(scaffold):
                eids: set[UUID] = set()
                for key in per_match[idx] if idx < len(per_match) else []:
                    for row in by_target.get(
                        (key.target_type, key.target_id, key.field_name), []
                    ):
                        if row.person_id == pid:
                            eids.add(row.evidence_id)
                matches.append(
                    MatchItem(
                        condition=item.condition,
                        type=item.type,
                        status=item.status,
                        evidence_count=len(eids),
                    )
                )

            top_projects: list[TopProjectItem] = []
            for project_id in chosen.get(pid, []):
                project = project_by_id.get(project_id)
                if project is None:
                    continue
                eids_list: list[UUID] = []
                seen: set[UUID] = set()
                for row in project_evidence_by_id.get(project_id, []):
                    if row.person_id != pid:
                        continue
                    if row.evidence_id in seen:
                        continue
                    seen.add(row.evidence_id)
                    eids_list.append(row.evidence_id)
                    if len(eids_list) >= TOP_PROJECT_EVIDENCE_CAP:
                        break
                top_projects.append(
                    TopProjectItem(
                        project_id=project_id,
                        project_name=project.project_name,
                        period=format_project_period(project.start_date, project.end_date),
                        roles=roles.get(project_id, []),
                        evidence_ids=eids_list,
                    )
                )

            evidence_items = self._select_evidence(
                person_id=pid,
                matches=matches,
                per_match_targets=per_match,
                by_target=by_target,
                project_evidence_by_id=project_evidence_by_id,
                top_project_ids=chosen.get(pid, []),
                chunks=chunks_by_person.get(pid, []),
            )
            out[pid] = PersonEnrichment(
                matches=matches,
                top_projects=top_projects,
                evidence=evidence_items,
            )
        return out

    def _resolve_spec_targets(
        self,
        *,
        spec: MatchEvidenceSpec,
        person_id: UUID,
        job_map: dict,
        skill_map: dict,
        exp_map: dict,
        cert_rows: dict,
        profile: PersonProfile | None,
        projects: list[Project],
        proj_job_codes: dict[UUID, set[str]],
        proj_skill_codes: dict[UUID, set[str]],
        proj_exp_codes: dict[UUID, set[str]],
        proj_biz_codes: dict[UUID, set[str]],
        proj_cust_codes: dict[UUID, set[str]],
    ) -> list[EvidenceTargetKey]:
        keys: list[EvidenceTargetKey] = []
        cat = spec.category

        if cat == "jobs":
            allowed = set()
            for group in spec.job_groups:
                allowed.update(group)
            for code, ids in job_map.get(person_id, {}).items():
                if code in allowed:
                    keys.extend(
                        EvidenceTargetKey("PERSON_JOB", tid, "job_code") for tid in ids
                    )
            for project in projects:
                for code in proj_job_codes.get(project.id, set()):
                    if code in allowed:
                        keys.append(
                            EvidenceTargetKey(
                                "PROJECT",
                                project.id,
                                project_relation_field_name("jobs", code),
                            )
                        )
            return keys

        if cat == "skills":
            codes = list(spec.requested)
            for code in codes:
                for tid in skill_map.get(person_id, {}).get(code, []):
                    keys.append(EvidenceTargetKey("PERSON_SKILL", tid, "tech_code"))
                for project in projects:
                    if code in proj_skill_codes.get(project.id, set()):
                        keys.append(
                            EvidenceTargetKey(
                                "PROJECT",
                                project.id,
                                project_relation_field_name("skills", code),
                            )
                        )
            return keys

        if cat == "expertise":
            for code in spec.requested:
                for tid in exp_map.get(person_id, {}).get(code, []):
                    keys.append(
                        EvidenceTargetKey("PERSON_EXPERTISE", tid, "exp_code")
                    )
                for project in projects:
                    if code in proj_exp_codes.get(project.id, set()):
                        keys.append(
                            EvidenceTargetKey(
                                "PROJECT",
                                project.id,
                                project_relation_field_name("expertise", code),
                            )
                        )
            return keys

        if cat == "business_domains":
            for code in spec.requested:
                for project in projects:
                    if code in proj_biz_codes.get(project.id, set()):
                        keys.append(
                            EvidenceTargetKey(
                                "PROJECT",
                                project.id,
                                project_relation_field_name("business_domains", code),
                            )
                        )
            return keys

        if cat == "customer_types":
            for code in spec.requested:
                for project in projects:
                    if code in proj_cust_codes.get(project.id, set()):
                        keys.append(
                            EvidenceTargetKey(
                                "PROJECT",
                                project.id,
                                project_relation_field_name("customer_types", code),
                            )
                        )
            return keys

        if cat == "grade":
            return [EvidenceTargetKey("PERSON_PROFILE", person_id, "technical_grade")]

        if cat == "career":
            return self._career_evidence_targets(person_id, profile)

        if cat == "affiliations":
            return [
                EvidenceTargetKey("PERSON_PROFILE", person_id, "affiliation_company")
            ]

        if cat == "certifications":
            for cert in cert_rows.get(person_id, []):
                if self._cert_matches_tokens(cert, spec.requested):
                    keys.append(EvidenceTargetKey("CERTIFICATION", cert.id, None))
            return keys

        if cat == "project_keywords":
            return self._project_keyword_targets(projects, spec.requested)

        return keys

    @staticmethod
    def _career_evidence_targets(
        person_id: UUID, profile: PersonProfile | None
    ) -> list[EvidenceTargetKey]:
        """Hard filter uses COALESCE(confirmed, calculated); target that field only."""
        if profile is None:
            return []
        if profile.career_confirmed_months is not None:
            return [
                EvidenceTargetKey(
                    "PERSON_PROFILE", person_id, "career_confirmed_months"
                )
            ]
        if profile.career_calculated_months is not None:
            return [
                EvidenceTargetKey(
                    "PERSON_PROFILE", person_id, "career_calculated_months"
                )
            ]
        return []

    @staticmethod
    def _cert_matches_tokens(cert: Certification, tokens: Sequence[str]) -> bool:
        name = (cert.certification_name or "").casefold()
        issuer = (cert.issuer or "").casefold()
        for token in tokens:
            needle = token.casefold()
            if needle and (needle in name or needle in issuer):
                return True
        return False

    @staticmethod
    def _project_keyword_targets(
        projects: Sequence[Project], tokens: Sequence[str]
    ) -> list[EvidenceTargetKey]:
        keys: list[EvidenceTargetKey] = []
        needles = [t.casefold() for t in tokens if t]
        if not needles:
            return keys
        for project in projects:
            field_values = {
                "project_name": project.project_name or "",
                "customer_name": project.customer_name or "",
                "responsibilities": project.responsibilities or "",
                "project_summary": project.project_summary or "",
            }
            matched_fields: list[str] = []
            for field_name, value in field_values.items():
                blob = value.casefold()
                if any(n in blob for n in needles):
                    matched_fields.append(field_name)
            if not matched_fields:
                continue
            for field_name in matched_fields:
                keys.append(EvidenceTargetKey("PROJECT", project.id, field_name))
            # Root SUPPORTS link can back the whole matched project.
            keys.append(EvidenceTargetKey("PROJECT", project.id, None))
        return keys

    def _select_evidence(
        self,
        *,
        person_id: UUID,
        matches: list[MatchItem],
        per_match_targets: list[list[EvidenceTargetKey]],
        by_target: dict,
        project_evidence_by_id: dict[UUID, list[EvidenceRow]],
        top_project_ids: list[UUID],
        chunks: list,
    ) -> list[EvidenceItem]:
        selected: list[EvidenceItem] = []
        seen: set[tuple] = set()

        def add_evidence_row(row: EvidenceRow, source_level: str) -> None:
            key = ("E", row.evidence_id)
            if key in seen:
                return
            seen.add(key)
            selected.append(
                EvidenceItem(
                    evidence_id=row.evidence_id,
                    source_level=source_level,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    field_name=row.field_name,
                    relation_type=row.relation_type or RELATION_TYPE_SUPPORTS,
                    document_id=row.document_id,
                    document_title=row.document_title,
                    original_filename=row.original_filename,
                    version_no=row.version_no,
                    page_no=row.page_no,
                    snippet=clip_snippet(row.quote_text),
                )
            )

        for match_type in ("REQUIRED", "PREFERRED"):
            for idx, item in enumerate(matches):
                if item.type != match_type or item.status != "MATCH":
                    continue
                for key in per_match_targets[idx] if idx < len(per_match_targets) else []:
                    for row in by_target.get(
                        (key.target_type, key.target_id, key.field_name), []
                    ):
                        if row.person_id != person_id:
                            continue
                        level = (
                            SOURCE_CONFIRMED_PROJECT
                            if row.target_type == "PROJECT"
                            else SOURCE_CONFIRMED_PROFILE
                        )
                        add_evidence_row(row, level)
                        if len(selected) >= SEARCH_EVIDENCE_LIMIT:
                            return selected

        for project_id in top_project_ids:
            for row in project_evidence_by_id.get(project_id, []):
                if row.person_id != person_id:
                    continue
                add_evidence_row(row, SOURCE_CONFIRMED_PROJECT)
                if len(selected) >= SEARCH_EVIDENCE_LIMIT:
                    return selected

        for chunk in chunks:
            key = ("C", chunk.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            selected.append(
                EvidenceItem(
                    evidence_id=None,
                    source_level=SOURCE_DOCUMENT_CHUNK,
                    document_id=chunk.document_id,
                    document_title=chunk.document_title,
                    original_filename=chunk.original_filename,
                    version_no=chunk.version_no,
                    page_no=chunk.page_no,
                    snippet=clip_snippet(chunk.chunk_text),
                )
            )
            if len(selected) >= SEARCH_EVIDENCE_LIMIT:
                return selected
        return selected

    def _project_roles(self, project_ids: Sequence[UUID]) -> dict[UUID, list[str]]:
        if not project_ids:
            return {}
        rows = self.db.execute(
            select(ProjectJob.project_id, CodeMaster.name, CodeMaster.code)
            .join(CodeMaster, CodeMaster.code == ProjectJob.job_code)
            .where(ProjectJob.project_id.in_(list(project_ids)))
            .order_by(
                CodeMaster.sort_order.asc(),
                CodeMaster.name.asc(),
                CodeMaster.code.asc(),
            )
        ).all()
        out: dict[UUID, list[str]] = defaultdict(list)
        for project_id, name, code in rows:
            label = name or code
            bucket = out[project_id]
            if label not in bucket and len(bucket) < 5:
                bucket.append(label)
        return out
