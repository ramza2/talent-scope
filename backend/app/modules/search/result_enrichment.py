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
    PersonSkill,
)
from app.db.models.project import Project, ProjectJob
from app.modules.evidence.materializer import (
    RELATION_TYPE_SUPPORTS,
    project_relation_field_name,
)
from app.modules.search.project_ranking import (
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
    EvidenceTargetKey,
    SearchEvidenceRepository,
    clip_snippet,
)

SOURCE_CONFIRMED_PROFILE = "CONFIRMED_PROFILE"
SOURCE_CONFIRMED_PROJECT = "CONFIRMED_PROJECT"
SOURCE_DOCUMENT_CHUNK = "DOCUMENT_CHUNK"


@dataclass
class PersonEnrichment:
    matches: list[MatchItem]
    top_projects: list[TopProjectItem]
    evidence: list[EvidenceItem]


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
        expanded_required_jobs: Sequence[str],
        expanded_preferred_jobs: Sequence[str],
    ) -> dict[UUID, PersonEnrichment]:
        if not person_ids:
            return {}

        # --- entity maps for page persons ---
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

        cert_map: dict[UUID, list[UUID]] = defaultdict(list)
        for row in self.db.execute(
            select(Certification).where(Certification.person_id.in_(list(person_ids)))
        ).scalars():
            cert_map[row.person_id].append(row.id)

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

        # --- build evidence targets for matches + top projects ---
        targets: list[EvidenceTargetKey] = []
        match_targets: dict[UUID, list[list[EvidenceTargetKey]]] = {}
        req_job_set = set(expanded_required_jobs)
        pref_job_set = set(expanded_preferred_jobs)

        for pid in person_ids:
            per_match: list[list[EvidenceTargetKey]] = []
            scaffold = scaffold_matches.get(pid, [])
            # Derive targets in scaffold order using request shape.
            expected: list[list[EvidenceTargetKey]] = []
            req = request.required

            if req.jobs:
                keys: list[EvidenceTargetKey] = []
                for code, ids in job_map.get(pid, {}).items():
                    if code in req_job_set:
                        keys.extend(
                            EvidenceTargetKey("PERSON_JOB", tid, "job_code") for tid in ids
                        )
                expected.append(keys)
            if request.skill_match_mode == "ALL":
                for code in req.skills:
                    keys = [
                        EvidenceTargetKey("PERSON_SKILL", tid, "tech_code")
                        for tid in skill_map.get(pid, {}).get(code, [])
                    ]
                    expected.append(keys)
            elif req.skills:
                keys = []
                for code in req.skills:
                    keys.extend(
                        EvidenceTargetKey("PERSON_SKILL", tid, "tech_code")
                        for tid in skill_map.get(pid, {}).get(code, [])
                    )
                expected.append(keys)
            if req.expertise:
                keys = []
                for code in req.expertise:
                    keys.extend(
                        EvidenceTargetKey("PERSON_EXPERTISE", tid, "exp_code")
                        for tid in exp_map.get(pid, {}).get(code, [])
                    )
                expected.append(keys)
            if req.business_domains:
                expected.append([])  # project-level filled below via top projects
            if req.customer_types:
                expected.append([])
            if req.grade and req.grade.values:
                expected.append(
                    [EvidenceTargetKey("PERSON_PROFILE", pid, "technical_grade")]
                )
            if req.career and (
                req.career.min_months is not None or req.career.max_months is not None
            ):
                expected.append(
                    [
                        EvidenceTargetKey("PERSON_PROFILE", pid, "career_confirmed_months"),
                        EvidenceTargetKey("PERSON_PROFILE", pid, "career_calculated_months"),
                        EvidenceTargetKey("PERSON_PROFILE", pid, "career_document_value"),
                    ]
                )
            if req.affiliations:
                expected.append(
                    [EvidenceTargetKey("PERSON_PROFILE", pid, "affiliation_company")]
                )
            if req.certifications:
                expected.append(
                    [EvidenceTargetKey("CERTIFICATION", cid, None) for cid in cert_map.get(pid, [])]
                )
            if req.project_keywords:
                expected.append(
                    [EvidenceTargetKey("PROJECT", p.id, None) for p in projects_by_person.get(pid, [])]
                )

            for code in request.preferred.jobs:
                keys = []
                if code in pref_job_set or code in job_map.get(pid, {}):
                    for person_code, ids in job_map.get(pid, {}).items():
                        if person_code == code or person_code in pref_job_set:
                            keys.extend(
                                EvidenceTargetKey("PERSON_JOB", tid, "job_code")
                                for tid in ids
                                if person_code == code
                            )
                expected.append(keys)
            for code in request.preferred.skills:
                expected.append(
                    [
                        EvidenceTargetKey("PERSON_SKILL", tid, "tech_code")
                        for tid in skill_map.get(pid, {}).get(code, [])
                    ]
                )
            for code in request.preferred.expertise:
                expected.append(
                    [
                        EvidenceTargetKey("PERSON_EXPERTISE", tid, "exp_code")
                        for tid in exp_map.get(pid, {}).get(code, [])
                    ]
                )
            for _code in request.preferred.business_domains:
                expected.append([])
            for _code in request.preferred.customer_types:
                expected.append([])

            for idx, item in enumerate(scaffold):
                keys = expected[idx] if idx < len(expected) else []
                if item.status != "MATCH":
                    keys = []
                per_match.append(keys)
                targets.extend(keys)
            match_targets[pid] = per_match

            for project_id in chosen.get(pid, []):
                targets.append(EvidenceTargetKey("PROJECT", project_id, None))
                # also relation fields for requested codes
                for code in list(req.jobs) + list(request.preferred.jobs):
                    targets.append(
                        EvidenceTargetKey(
                            "PROJECT",
                            project_id,
                            project_relation_field_name("jobs", code),
                        )
                    )
                for code in list(req.skills) + list(request.preferred.skills):
                    targets.append(
                        EvidenceTargetKey(
                            "PROJECT",
                            project_id,
                            project_relation_field_name("skills", code),
                        )
                    )
                for code in list(req.expertise) + list(request.preferred.expertise):
                    targets.append(
                        EvidenceTargetKey(
                            "PROJECT",
                            project_id,
                            project_relation_field_name("expertise", code),
                        )
                    )

        evidence_rows = self.evidence_repo.list_supporting_evidence(
            targets, person_ids=person_ids
        )
        by_target: dict[tuple[str, UUID, str | None], list] = defaultdict(list)
        by_person: dict[UUID, list] = defaultdict(list)
        for row in evidence_rows:
            by_target[(row.target_type, row.target_id, row.field_name)].append(row)
            by_person[row.person_id].append(row)

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

        roles = self._project_roles(list({i for ids in chosen.values() for i in ids}))

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
                for row in by_person.get(pid, []):
                    if row.target_type == "PROJECT" and row.target_id == project_id:
                        if row.evidence_id in seen:
                            continue
                        seen.add(row.evidence_id)
                        eids_list.append(row.evidence_id)
                        if len(eids_list) >= 5:
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
                by_person=by_person,
                top_project_ids=chosen.get(pid, []),
                chunks=chunks_by_person.get(pid, []),
            )
            out[pid] = PersonEnrichment(
                matches=matches,
                top_projects=top_projects,
                evidence=evidence_items,
            )
        return out

    def _select_evidence(
        self,
        *,
        person_id: UUID,
        matches: list[MatchItem],
        per_match_targets: list[list[EvidenceTargetKey]],
        by_target: dict,
        by_person: dict,
        top_project_ids: list[UUID],
        chunks: list,
    ) -> list[EvidenceItem]:
        selected: list[EvidenceItem] = []
        seen: set[tuple] = set()

        def add_evidence_row(row, source_level: str) -> None:
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
            for row in by_person.get(person_id, []):
                if row.target_type == "PROJECT" and row.target_id == project_id:
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
