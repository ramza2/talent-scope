"""Strict v30-only identity-linking tests; no DB, model, or network."""

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.project_source_first_diff_guard_v30 import (
    _exact_identity,
    build_source_first_review_safe_diffs,
)


ID_1 = "00000000-0000-0000-0000-000000000111"
ID_2 = "00000000-0000-0000-0000-000000000222"


def _project(name, customer="비공개 고객기관"):
    return {
        "project_name": name, "customer_name": customer,
        "start_date": "2020.02", "end_date": "2020.04",
    }


def _snapshot(name, customer="비공개고객기관", *, ident=ID_1):
    return {"id": ident, **_project(name, customer)}


def _run(name, snapshots, *, customer="비공개 고객기관"):
    candidate = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자"},
        "projects": [_project(name, customer)],
    })
    return build_source_first_review_safe_diffs(
        candidate, {"projects": snapshots},
    )


def test_whitespace_only_identity_links_existing_project():
    row = _snapshot("비공개플랫폼구축사업")
    assert _exact_identity(
        ProfileCandidateDocument.model_validate({
            "profile": {"name": "시험사용자"},
            "projects": [_project("비공개 플랫폼 구축 사업")],
        }).projects[0], row,
    )
    result = _run("비공개 플랫폼 구축 사업", [row])
    assert result.variant_review_indices == []
    assert result.collision_review_indices == []
    project_specs = [s for s in result.specs if s.entity_type == "PROJECT"]
    assert project_specs
    assert any(str(s.existing_target_id) == ID_1 for s in project_specs)
    assert not any(
        s.change_type == "REVIEW" and s.existing_target_id is None
        for s in project_specs if s.field_name is None
    )


def test_nfkc_and_unicode_whitespace_normalizes_identity_only():
    candidate = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자"},
        "projects": [_project("ＡＢＣ　구축")],
    }).projects[0]
    assert _exact_identity(candidate, _snapshot("ABC구축"))
    # Unicode compatibility must not also erase punctuation.
    assert not _exact_identity(candidate, _snapshot("ABC-구축"))


def test_punctuation_difference_never_auto_links():
    row = _snapshot("비공개플랫폼구축사업")
    result = _run("비공개·플랫폼구축사업", [row])
    assert result.variant_review_indices == [0]
    roots = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name is None
    ]
    assert len(roots) == 1
    assert roots[0].change_type == "REVIEW"
    assert roots[0].existing_target_id is None


def test_whitespace_equivalence_collision_with_confirmed_records_requires_review():
    result = _run("비공개 플랫폼 구축 사업", [
        _snapshot("비공개플랫폼구축사업", ident=ID_1),
        _snapshot("비공개 플랫폼구축사업", ident=ID_2),
    ])
    roots = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name is None
    ]
    assert len(roots) == 1
    assert roots[0].change_type == "REVIEW"
    assert roots[0].existing_target_id is None


def test_exact_raw_match_does_not_bypass_confirmed_normalization_collision():
    result = _run(
        "비공개플랫폼구축사업",
        [
            _snapshot("비공개플랫폼구축사업", ident=ID_1),
            _snapshot("비공개 플랫폼구축사업", ident=ID_2),
        ],
        customer="비공개고객기관",
    )
    assert result.variant_review_indices == [0]
    roots = [
        spec for spec in result.specs
        if spec.entity_type == "PROJECT" and spec.field_name is None
    ]
    assert len(roots) == 1
    assert roots[0].change_type == "REVIEW"
    assert roots[0].existing_target_id is None


def test_different_date_remains_nonidentity():
    candidate = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자"},
        "projects": [_project("비공개 플랫폼 구축 사업")],
    }).projects[0]
    row = {**_snapshot("비공개플랫폼구축사업"), "end_date": "2020.05"}
    assert not _exact_identity(candidate, row)


def test_non_project_diff_fields_unmodified():
    candidate = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자", "current_title": "선임"},
        "projects": [_project("비공개 플랫폼 구축 사업")],
    })
    result = build_source_first_review_safe_diffs(
        candidate,
        {"profile": {"name": "시험사용자", "current_title": "선임"},
         "projects": [_snapshot("비공개플랫폼구축사업")]},
    )
    assert result.variant_review_indices == []
    assert any(s.entity_type == "PROFILE" for s in result.specs)
