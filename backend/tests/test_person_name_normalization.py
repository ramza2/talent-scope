"""Person name normalization — shared helper and call-site coverage."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture()
def redis_prefix() -> str:
    return f"talentscope:test:{uuid.uuid4().hex}"


@pytest.fixture()
def client(redis_prefix: str) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"

    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.main import create_app

    get_settings.cache_clear()
    get_redis.cache_clear()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()


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


def _cleanup_user(db_session, user_id) -> None:
    from app.db.models.revision import AuditLog
    from app.db.models.user import AppUser

    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _cleanup_person(db_session, person_id) -> None:
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    run_ids = list(
        db_session.execute(
            select(AnalysisRun.id).where(AnalysisRun.person_id == person_id)
        )
        .scalars()
        .all()
    )
    if run_ids:
        db_session.execute(
            delete(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id.in_(run_ids))
        )
        db_session.execute(
            delete(AnalysisRunDocument).where(
                AnalysisRunDocument.analysis_run_id.in_(run_ids)
            )
        )
        db_session.execute(
            delete(AuditLog).where(
                AuditLog.target_type == "ANALYSIS_RUN",
                AuditLog.target_id.in_(run_ids),
            )
        )
        db_session.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))
    db_session.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.target_type == "PERSON", AuditLog.target_id == person_id
        )
    )
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db_session.execute(delete(Person).where(Person.id == person_id))
    db_session.commit()


def _login(client: TestClient, login_id: str, password: str = "Secret123!") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


# --- A. shared helper -------------------------------------------------------


def test_normalize_person_name_helpers() -> None:
    from app.core.person_name import (
        is_korean_person_name,
        normalize_person_name,
        person_name_match_key,
    )

    assert normalize_person_name(None) is None
    assert normalize_person_name("   ") is None
    assert normalize_person_name("강 상 원") == "강상원"
    assert normalize_person_name("곽   영   훈") == "곽영훈"
    assert normalize_person_name("홍길동") == "홍길동"
    assert normalize_person_name("  홍길동  ") == "홍길동"
    assert normalize_person_name("남 궁 민") == "남궁민"

    assert normalize_person_name(" John  Smith ") == "John  Smith"
    assert person_name_match_key(" John  Smith ") == "john smith"
    assert person_name_match_key("강 상 원") == "강상원"
    assert person_name_match_key("강상원") == "강상원"

    # Hangul-only compact outside 2–6 → do not force compact
    long_spaced = "가 나 다 라 마 바 사"
    assert normalize_person_name(long_spaced) == long_spaced.strip()
    assert not is_korean_person_name(long_spaced)
    assert is_korean_person_name("강 상 원")
    assert not is_korean_person_name("John Smith")


# --- B. IdentityExtraction --------------------------------------------------


def test_identity_extraction_korean_name() -> None:
    from app.ai.schemas.identity import IdentityExtraction

    identity = IdentityExtraction(name="강 상 원", company="ABC")
    assert identity.name == "강상원"
    assert identity.company == "ABC"


# --- C. ProfileCandidate ----------------------------------------------------


def test_profile_candidate_name_normalization() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidate

    profile = ProfileCandidate(name="강 상 원", phone="010")
    assert profile.name == "강상원"
    assert profile.phone == "010"


# --- F. ResolveIdentity schema ----------------------------------------------


def test_resolve_identity_schema_normalization() -> None:
    from app.modules.documents.schemas import ResolveIdentity

    identity = ResolveIdentity(name="신 규 인", company="Co")
    assert identity.name == "신규인"


# --- H. Analysis Diff name-aware --------------------------------------------


def test_diff_profile_name_match_key_same_and_conflict() -> None:
    from app.ai.schemas.profile_candidate import (
        ProfileCandidate,
        ProfileCandidateDocument,
    )
    from app.modules.analysis.diff_engine import build_diffs

    base = {
        "profile": {"name": "홍 길 동"},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
    }
    same_doc = ProfileCandidateDocument(
        profile=ProfileCandidate(name="홍길동"),
    )
    same_specs = build_diffs(same_doc, base)
    name_same = [s for s in same_specs if s.field_name == "name"]
    assert len(name_same) == 1
    assert name_same[0].change_type == "SAME"

    conflict_doc = ProfileCandidateDocument(
        profile=ProfileCandidate(name="홍길순"),
    )
    conflict_specs = build_diffs(conflict_doc, base)
    name_conflict = [s for s in conflict_specs if s.field_name == "name"]
    assert len(name_conflict) == 1
    assert name_conflict[0].change_type == "CONFLICT"


# --- I. Confirm final guard (coerce path) -----------------------------------


def test_confirm_coerce_name_final_guard() -> None:
    from app.modules.analysis.confirm import ConfirmValidationError, _ConfirmContext

    ctx = object.__new__(_ConfirmContext)
    assert ctx._coerce_profile_value("name", "김 철 수") == "김철수"
    with pytest.raises(ConfirmValidationError, match="비어"):
        ctx._coerce_profile_value("name", "   ")


# --- D/E. Manual create / update --------------------------------------------


def test_people_create_and_update_normalize_korean_name(
    client: TestClient, db_session
) -> None:
    from app.db.models.person import PersonProfile

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"pn_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": "홍 길 동"},
        )
        assert created.status_code == 201, created.text
        data = created.json()["data"]
        person_id = uuid.UUID(data["id"])
        assert data["profile"]["name"] == "홍길동"

        db_session.expire_all()
        profile = db_session.execute(
            select(PersonProfile).where(PersonProfile.person_id == person_id)
        ).scalar_one()
        assert profile.name == "홍길동"

        updated = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 1, "name": " 김 철 수 "},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["profile"]["name"] == "김철수"

        db_session.expire_all()
        profile = db_session.execute(
            select(PersonProfile).where(PersonProfile.person_id == person_id)
        ).scalar_one()
        assert profile.name == "김철수"
        assert profile.profile_version == 2
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


# --- G. Duplicate Matcher + legacy spaced DB name ---------------------------


def test_duplicate_matcher_legacy_spaced_korean_name(db_session) -> None:
    from app.ai.schemas.identity import IdentityExtraction
    from app.db.models.person import Person, PersonProfile
    from app.modules.upload_identification.duplicate_matcher import DuplicateMatcher

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"dm_{suffix}", password="Secret123!")
    person_id = None
    try:
        person = Person(status="ACTIVE", created_by=admin.id)
        db_session.add(person)
        db_session.flush()
        # Bypass API so legacy spaced name is stored as-is.
        profile = PersonProfile(
            person_id=person.id,
            name="홍 길 동",
            profile_version=1,
        )
        db_session.add(profile)
        db_session.commit()
        person_id = person.id

        identity = IdentityExtraction(name="홍길동")
        cands = DuplicateMatcher(db_session).find_candidates(identity)
        matched = [c for c in cands if c.person_id == person_id]
        assert matched, "legacy spaced name must be loaded and scored"
        assert "NAME_EXACT" in matched[0].match_reasons
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


# --- I+. Confirm MODIFIED decided_value path --------------------------------


def test_confirm_modified_name_decided_value_canonicalizes(db_session) -> None:
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.analysis.confirm import confirm_analysis_run
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"cf_{suffix}", password="Secret123!")
    person_id = None
    try:
        person = Person(status="ACTIVE", created_by=admin.id)
        db_session.add(person)
        db_session.flush()
        profile = PersonProfile(
            person_id=person.id,
            name="기존이름",
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
                created_by=admin.id,
            )
        )
        run = AnalysisRun(
            person_id=person.id,
            status="REVIEWING",
            base_profile_version=1,
            candidate_json={
                "schema_version": "profile-candidate-v1",
                "profile": {"name": "후보"},
            },
        )
        db_session.add(run)
        db_session.flush()
        # decided_value bypasses ProfileCandidate validator — spaced Korean name.
        db_session.add(
            AnalysisDiffItem(
                analysis_run_id=run.id,
                entity_type="PROFILE",
                candidate_path="profile.name",
                field_name="name",
                change_type="UPDATE",
                old_value="기존이름",
                new_value="후보",
                review_status="MODIFIED",
                decided_value="김 철 수",
            )
        )
        db_session.commit()
        person_id = person.id

        confirm_analysis_run(
            db_session,
            analysis_id=run.id,
            expected_profile_version=1,
            actor_user_id=admin.id,
        )
        db_session.commit()

        db_session.expire_all()
        profile = db_session.execute(
            select(PersonProfile).where(PersonProfile.person_id == person_id)
        ).scalar_one()
        assert profile.name == "김철수"
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)
