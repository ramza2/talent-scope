"""Frontend DEFERRED status wiring (no vitest in this repo)."""

from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def test_frontend_deferred_status_type_and_polling() -> None:
    analyses_api = _read("api/analyses.ts")
    assert "| 'DEFERRED'" in analyses_api or '| "DEFERRED"' in analyses_api
    assert "status === 'DEFERRED'" in analyses_api
    assert "isActiveAnalysisStatus" in analyses_api


def test_frontend_deferred_list_filter_options() -> None:
    for rel in ("pages/AnalysesPage.tsx", "pages/AdminAnalysesPage.tsx"):
        src = _read(rel)
        assert "value: 'DEFERRED'" in src
        assert "isActiveAnalysisStatus" in src


def test_frontend_deferred_detail_message() -> None:
    src = _read("pages/AnalysisDetailPage.tsx")
    assert "앞선 AI 분석 완료를 기다리고 있습니다." in src
    assert "status === 'DEFERRED'" in src


def test_frontend_documents_tab_disables_on_deferred() -> None:
    src = _read("pages/people/DocumentsTab.tsx")
    assert "a.status === 'DEFERRED'" in src
    assert "hasActiveAnalysis" in src
