"""Phase 0 tests for SadaOps control-plane API."""

from __future__ import annotations

import jwt
from fastapi.testclient import TestClient

from api import app as api_module


def test_mint_livekit_token_returns_valid_jwt() -> None:
    """POST /api/token should mint a valid LiveKit JWT containing room grants."""
    client = TestClient(api_module.app)
    resp = client.post(
        "/api/token",
        json={
            "room_name": "ops-room-1",
            "identity": "bejad-op",
            "name": "Bejad",
            "language": "ar",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["room_name"] == "ops-room-1"
    assert body["identity"] == "bejad-op"
    assert body["livekit_url"].startswith("ws://")

    decoded = jwt.decode(
        body["token"],
        api_module.settings.livekit_api_secret,
        algorithms=["HS256"],
    )
    assert decoded["sub"] == "bejad-op"
    assert decoded["video"]["room"] == "ops-room-1"
    assert decoded["video"]["roomJoin"] is True


def test_health_returns_ok_when_dependencies_healthy(monkeypatch) -> None:
    """GET /health returns 200 when all infrastructure checks succeed."""
    monkeypatch.setattr(api_module, "_check_postgres", lambda _url: True)

    async def _always_true(_url: str) -> bool:
        return True

    monkeypatch.setattr(api_module, "_check_http_service", _always_true)
    client = TestClient(api_module.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {
            "postgres": "ok",
            "qdrant": "ok",
            "livekit": "ok",
            "mailpit": "ok",
        },
    }


def test_health_returns_503_when_dependency_down(monkeypatch) -> None:
    """GET /health returns 503 degraded if Postgres is unreachable."""
    monkeypatch.setattr(api_module, "_check_postgres", lambda _url: False)

    async def _always_true(_url: str) -> bool:
        return True

    monkeypatch.setattr(api_module, "_check_http_service", _always_true)
    client = TestClient(api_module.app)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"]["postgres"] == "unreachable"
