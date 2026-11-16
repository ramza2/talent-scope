"""Strict v30-only identity-linking tests; no DB, model, or network."""

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.project_source_first_diff_guard_v30 import (
    _exact_identity,
    _responsibilities_formatting_equivalent,
    build_source_first_review_safe_diffs,
)


ID_1 = "00000000-0000-0000-0000-000000000111"
ID_2 = "00000000-0000-0000-0000-000000000222"


def _project(name, customer="비공개 고객기관", **fields):
    row = {
        "project_name": name, "customer_name": customer,
        "start_date": "2020.02", "end_date": "2020.04",
    }
    row.update(fields)
    return row


def _snapshot(name, customer="비공개고객기관", *, ident=ID_1, **fields):
    return {"id": ident, **_project(name, customer, **fields)}


def _run(name, snapshots, *, customer="비공개 고객기관", **fields):
    candidate = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자"},
        "projects": [_project(name, customer, **fields)],
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


def test_format_only_responsibilities_conflict_becomes_same_for_safe_match():
    confirmed = "수행역할: PL / TA"
    candidate_role = "수행역할:　PL／TA"  # NFKC + whitespace-only difference
    assert _responsibilities_formatting_equivalent(confirmed, candidate_role)
    result = _run(
        "비공개 플랫폼 구축 사업",
        [_snapshot("비공개플랫폼구축사업", responsibilities=confirmed)],
        responsibilities=candidate_role,
    )
    assert result.variant_review_indices == []
    assert result.collision_review_indices == []
    role_specs = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name == "responsibilities"
    ]
    assert len(role_specs) == 1
    assert role_specs[0].change_type == "SAME"
    assert role_specs[0].old_value == confirmed
    assert str(role_specs[0].existing_target_id) == ID_1
    # Source project remains represented (root or field paths).
    assert any(
        (s.candidate_path or "").startswith("projects[0]")
        for s in result.specs if s.entity_type == "PROJECT"
    )


def test_real_responsibilities_content_difference_stays_conflict():
    result = _run(
        "비공개 플랫폼 구축 사업",
        [_snapshot("비공개플랫폼구축사업", responsibilities="수행역할: PL")],
        responsibilities="수행역할: PM",
    )
    role_specs = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name == "responsibilities"
    ]
    assert len(role_specs) == 1
    assert role_specs[0].change_type == "CONFLICT"
    assert role_specs[0].old_value == "수행역할: PL"
    assert role_specs[0].new_value == "수행역할: PM"


def test_format_only_responsibilities_not_softened_on_identity_ambiguity():
    confirmed = "수행역할: PL / TA"
    candidate_role = "수행역할: PL/TA"
    # Exact raw customer so legacy Diff links one target; normalized
    # confirmed collision still forces root REVIEW (no field SAME).
    result = _run(
        "비공개플랫폼구축사업",
        [
            _snapshot(
                "비공개플랫폼구축사업",
                "비공개고객기관",
                ident=ID_1,
                responsibilities=confirmed,
            ),
            _snapshot(
                "비공개 플랫폼구축사업",
                "비공개고객기관",
                ident=ID_2,
                responsibilities=confirmed,
            ),
        ],
        customer="비공개고객기관",
        responsibilities=candidate_role,
    )
    assert result.variant_review_indices == [0]
    roots = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name is None
    ]
    assert len(roots) == 1
    assert roots[0].change_type == "REVIEW"
    assert roots[0].existing_target_id is None
    # Ambiguous rewrite drops field CONFLICT rather than auto-SAME.
    assert not any(
        s.field_name == "responsibilities" for s in result.specs
    )


def test_format_only_responsibilities_not_softened_on_duplicate_target_link():
    confirmed = "수행역할: PL / TA"
    candidate = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자"},
        "projects": [
            _project(
                "비공개플랫폼구축사업",
                "비공개고객기관",
                responsibilities="수행역할: PL/TA",
            ),
            _project(
                "비공개 플랫폼 구축 사업",
                "비공개 고객기관",
                responsibilities="수행역할:　PL／TA",
            ),
        ],
    })
    result = build_source_first_review_safe_diffs(
        candidate,
        {
            "projects": [
                _snapshot(
                    "비공개플랫폼구축사업",
                    "비공개고객기관",
                    responsibilities=confirmed,
                )
            ]
        },
    )
    assert result.collision_review_indices == [0, 1]
    assert not any(
        s.field_name == "responsibilities" and s.change_type == "SAME"
        for s in result.specs
    )
    roots = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name is None
    ]
    assert {s.change_type for s in roots} == {"REVIEW"}
    assert all(s.existing_target_id is None for s in roots)


def test_whitespace_only_responsibilities_preserves_confirmed_old_value():
    confirmed = "사업관리 및 품질보증"
    candidate_role = "사업관리  및\t품질보증"
    result = _run(
        "비공개플랫폼구축사업",
        [_snapshot(
            "비공개플랫폼구축사업",
            "비공개고객기관",
            responsibilities=confirmed,
        )],
        customer="비공개고객기관",
        responsibilities=candidate_role,
    )
    role = next(
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name == "responsibilities"
    )
    assert role.change_type == "SAME"
    assert role.old_value == confirmed
    # Candidate formatting may remain visible, but Confirmed text is unchanged.
    assert role.new_value == candidate_role


def test_non_responsibility_field_conflicts_are_not_auto_same():
    result = _run(
        "비공개 플랫폼 구축 사업",
        [_snapshot(
            "비공개플랫폼구축사업",
            project_summary="기존 요약",
            responsibilities="수행역할: PL",
        )],
        project_summary="다른 요약",
        responsibilities="수행역할: PL",
    )
    summary = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name == "project_summary"
    ]
    assert summary and all(s.change_type == "CONFLICT" for s in summary)
