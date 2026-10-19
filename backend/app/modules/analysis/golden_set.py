"""Golden Set accuracy evaluation for profile-candidate-v1 AnalysisRun output.

Pure evaluation helpers only: no database writes and no AI calls.
Golden expectations are intentionally partial; an omitted field is not scored,
while an explicit empty list asserts that no values are expected for that field.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any
from uuid import UUID

GOLDEN_SCHEMA_VERSION = "talentscope-analysis-golden-v1"
ROOT_CODE_KEYS: tuple[str, ...] = ("jobs", "skills", "expertise")
PROJECT_RELATION_KEYS: tuple[str, ...] = (
    "jobs",
    "skills",
    "expertise",
    "business_domains",
    "customer_types",
)


@dataclass(frozen=True)
class Score:
    expected_count: int = 0
    actual_count: int = 0
    matched_count: int = 0
    false_positive_count: int = 0
    false_negative_count: int = 0
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceScore:
    expected_count: int = 0
    with_source_refs_count: int = 0
    coverage_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseEvaluation:
    case_id: str
    person_id: str
    analysis_run_id: str | None
    person_name: str | None = None
    run_status: str | None = None
    root_scores: dict[str, Score] = field(default_factory=dict)
    project_score: Score = field(default_factory=Score)
    project_relation_scores: dict[str, Score] = field(default_factory=dict)
    evidence: EvidenceScore = field(default_factory=EvidenceScore)
    missing: dict[str, list[str]] = field(default_factory=dict)
    unexpected: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "person_id": self.person_id,
            "analysis_run_id": self.analysis_run_id,
            "person_name": self.person_name,
            "run_status": self.run_status,
            "root_scores": {k: v.to_dict() for k, v in self.root_scores.items()},
            "project_score": self.project_score.to_dict(),
            "project_relation_scores": {
                k: v.to_dict() for k, v in self.project_relation_scores.items()
            },
            "evidence": self.evidence.to_dict(),
            "missing": {k: list(v) for k, v in self.missing.items()},
            "unexpected": {k: list(v) for k, v in self.unexpected.items()},
            "notes": list(self.notes),
        }


@dataclass
class AggregateEvaluation:
    case_count: int = 0
    root_scores: dict[str, Score] = field(default_factory=dict)
    project_score: Score = field(default_factory=Score)
    project_relation_scores: dict[str, Score] = field(default_factory=dict)
    evidence: EvidenceScore = field(default_factory=EvidenceScore)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "root_scores": {k: v.to_dict() for k, v in self.root_scores.items()},
            "project_score": self.project_score.to_dict(),
            "project_relation_scores": {
                k: v.to_dict() for k, v in self.project_relation_scores.items()
            },
            "evidence": self.evidence.to_dict(),
        }


def _pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round((num / den) * 100.0, 2)


def _score(expected_count: int, actual_count: int, matched_count: int) -> Score:
    fp = max(0, actual_count - matched_count)
    fn = max(0, expected_count - matched_count)
    precision = _pct(matched_count, actual_count)
    recall = _pct(matched_count, expected_count)
    if precision is None or recall is None or precision + recall == 0:
        f1 = None if precision is None or recall is None else 0.0
    else:
        f1 = round((2 * precision * recall) / (precision + recall), 2)
    return Score(
        expected_count=expected_count,
        actual_count=actual_count,
        matched_count=matched_count,
        false_positive_count=fp,
        false_negative_count=fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _sum_scores(scores: list[Score]) -> Score:
    return _score(
        sum(s.expected_count for s in scores),
        sum(s.actual_count for s in scores),
        sum(s.matched_count for s in scores),
    )


def _norm_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    return text or None


def _codes(items: Any) -> set[str]:
    if not isinstance(items, list):
        return set()
    out: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        code = _norm_code(item.get("code"))
        if code:
            out.add(code)
    return out


def _items_by_code(items: Any) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        code = _norm_code(item.get("code"))
        if code:
            out.setdefault(code, []).append(item)
    return out


def _has_source_refs(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    refs = item.get("source_refs")
    return isinstance(refs, list) and any(isinstance(ref, dict) for ref in refs)


def _fold_ws(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value.strip() if not ch.isspace()).casefold()
    return text or None


_DATE_RE = re.compile(r"^(\d{4})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?$")


def _date_parts(value: Any) -> tuple[int, int | None, int | None] | None:
    if not isinstance(value, str):
        return None
    text = value.strip().split("T", 1)[0]
    match = _DATE_RE.fullmatch(text)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not 1 <= day <= 31:
        return None
    return year, month, day


def _expected_date_matches(expected: Any, actual: Any) -> bool:
    if expected is None:
        return True
    ep = _date_parts(expected)
    ap = _date_parts(actual)
    if ep is None or ap is None:
        return str(expected).strip() == str(actual or "").strip()
    ey, em, ed = ep
    ay, am, ad = ap
    if ey != ay:
        return False
    if em is None:
        return True
    if em != am:
        return False
    if ed is None:
        return True
    return ed == ad


def _project_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if _fold_ws(expected.get("project_name")) != _fold_ws(actual.get("project_name")):
        return False
    if "customer_name" in expected and expected.get("customer_name") is not None:
        if _fold_ws(expected.get("customer_name")) != _fold_ws(actual.get("customer_name")):
            return False
    for key in ("start_date", "end_date"):
        if key in expected and expected.get(key) is not None:
            if not _expected_date_matches(expected.get(key), actual.get(key)):
                return False
    return True


def _project_label(project: dict[str, Any]) -> str:
    name = str(project.get("project_name") or "?").strip()
    start = str(project.get("start_date") or "?").strip()
    end = str(project.get("end_date") or "?").strip()
    return f"{name} [{start}~{end}]"


def _validate_code_list(value: Any, *, path: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    normalized: list[str] = []
    for idx, raw in enumerate(value):
        code = _norm_code(raw)
        if code is None:
            raise ValueError(f"{path}[{idx}] must be a non-empty code string")
        normalized.append(code)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{path} must not contain duplicate codes")


def validate_golden_set(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("golden set root must be an object")
    if payload.get("schema_version") != GOLDEN_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {GOLDEN_SCHEMA_VERSION!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        path = f"cases[{index}]"
        if not isinstance(case, dict):
            raise ValueError(f"{path} must be an object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{path}.case_id must be a non-empty string")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        try:
            UUID(str(case.get("person_id")))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}.person_id must be a UUID") from exc
        run_id = case.get("analysis_run_id")
        if run_id is not None:
            try:
                UUID(str(run_id))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}.analysis_run_id must be a UUID or null") from exc
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"{path}.expected must be an object")
        for key in ROOT_CODE_KEYS:
            if key in expected:
                _validate_code_list(expected[key], path=f"{path}.expected.{key}")
        if "projects" in expected:
            projects = expected["projects"]
            if not isinstance(projects, list):
                raise ValueError(f"{path}.expected.projects must be a list")
            for pidx, project in enumerate(projects):
                ppath = f"{path}.expected.projects[{pidx}]"
                if not isinstance(project, dict):
                    raise ValueError(f"{ppath} must be an object")
                if not _fold_ws(project.get("project_name")):
                    raise ValueError(f"{ppath}.project_name must be non-empty")
                for key in PROJECT_RELATION_KEYS:
                    if key in project:
                        _validate_code_list(project[key], path=f"{ppath}.{key}")
    return payload


def _compare_code_key(
    expected_codes_raw: list[str],
    actual_items: Any,
) -> tuple[Score, list[str], list[str], int, int]:
    expected = {_norm_code(code) for code in expected_codes_raw}
    expected.discard(None)
    actual = _codes(actual_items)
    matched = expected & actual
    score = _score(len(expected), len(actual), len(matched))
    item_map = _items_by_code(actual_items)
    evidence_expected = len(expected)
    evidence_with_refs = sum(
        1
        for code in expected
        if any(_has_source_refs(item) for item in item_map.get(code, []))
    )
    return (
        score,
        sorted(expected - actual),
        sorted(actual - expected),
        evidence_expected,
        evidence_with_refs,
    )


def evaluate_candidate(
    *,
    case: dict[str, Any],
    candidate_json: Any,
    analysis_run_id: str | None,
    person_name: str | None = None,
    run_status: str | None = None,
) -> CaseEvaluation:
    candidate = candidate_json if isinstance(candidate_json, dict) else {}
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    result = CaseEvaluation(
        case_id=str(case.get("case_id")),
        person_id=str(case.get("person_id")),
        analysis_run_id=analysis_run_id,
        person_name=person_name,
        run_status=run_status,
    )
    evidence_expected = 0
    evidence_with_refs = 0

    for key in ROOT_CODE_KEYS:
        if key not in expected:
            continue
        score, missing, unexpected, ev_expected, ev_refs = _compare_code_key(
            expected[key], candidate.get(key)
        )
        result.root_scores[key] = score
        if missing:
            result.missing[key] = missing
        if unexpected:
            result.unexpected[key] = unexpected
        evidence_expected += ev_expected
        evidence_with_refs += ev_refs

    if "projects" in expected:
        expected_projects = [
            p for p in expected.get("projects", []) if isinstance(p, dict)
        ]
        actual_projects = (
            [p for p in candidate.get("projects", []) if isinstance(p, dict)]
            if isinstance(candidate.get("projects"), list)
            else []
        )
        unmatched_actual = set(range(len(actual_projects)))
        matched_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        missing_projects: list[str] = []
        ambiguous_projects: list[str] = []

        for exp_project in expected_projects:
            hits = [
                idx
                for idx in sorted(unmatched_actual)
                if _project_matches(exp_project, actual_projects[idx])
            ]
            if len(hits) == 1:
                idx = hits[0]
                unmatched_actual.remove(idx)
                matched_pairs.append((exp_project, actual_projects[idx]))
            else:
                missing_projects.append(_project_label(exp_project))
                if len(hits) > 1:
                    ambiguous_projects.append(_project_label(exp_project))

        result.project_score = _score(
            len(expected_projects), len(actual_projects), len(matched_pairs)
        )
        if missing_projects:
            result.missing["projects"] = missing_projects
        if unmatched_actual:
            result.unexpected["projects"] = [
                _project_label(actual_projects[idx]) for idx in sorted(unmatched_actual)
            ]
        if ambiguous_projects:
            result.notes.append(
                "ambiguous project matches: " + "; ".join(ambiguous_projects)
            )

        evidence_expected += len(expected_projects)
        evidence_with_refs += sum(
            1 for _, actual in matched_pairs if _has_source_refs(actual)
        )
        for exp_project in expected_projects:
            for key in PROJECT_RELATION_KEYS:
                if key in exp_project:
                    evidence_expected += len(
                        {_norm_code(code) for code in exp_project[key]}
                    )

        rel_scores: dict[str, list[Score]] = {key: [] for key in PROJECT_RELATION_KEYS}
        rel_missing: dict[str, list[str]] = {key: [] for key in PROJECT_RELATION_KEYS}
        rel_unexpected: dict[str, list[str]] = {key: [] for key in PROJECT_RELATION_KEYS}
        for exp_project, actual_project in matched_pairs:
            label = _project_label(exp_project)
            for key in PROJECT_RELATION_KEYS:
                if key not in exp_project:
                    continue
                score, missing, unexpected, _ev_expected, ev_refs = _compare_code_key(
                    exp_project[key], actual_project.get(key)
                )
                rel_scores[key].append(score)
                rel_missing[key].extend(f"{label}: {code}" for code in missing)
                rel_unexpected[key].extend(f"{label}: {code}" for code in unexpected)
                evidence_with_refs += ev_refs
        for key, scores in rel_scores.items():
            if not scores:
                continue
            result.project_relation_scores[key] = _sum_scores(scores)
            if rel_missing[key]:
                result.missing[f"project_{key}"] = rel_missing[key]
            if rel_unexpected[key]:
                result.unexpected[f"project_{key}"] = rel_unexpected[key]

    result.evidence = EvidenceScore(
        expected_count=evidence_expected,
        with_source_refs_count=evidence_with_refs,
        coverage_pct=_pct(evidence_with_refs, evidence_expected),
    )
    return result


def aggregate_evaluations(cases: list[CaseEvaluation]) -> AggregateEvaluation:
    root: dict[str, Score] = {}
    for key in ROOT_CODE_KEYS:
        scores = [case.root_scores[key] for case in cases if key in case.root_scores]
        if scores:
            root[key] = _sum_scores(scores)
    project_scores = [
        case.project_score
        for case in cases
        if case.project_score.expected_count or case.project_score.actual_count
    ]
    project = _sum_scores(project_scores) if project_scores else Score()
    rel: dict[str, Score] = {}
    for key in PROJECT_RELATION_KEYS:
        scores = [
            case.project_relation_scores[key]
            for case in cases
            if key in case.project_relation_scores
        ]
        if scores:
            rel[key] = _sum_scores(scores)
    ev_expected = sum(case.evidence.expected_count for case in cases)
    ev_refs = sum(case.evidence.with_source_refs_count for case in cases)
    return AggregateEvaluation(
        case_count=len(cases),
        root_scores=root,
        project_score=project,
        project_relation_scores=rel,
        evidence=EvidenceScore(
            expected_count=ev_expected,
            with_source_refs_count=ev_refs,
            coverage_pct=_pct(ev_refs, ev_expected),
        ),
    )
