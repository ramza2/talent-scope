"""SESSION_COOKIE_SECURE override preserves historical cookie_secure defaults."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings


def test_cookie_secure_unset_matches_historical_defaults() -> None:
    assert Settings(app_env="production", session_cookie_secure=None).cookie_secure is True
    assert Settings(app_env="development", session_cookie_secure=None).cookie_secure is False
    assert Settings(app_env="test", session_cookie_secure=None).cookie_secure is False


def test_cookie_secure_env_override() -> None:
    assert (
        Settings(app_env="production", session_cookie_secure=False).cookie_secure
        is False
    )
    assert (
        Settings(app_env="development", session_cookie_secure=True).cookie_secure
        is True
    )


def test_server_compose_frontend_only_bind_no_traefik() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "docker-compose.server.yml").read_text(encoding="utf-8")
    assert "traefik." not in text
    assert "TRAEFIK_NETWORK" not in text
    assert "proxy:" not in text
    assert "${TALENTSCOPE_BIND_IP}:${TALENTSCOPE_PORT}:80" in text
    # Frontend must share the private network so nginx can reach api:8000.
    assert "networks:\n      - internal" in text
    # Host ports must not be declared for backend services.
    for service in ("api:", "worker:", "postgres:", "redis:", "minio:"):
        assert service in text
    assert text.count("ports:") == 1
