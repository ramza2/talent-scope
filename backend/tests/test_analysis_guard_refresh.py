"""Guard-only refresh API for REVIEWING analyses (no AI re-analysis)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.ai.schemas.profile_candidate import (
    CertificationCandidate,
    EducationCandidate,
    EmploymentCandidate,
    ProfileCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


@pytest.fixture()
def redis_prefix() -> str:
    return f"talentscope:test:{uuid.uuid4().hex}"


@pytest.fixture()
def client(
    redis_prefix: str, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.run_profile_analysis.delay",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.identify_upload_session.delay",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.tasks.document_tasks.process_document.delay",
        lambda *_a, **_k: None,
    )
    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.main import create_app
    from app.storage.s3 import reset_object_storage_cache

    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

    from app.storage.s3 import build_object_storage

    storage = build_object_storage()
    if hasattr(storage, "clear"):
        storage.clear()
    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()


@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _create_user(db_session, *, login_id: str, password: str, role: str = "ADMIN"):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=login_id,
        password_hash=hash_password(password),
        name="Tester",
        role=role,
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(client: TestClient, login_id: str, password: str) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"login_id": login_id, "password": password}
    )
    assert resp.status_code == 200, resp.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


def _ensure_code(db_session, code: str, *, code_type: str, name: str) -> str:
    from app.db.models.code import CodeMaster

    row = db_session.get(CodeMaster, code)
    if row is None:
        db_session.add(
            CodeMaster(
                code=code,
                code_type=code_type,
                name=name,
                sort_order=1,
                is_active=True,
            )
        )
        db_session.commit()
    return code


def _docx_id() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _kosa_id() -> uuid.UUID:
    return uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _pre_guard_candidate(*, docx: str, kosa: str) -> ProfileCandidateDocument:
    """2/4/7/70 candidate that guards collapse to 1/2/6/70 with page evidence."""
    mislinked = [
        SourceRef(document_id=docx, page_no=1, quote_text="비스프로컨설팅(주)")
    ]
    return ProfileCandidateDocument(
        profile=ProfileCandidate(name="권민규"),
        education=[
            EducationCandidate(school_name="한림대학교", degree="학사"),
            EducationCandidate(
                school_name="한림대학교",
                degree="학사",
                major="유전공학",
                start_date="1994.03.02",
                end_date="1999.02.20",
            ),
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                acquired_date="2006.12.18",
                issuer="한림대학교",
            ),
            CertificationCandidate(
                certification_name="경보처리기사",
                acquired_date="2006.12.18",
            ),
            CertificationCandidate(
                certification_name="전자계산기조직응용기사",
                acquired_date="2008.06.02",
            ),
            CertificationCandidate(
                certification_name="전자계산기조작용용기사",
                acquired_date="2008.06.02",
            ),
        ],
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02",
                end_date="2012.11",
                source_refs=list(mislinked),
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02.01",
                end_date="2012.11.30",
                source_refs=list(mislinked),
            ),
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2012.12",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="피앤드티컨설팅",
                start_date="2008.01",
                end_date="2009.12",
            ),
            EmploymentCandidate(
                company_name="네모소프트(주)",
                start_date="2007.07",
                end_date="2011.01",
            ),
            EmploymentCandidate(
                company_name="모스트",
                start_date="2016.03",
                end_date="2017.12",
            ),
            EmploymentCandidate(
                company_name="오픈링크",
                start_date="2015.01",
                end_date="2020.12",
            ),
        ],
        projects=[
            ProjectCandidate(project_name=f"사업{i}", start_date="2020.01")
            for i in range(70)
        ],
    )


def _docx_page_text() -> str:
    return (
        "학력\n"
        "한림대학교 유전공학 1994.03.02 ~ 1999.02.20 학사\n"
        "자격증\n"
        "정보처리기사 2006.12.18\n"
        "전자계산기조직응용기사 2008.06.02\n"
        "근무경력\n"
        "회사명\n"
        "기간\n"
        "직위\n"
        "담당업무\n"
        "피엔드티컨설팅(주)\n"
        "2012.12 ~ 2014.09\n"
        "과장\n"
        "시스템 운영\n"
        "비스프로컨설팅(주)\n"
        "2011.02 ~ 2012.11\n"
        "과장\n"
        "SI 업무\n"
        "네모소프트(주)\n"
        "2007.07 ~ 2011.01\n"
        "사원\n"
        "개발\n"
    )


def _kosa_page_text() -> str:
    return (
        "학력\n"
        "한림대학교 / 유전공학 / 1994.03.02~1999.02.20 / 학사\n"
        "자격증\n"
        "경보처리기사 2006.12.18\n"
        "전자계산기조작용용기사 2008.06.02\n"
        "근무경력\n"
        "근무처 비스프로컨설팅(주) 2011.02.01 ~ 2012.11.30 과장\n"
    )


def _seed_reviewing_guard_case(db_session, admin_id):
    from app.db.models.analysis import (
        AnalysisDiffItem,
        AnalysisRun,
        AnalysisRunDocument,
    )
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    _ensure_code(db_session, "DOC-PROFILE", code_type="DOC_TYPE", name="프로필")
    _ensure_code(db_session, "DOC-KOSA", code_type="DOC_TYPE", name="KOSA")

    person = Person(status="ACTIVE", created_by=admin_id)
    db_session.add(person)
    db_session.flush()
    profile = PersonProfile(
        person_id=person.id,
        name="권민규",
        technical_grade="ADVANCED",
        profile_version=1,
    )
    db_session.add(profile)
    db_session.flush()
    snap = build_confirmed_profile_snapshot(db_session, person.id)
    db_session.add(
        ProfileRevision(
            person_id=person.id,
            revision_no=1,
            snapshot_json=snap,
            source_type="USER",
            created_by=admin_id,
        )
    )

    docx_id = _docx_id()
    kosa_id = _kosa_id()
    docs: dict[str, Document] = {}
    for doc_id, code, filename, page_text in (
        (docx_id, "DOC-PROFILE", "career.docx", _docx_page_text()),
        (kosa_id, "DOC-KOSA", "kosa.pdf", _kosa_page_text()),
    ):
        group = DocumentGroup(
            person_id=person.id,
            document_type_code=code,
            title=filename,
        )
        db_session.add(group)
        db_session.flush()
        document = Document(
            id=doc_id,
            document_group_id=group.id,
            version_no=1,
            is_latest=True,
            original_filename=filename,
            extension=filename.rsplit(".", 1)[-1],
            mime_type="application/octet-stream",
            file_size=100,
            storage_key=f"test/{doc_id}.bin",
            sha256="a" * 64,
            processing_status="READY",
            uploaded_by=admin_id,
        )
        db_session.add(document)
        db_session.flush()
        db_session.add(
            DocumentPage(
                document_id=document.id,
                page_no=1,
                extracted_text=page_text,
                layout_json={"needs_vlm": False},
                extraction_method="TEXT_PARSER",
            )
        )
        docs[code] = document

    candidate = _pre_guard_candidate(docx=str(docx_id), kosa=str(kosa_id))
    assert len(candidate.education) == 2
    assert len(candidate.certifications) == 4
    assert len(candidate.employment_history) == 7
    assert len(candidate.projects) == 70

    run = AnalysisRun(
        person_id=person.id,
        status="REVIEWING",
        candidate_json=candidate.to_storage_dict(),
        base_profile_version=1,
        prompt_version="test",
        schema_version="test",
        overall_confidence=Decimal("0.9000"),
    )
    db_session.add(run)
    db_session.flush()
    for document in docs.values():
        db_session.add(
            AnalysisRunDocument(
                analysis_run_id=run.id,
                document_id=document.id,
                analysis_role="SOURCE",
            )
        )
    # Seed a few PENDING diffs (will be replaced on apply when changed).
    for path in ("profile.name", "employment_history[0]", "projects[0]"):
        db_session.add(
            AnalysisDiffItem(
                analysis_run_id=run.id,
                entity_type="PROFILE" if path.startswith("profile") else (
                    "EMPLOYMENT" if path.startswith("employment") else "PROJECT"
                ),
                candidate_path=path,
                field_name="name" if path == "profile.name" else None,
                change_type="NEW",
                new_value="x",
                review_status="PENDING",
            )
        )
    db_session.commit()
    db_session.refresh(run)
    return person, run, docs


def _cleanup(db_session, person_id, admin_id) -> None:
    from app.db.models.analysis import (
        AnalysisDiffItem,
        AnalysisRun,
        AnalysisRunDocument,
    )
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision

    run_ids = list(
        db_session.execute(
            select(AnalysisRun.id).where(AnalysisRun.person_id == person_id)
        )
        .scalars()
        .all()
    )
    if run_ids:
        db_session.execute(
            delete(AnalysisDiffItem).where(
                AnalysisDiffItem.analysis_run_id.in_(run_ids)
            )
        )
        db_session.execute(
            delete(AnalysisRunDocument).where(
                AnalysisRunDocument.analysis_run_id.in_(run_ids)
            )
        )
        db_session.execute(
            delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids))
        )
    group_ids = list(
        db_session.execute(
            select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)
        )
        .scalars()
        .all()
    )
    if group_ids:
        doc_ids = list(
            db_session.execute(
                select(Document.id).where(Document.document_group_id.in_(group_ids))
            )
            .scalars()
            .all()
        )
        if doc_ids:
            db_session.execute(
                delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids))
            )
            db_session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        db_session.execute(
            delete(DocumentGroup).where(DocumentGroup.id.in_(group_ids))
        )
    db_session.execute(
        delete(ProfileRevision).where(ProfileRevision.person_id == person_id)
    )
    db_session.execute(
        delete(PersonProfile).where(PersonProfile.person_id == person_id)
    )
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == person_id))
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.action_type == "ANALYSIS_GUARD_REFRESH",
            AuditLog.user_id == admin_id,
        )
    )
    db_session.execute(delete(Person).where(Person.id == person_id))
    db_session.commit()


def test_guard_refresh_preview_and_apply_kwon_shaped(
    client: TestClient, db_session
) -> None:
    admin = _create_user(
        db_session, login_id=f"gr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, run, _docs = _seed_reviewing_guard_case(db_session, admin.id)
    analysis_id = str(run.id)

    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.db.models.revision import AuditLog

    before_diff_ids = set(
        db_session.execute(
            select(AnalysisDiffItem.id).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    )
    assert len(before_diff_ids) == 3

    preview = client.get(f"/api/v1/analyses/{analysis_id}/guard-refresh/preview")
    assert preview.status_code == 200, preview.text
    pdata = preview.json()["data"]
    assert pdata["changed"] is True
    assert pdata["applied"] is False
    assert pdata["ui_refresh_required"] is False
    assert pdata["before"] == {
        "education": 2,
        "certifications": 4,
        "employment": 7,
        "projects": 70,
    }
    assert pdata["after"] == {
        "education": 1,
        "certifications": 2,
        "employment": 6,
        "projects": 70,
    }

    # Preview must not mutate candidate or diffs.
    db_session.refresh(run)
    stored = ProfileCandidateDocument.model_validate(run.candidate_json)
    assert len(stored.employment_history) == 7
    assert set(
        db_session.execute(
            select(AnalysisDiffItem.id).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    ) == before_diff_ids

    apply = client.post(
        f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
        headers={"X-CSRF-Token": csrf},
    )
    assert apply.status_code == 200, apply.text
    adata = apply.json()["data"]
    assert adata["changed"] is True
    assert adata["applied"] is True
    assert adata["ui_refresh_required"] is True
    assert "새로고침" in (adata.get("message") or "")
    assert adata["before"]["employment"] == 7
    assert adata["after"]["employment"] == 6
    assert adata["pending_diff_count_after"] == adata["diff_count_after"]
    assert adata["diff_count_after"] >= 1

    db_session.expire_all()
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == run.id)
    ).scalar_one()
    assert run.status == "REVIEWING"
    refreshed = ProfileCandidateDocument.model_validate(run.candidate_json)
    assert len(refreshed.education) == 1
    assert len(refreshed.certifications) == 2
    assert len(refreshed.employment_history) == 6
    assert len(refreshed.projects) == 70

    diffs = list(
        db_session.execute(
            select(AnalysisDiffItem).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    )
    assert diffs
    assert all(d.review_status == "PENDING" for d in diffs)
    assert {d.id for d in diffs}.isdisjoint(before_diff_ids)

    audit = db_session.execute(
        select(AuditLog)
        .where(
            AuditLog.action_type == "ANALYSIS_GUARD_REFRESH",
            AuditLog.target_id == run.id,
        )
        .order_by(AuditLog.created_at.desc())
    ).scalars().first()
    assert audit is not None
    assert "candidate_json" not in (audit.before_json or {})
    assert "candidate_json" not in (audit.after_json or {})
    assert (audit.after_json or {}).get("entity_counts", {}).get("employment") == 6

    # Idempotent second apply — no further change / no diff replace.
    after_ids = {d.id for d in diffs}
    again = client.post(
        f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
        headers={"X-CSRF-Token": csrf},
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["changed"] is False
    assert again.json()["data"]["applied"] is False
    assert again.json()["data"]["ui_refresh_required"] is False
    assert set(
        db_session.execute(
            select(AnalysisDiffItem.id).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    ) == after_ids

    _cleanup(db_session, person.id, admin.id)


def test_guard_refresh_blocks_decided_diffs_and_version_drift(
    client: TestClient, db_session
) -> None:
    admin = _create_user(
        db_session, login_id=f"grb_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, run, _docs = _seed_reviewing_guard_case(db_session, admin.id)
    analysis_id = str(run.id)

    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.db.models.person import PersonProfile

    diff = db_session.execute(
        select(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id == run.id)
    ).scalars().first()
    assert diff is not None
    diff.review_status = "ACCEPTED"
    db_session.add(diff)
    db_session.commit()

    blocked = client.post(
        f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 409

    # Restore PENDING, bump profile version.
    diff.review_status = "PENDING"
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    profile.profile_version = 2
    db_session.add(diff)
    db_session.add(profile)
    db_session.commit()

    version_blocked = client.post(
        f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
        headers={"X-CSRF-Token": csrf},
    )
    assert version_blocked.status_code == 409

    # CONFIRMED / CANCELLED blocked.
    profile.profile_version = 1
    db_session.add(profile)
    run.status = "CONFIRMED"
    db_session.add(run)
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 409
    )
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == run.id)
    ).scalar_one()
    run.status = "CANCELLED"
    db_session.add(run)
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 409
    )

    _cleanup(db_session, person.id, admin.id)


def test_guard_refresh_apply_requires_csrf_and_admin(
    client: TestClient, db_session
) -> None:
    admin = _create_user(
        db_session, login_id=f"gra_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    user = _create_user(
        db_session,
        login_id=f"gru_{uuid.uuid4().hex[:10]}",
        password="Passw0rd!",
        role="USER",
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, run, _docs = _seed_reviewing_guard_case(db_session, admin.id)
    analysis_id = str(run.id)

    assert (
        client.post(f"/api/v1/analyses/{analysis_id}/guard-refresh/apply").status_code
        == 403
    )

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    _login(client, user.login_id, "Passw0rd!")
    assert (
        client.get(
            f"/api/v1/analyses/{analysis_id}/guard-refresh/preview"
        ).status_code
        == 403
    )

    _cleanup(db_session, person.id, admin.id)
    from app.db.models.revision import AuditLog
    from app.db.models.user import AppUser

    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user.id))
    db_session.execute(delete(AppUser).where(AppUser.id == user.id))
    db_session.commit()


def test_guard_refresh_apply_rolls_back_on_persist_failure(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _create_user(
        db_session, login_id=f"grr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, run, _docs = _seed_reviewing_guard_case(db_session, admin.id)
    analysis_id = str(run.id)

    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.modules.analysis import repository as repo_mod

    before_json = dict(run.candidate_json)
    before_diff_ids = set(
        db_session.execute(
            select(AnalysisDiffItem.id).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    )

    original = repo_mod.AnalysisRepository.replace_diffs

    def _boom(self, run_id, specs):  # noqa: ANN001
        raise RuntimeError("forced persist failure")

    monkeypatch.setattr(repo_mod.AnalysisRepository, "replace_diffs", _boom)
    with pytest.raises(RuntimeError, match="forced persist failure"):
        from app.modules.analysis.guard_refresh import apply_guard_refresh

        apply_guard_refresh(db_session, run.id, actor_user_id=admin.id)

    # Restore real method for subsequent checks via a fresh session state.
    monkeypatch.setattr(repo_mod.AnalysisRepository, "replace_diffs", original)
    db_session.expire_all()
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == run.id)
    ).scalar_one()
    assert run.candidate_json == before_json
    assert set(
        db_session.execute(
            select(AnalysisDiffItem.id).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    ) == before_diff_ids

    # API path still works after rollback.
    ok = client.post(
        f"/api/v1/analyses/{analysis_id}/guard-refresh/apply",
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["applied"] is True

    _cleanup(db_session, person.id, admin.id)


def test_guard_refresh_apply_lock_order_matches_confirm(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AnalysisRun must be locked before Person/Profile (confirm-compatible)."""
    admin = _create_user(
        db_session, login_id=f"grl_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, run, _docs = _seed_reviewing_guard_case(db_session, admin.id)

    from app.modules.analysis import repository as repo_mod
    from app.modules.analysis.guard_refresh import apply_guard_refresh

    order: list[str] = []
    original_get_run = repo_mod.AnalysisRepository.get_run
    original_get_person = repo_mod.AnalysisRepository.get_person
    original_get_profile = repo_mod.AnalysisRepository.get_profile

    def _get_run(self, run_id, *, for_update: bool = False):  # noqa: ANN001
        if for_update:
            order.append("AnalysisRun")
        return original_get_run(self, run_id, for_update=for_update)

    def _get_person(self, person_id, *, for_update: bool = False):  # noqa: ANN001
        if for_update:
            order.append("Person")
        return original_get_person(self, person_id, for_update=for_update)

    def _get_profile(self, person_id, *, for_update: bool = False):  # noqa: ANN001
        if for_update:
            order.append("PersonProfile")
        return original_get_profile(self, person_id, for_update=for_update)

    monkeypatch.setattr(repo_mod.AnalysisRepository, "get_run", _get_run)
    monkeypatch.setattr(repo_mod.AnalysisRepository, "get_person", _get_person)
    monkeypatch.setattr(repo_mod.AnalysisRepository, "get_profile", _get_profile)

    result = apply_guard_refresh(db_session, run.id, actor_user_id=admin.id)
    assert result.applied is True
    assert order[:3] == ["AnalysisRun", "Person", "PersonProfile"]
    # No Person/Profile FOR UPDATE before the first AnalysisRun lock.
    assert order.index("AnalysisRun") < order.index("Person")
    assert order.index("Person") < order.index("PersonProfile")

    _cleanup(db_session, person.id, admin.id)


def test_guard_refresh_revalidates_eligibility_after_locks(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status/diff eligibility is checked only after locks are acquired."""
    admin = _create_user(
        db_session, login_id=f"grv_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, run, _docs = _seed_reviewing_guard_case(db_session, admin.id)

    from app.core.exceptions import AnalysisStateConflictError
    from app.db.models.analysis import AnalysisDiffItem
    from app.modules.analysis import repository as repo_mod
    from app.modules.analysis.guard_refresh import apply_guard_refresh

    order: list[str] = []
    original_get_run = repo_mod.AnalysisRepository.get_run

    def _get_run(self, run_id, *, for_update: bool = False):  # noqa: ANN001
        row = original_get_run(self, run_id, for_update=for_update)
        if for_update and row is not None:
            order.append("locked_run")
            # Simulate concurrent review decision after the run lock is held.
            diff = self.db.execute(
                select(AnalysisDiffItem).where(
                    AnalysisDiffItem.analysis_run_id == row.id
                )
            ).scalars().first()
            if diff is not None and diff.review_status == "PENDING":
                diff.review_status = "ACCEPTED"
                self.db.add(diff)
                self.db.flush()
                order.append("mutated_diff")
        return row

    monkeypatch.setattr(repo_mod.AnalysisRepository, "get_run", _get_run)

    with pytest.raises(AnalysisStateConflictError):
        apply_guard_refresh(db_session, run.id, actor_user_id=admin.id)

    assert order[:2] == ["locked_run", "mutated_diff"]
    # Candidate / decided diffs must remain untouched after rollback.
    db_session.expire_all()
    from app.db.models.analysis import AnalysisRun

    stored = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == run.id)
    ).scalar_one()
    assert len(stored.candidate_json.get("employment_history") or []) == 7
    statuses = list(
        db_session.execute(
            select(AnalysisDiffItem.review_status).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        .scalars()
        .all()
    )
    assert statuses and all(status == "PENDING" for status in statuses)

    _cleanup(db_session, person.id, admin.id)
