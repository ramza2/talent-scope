"""V39-B: project relation codes must dedupe within Confirm TX (autoflush=False)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

pytest_plugins = ["tests.test_analysis"]


def _project_payload(
    name: str,
    *,
    job: str = "JOB-DEV-FE",
    tech: str = "TECH-LANG-JAVA",
    exp: str = "EXP-AI-RAG",
    biz: str = "BIZ-PUBLIC",
    customer_type: str = "CUSTOMER-PRIVATE",
) -> dict:
    """Payload with intentional duplicate codes in every relation list."""
    return {
        "project_name": name,
        "customer_name": "테스트고객",
        "start_date": "2023-01",
        "end_date": "2023-12",
        "jobs": [
            {"code": job, "raw_value": "프론트"},
            {"code": job, "raw_value": "프론트 중복"},
        ],
        "skills": [
            {"code": tech, "raw_value": "Java"},
            {"code": tech, "raw_value": "Java 중복"},
        ],
        "expertise": [
            {"code": exp, "raw_value": "RAG", "evidence_type": "EXPLICIT"},
            {"code": exp, "raw_value": "RAG 중복", "evidence_type": "INFERRED"},
        ],
        "business_domains": [
            {"code": biz, "raw_value": "공공"},
            {"code": biz, "raw_value": "공공 중복"},
        ],
        "customer_types": [
            {"code": customer_type, "raw_value": "민간"},
            {"code": customer_type, "raw_value": "민간 중복"},
        ],
    }


def test_confirm_dedupes_duplicate_project_relation_codes_three_projects(
    client: TestClient, db_session
) -> None:
    """3 projects with in-payload duplicate codes → all saved, skipped=0."""
    from app.db.models.project import (
        Project,
        ProjectBusinessDomain,
        ProjectCustomerType,
        ProjectExpertise,
        ProjectJob,
        ProjectSkill,
    )
    from app.modules.analysis.confirm import confirm_analysis_run
    from tests.test_analysis import (
        _cleanup_person_with_search,
        _create_user,
        _ensure_analysis_code,
        _login,
        _seed_person_with_ready_doc,
        _start_reviewing_analysis,
    )

    for code, ctype, name in (
        ("JOB-DEV-FE", "JOB", "프론트엔드"),
        ("TECH-LANG-JAVA", "TECH", "Java"),
        ("EXP-AI-RAG", "EXP", "RAG"),
        ("BIZ-PUBLIC", "BIZ", "공공"),
        ("CUSTOMER-PRIVATE", "CUSTOMER_TYPE", "민간"),
    ):
        _ensure_analysis_code(db_session, code, ctype, name)

    admin = _create_user(
        db_session, login_id=f"v39b_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id, run = _start_reviewing_analysis(
        client, db_session, csrf, person, document
    )

    from app.db.models.analysis import AnalysisDiffItem

    names = ["모바일 퍼즐 게임", "사내 포털", "공공 데이터 허브"]
    for idx, name in enumerate(names):
        db_session.add(
            AnalysisDiffItem(
                analysis_run_id=run.id,
                entity_type="PROJECT",
                candidate_path=f"projects[{idx}]",
                change_type="NEW",
                new_value=_project_payload(name),
                review_status="ACCEPTED",
            )
        )
    db_session.commit()

    try:
        result = confirm_analysis_run(
            db_session,
            analysis_id=uuid.UUID(analysis_id),
            expected_profile_version=1,
            actor_user_id=admin.id,
            skip_invalid_values=True,
        )
        db_session.commit()

        assert result.skipped_diff_count == 0
        assert result.skipped_diff_paths == []

        projects = list(
            db_session.execute(
                select(Project).where(
                    Project.person_id == person.id,
                    Project.deleted_at.is_(None),
                )
            ).scalars()
        )
        assert {p.project_name for p in projects} == set(names)
        assert len(projects) == 3

        for project in projects:
            job_codes = list(
                db_session.execute(
                    select(ProjectJob.job_code).where(
                        ProjectJob.project_id == project.id
                    )
                ).scalars()
            )
            assert job_codes == ["JOB-DEV-FE"]

            tech_codes = list(
                db_session.execute(
                    select(ProjectSkill.tech_code).where(
                        ProjectSkill.project_id == project.id
                    )
                ).scalars()
            )
            assert tech_codes == ["TECH-LANG-JAVA"]

            exp_rows = list(
                db_session.execute(
                    select(ProjectExpertise).where(
                        ProjectExpertise.project_id == project.id
                    )
                ).scalars()
            )
            assert len(exp_rows) == 1
            assert exp_rows[0].exp_code == "EXP-AI-RAG"
            assert exp_rows[0].evidence_type == "EXPLICIT"

            biz_codes = list(
                db_session.execute(
                    select(ProjectBusinessDomain.biz_code).where(
                        ProjectBusinessDomain.project_id == project.id
                    )
                ).scalars()
            )
            assert biz_codes == ["BIZ-PUBLIC"]

            cust_codes = list(
                db_session.execute(
                    select(ProjectCustomerType.customer_type_code).where(
                        ProjectCustomerType.project_id == project.id
                    )
                ).scalars()
            )
            assert cust_codes == ["CUSTOMER-PRIVATE"]

        # No duplicate PK rows across the three projects' relations.
        for model, col in (
            (ProjectJob, ProjectJob.job_code),
            (ProjectSkill, ProjectSkill.tech_code),
            (ProjectExpertise, ProjectExpertise.exp_code),
            (ProjectBusinessDomain, ProjectBusinessDomain.biz_code),
            (ProjectCustomerType, ProjectCustomerType.customer_type_code),
        ):
            pids = [p.id for p in projects]
            total = db_session.execute(
                select(func.count()).select_from(model).where(model.project_id.in_(pids))
            ).scalar_one()
            distinct = db_session.execute(
                select(func.count()).select_from(
                    select(model.project_id, col)
                    .where(model.project_id.in_(pids))
                    .distinct()
                    .subquery()
                )
            ).scalar_one()
            assert total == distinct == 3
    finally:
        _cleanup_person_with_search(db_session, person.id, admin.id)


def test_confirm_merged_project_dedupes_overlapping_relation_codes(
    client: TestClient, db_session
) -> None:
    """MERGED base + decided_value sharing the same codes must not PK-collide."""
    from app.db.models.analysis import AnalysisDiffItem
    from app.db.models.project import Project, ProjectJob, ProjectSkill
    from app.modules.analysis.confirm import confirm_analysis_run
    from tests.test_analysis import (
        _cleanup_person_with_search,
        _create_user,
        _ensure_analysis_code,
        _login,
        _seed_person_with_ready_doc,
        _start_reviewing_analysis,
    )

    _ensure_analysis_code(db_session, "JOB-DEV-FE", "JOB", "프론트엔드")
    _ensure_analysis_code(db_session, "TECH-LANG-JAVA", "TECH", "Java")

    admin = _create_user(
        db_session, login_id=f"v39bm_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id, run = _start_reviewing_analysis(
        client, db_session, csrf, person, document
    )

    existing = Project(
        person_id=person.id,
        project_name="기존 프로젝트",
        customer_name="기존고객",
        source_type="USER",
    )
    db_session.add(existing)
    db_session.flush()
    db_session.add(ProjectJob(project_id=existing.id, job_code="JOB-DEV-FE"))
    db_session.flush()

    base = {
        "project_name": "기존 프로젝트",
        "customer_name": "기존고객",
        "jobs": [
            {"code": "JOB-DEV-FE", "raw_value": "FE"},
            {"code": "JOB-DEV-FE", "raw_value": "FE dup"},
        ],
        "skills": [
            {"code": "TECH-LANG-JAVA", "raw_value": "Java"},
            {"code": "TECH-LANG-JAVA", "raw_value": "Java dup"},
        ],
        "expertise": [],
        "business_domains": [],
        "customer_types": [],
    }
    decided = {
        "project_name": "기존 프로젝트(갱신)",
        "jobs": [{"code": "JOB-DEV-FE", "raw_value": "FE again"}],
        "skills": [{"code": "TECH-LANG-JAVA", "raw_value": "Java again"}],
        "expertise": [],
        "business_domains": [],
        "customer_types": [],
    }
    db_session.add(
        AnalysisDiffItem(
            analysis_run_id=run.id,
            entity_type="PROJECT",
            candidate_path="projects[0]",
            change_type="REVIEW",
            existing_target_id=existing.id,
            new_value=base,
            decided_value=decided,
            review_status="MERGED",
        )
    )
    db_session.commit()

    try:
        result = confirm_analysis_run(
            db_session,
            analysis_id=uuid.UUID(analysis_id),
            expected_profile_version=1,
            actor_user_id=admin.id,
            skip_invalid_values=True,
        )
        db_session.commit()

        assert result.skipped_diff_count == 0

        jobs = list(
            db_session.execute(
                select(ProjectJob.job_code).where(ProjectJob.project_id == existing.id)
            ).scalars()
        )
        assert jobs == ["JOB-DEV-FE"]

        skills = list(
            db_session.execute(
                select(ProjectSkill.tech_code).where(
                    ProjectSkill.project_id == existing.id
                )
            ).scalars()
        )
        assert skills == ["TECH-LANG-JAVA"]

        project = db_session.get(Project, existing.id)
        assert project is not None
        assert project.project_name == "기존 프로젝트(갱신)"
    finally:
        _cleanup_person_with_search(db_session, person.id, admin.id)
