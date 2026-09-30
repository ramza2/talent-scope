"""Confidence label coercion + root profile.source_refs migration."""

from __future__ import annotations

from app.ai.prompts.profile_extract_v5 import CANDIDATE_SCHEMA_GUIDE
from app.ai.schemas.profile_candidate import (
    AnalysisMetaCandidate,
    CertificationCandidate,
    EducationCandidate,
    EmploymentCandidate,
    ExpertiseCandidate,
    JobCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SkillCandidate,
    coerce_confidence,
)
from app.modules.analysis.normalize import (
    _migrate_dotted_profile_source_refs,
    normalize_candidate,
)


def test_coerce_confidence_labels_and_numeric() -> None:
    assert coerce_confidence("HIGH") == 0.9
    assert coerce_confidence("high") == 0.9
    assert coerce_confidence(" MEDIUM ") == 0.6
    assert coerce_confidence("LOW") == 0.3
    assert coerce_confidence(0.85) == 0.85
    assert coerce_confidence(1) == 1.0
    assert coerce_confidence(0) == 0.0
    assert coerce_confidence("0.85") == 0.85
    assert coerce_confidence(" 1.0 ") == 1.0
    assert coerce_confidence(None) is None
    assert coerce_confidence("") is None
    assert coerce_confidence("   ") is None


def test_coerce_confidence_rejects_invalid() -> None:
    assert coerce_confidence(1.5) is None
    assert coerce_confidence(-0.1) is None
    assert coerce_confidence("1.5") is None
    assert coerce_confidence("-0.01") is None
    assert coerce_confidence(True) is None
    assert coerce_confidence(False) is None
    assert coerce_confidence({"x": 1}) is None
    assert coerce_confidence([0.9]) is None
    assert coerce_confidence("UNKNOWN") is None
    assert coerce_confidence("very high") is None


def test_candidate_confidence_validators_accept_labels() -> None:
    assert JobCandidate.model_validate({"confidence": "HIGH"}).confidence == 0.9
    assert SkillCandidate.model_validate({"confidence": "MEDIUM"}).confidence == 0.6
    assert ExpertiseCandidate.model_validate({"confidence": "LOW"}).confidence == 0.3
    assert (
        EmploymentCandidate.model_validate({"confidence": "0.75"}).confidence == 0.75
    )
    assert EducationCandidate.model_validate({"confidence": 0.5}).confidence == 0.5
    assert CertificationCandidate.model_validate({"confidence": True}).confidence is None
    assert ProjectCandidate.model_validate({"confidence": "HIGH"}).confidence == 0.9
    assert (
        AnalysisMetaCandidate.model_validate(
            {"overall_confidence": "HIGH"}
        ).overall_confidence
        == 0.9
    )


def test_ops_all_high_confidence_payload_validates() -> None:
    """Operational shape: every confidence field returned as 'HIGH'."""
    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "홍길동"},
        "jobs": [{"raw_value": "AI개발", "job_type": "PRIMARY", "confidence": "HIGH"}],
        "skills": [{"raw_value": "Python", "confidence": "HIGH"}],
        "expertise": [{"raw_value": "RAG", "confidence": "HIGH"}],
        "employment_history": [
            {"company_name": "ABC", "confidence": "HIGH"}
        ],
        "education": [{"school_name": "서울대", "confidence": "HIGH"}],
        "certifications": [{"certification_name": "정보처리", "confidence": "HIGH"}],
        "projects": [{"project_name": "공공 RAG", "confidence": "HIGH"}],
        "summary": {},
        "analysis": {"overall_confidence": "HIGH"},
    }
    doc = ProfileCandidateDocument.model_validate(raw)
    assert doc.jobs[0].confidence == 0.9
    assert doc.skills[0].confidence == 0.9
    assert doc.expertise[0].confidence == 0.9
    assert doc.employment_history[0].confidence == 0.9
    assert doc.education[0].confidence == 0.9
    assert doc.certifications[0].confidence == 0.9
    assert doc.projects[0].confidence == 0.9
    assert doc.analysis.overall_confidence == 0.9


def test_migrate_dotted_profile_source_refs_into_nested() -> None:
    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    cleaned = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "홍길동"},
        "profile.source_refs": {
            "name": [
                {"document_id": doc_id, "page_no": 1, "quote_text": "홍길동"}
            ]
        },
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    _migrate_dotted_profile_source_refs(cleaned)
    assert "profile.source_refs" not in cleaned
    assert cleaned["profile"]["source_refs"]["name"][0]["quote_text"] == "홍길동"

    normalized = normalize_candidate(
        {
            "schema_version": "profile-candidate-v1",
            "profile": {"name": "홍길동"},
            "profile.source_refs": {
                "name": [
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": "홍길동",
                    }
                ]
            },
            "jobs": [],
            "skills": [],
            "expertise": [],
            "employment_history": [],
            "education": [],
            "certifications": [],
            "projects": [],
            "summary": {},
            "analysis": {},
        },
        catalog={},
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): "홍길동 이력서"},
    )
    assert "name" in normalized.profile.source_refs
    assert normalized.profile.source_refs["name"][0].quote_text == "홍길동"


def test_migrate_nested_wins_and_fills_missing_keys() -> None:
    cleaned = {
        "profile": {
            "name": "홍길동",
            "source_refs": {
                "name": [{"document_id": "nested", "quote_text": "nested-name"}],
            },
        },
        "profile.source_refs": {
            "name": [{"document_id": "dotted", "quote_text": "dotted-name"}],
            "phone": [{"document_id": "dotted", "quote_text": "010"}],
        },
    }
    _migrate_dotted_profile_source_refs(cleaned)
    assert "profile.source_refs" not in cleaned
    refs = cleaned["profile"]["source_refs"]
    assert refs["name"][0]["quote_text"] == "nested-name"
    assert refs["phone"][0]["quote_text"] == "010"


def test_migrate_ignores_non_dict_dotted_and_other_dotted_keys() -> None:
    cleaned = {
        "profile": {"name": "A"},
        "profile.source_refs": "not-a-dict",
        "profile.other": {"x": 1},
    }
    _migrate_dotted_profile_source_refs(cleaned)
    assert "profile.source_refs" not in cleaned
    assert "source_refs" not in cleaned["profile"]
    assert cleaned["profile.other"] == {"x": 1}

    no_profile = {"profile.source_refs": {"name": []}}
    _migrate_dotted_profile_source_refs(no_profile)
    assert "profile.source_refs" not in no_profile


def test_v5_guide_confidence_and_nested_source_refs() -> None:
    assert "confidence:number(0..1)" in CANDIDATE_SCHEMA_GUIDE
    assert "overall_confidence:number(0..1)" in CANDIDATE_SCHEMA_GUIDE
    assert "not HIGH/MEDIUM/LOW" in CANDIDATE_SCHEMA_GUIDE
    assert 'Never emit root key "profile.source_refs"' in CANDIDATE_SCHEMA_GUIDE
    assert "nested under profile only" in CANDIDATE_SCHEMA_GUIDE
