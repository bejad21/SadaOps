"""Tests for Google Workspace OAuth FastAPI integration endpoints."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import api.app as app_mod
from mcp_servers.workspace_mcp.google_client import GoogleOAuthConfig, GoogleWorkspaceClient


def test_google_integration_endpoints_status_auth_callback_disconnect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token_path = tmp_path / "tok.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(
                200,
                json={"access_token": "acc-1", "refresh_token": "rtok-1", "expires_in": 3600},
            )
        if str(request.url).startswith("https://www.googleapis.com/oauth2/v2/userinfo"):
            return httpx.Response(200, json={"email": "operator@gmail.com"})
        return httpx.Response(404)

    unconfigured = GoogleWorkspaceClient(
        GoogleOAuthConfig(
            "",
            "",
            "http://127.0.0.1:8000/api/integrations/google/callback",
            "",
            token_path,
        )
    )
    monkeypatch.setattr(app_mod, "_get_google_client", lambda: unconfigured)
    client = TestClient(app_mod.app)

    st_unconf = client.get("/api/integrations/google/status")
    assert st_unconf.status_code == 200
    assert st_unconf.json()["configured"] is False
    assert st_unconf.json()["connected"] is False

    bad_auth = client.get("/api/integrations/google/auth-url")
    assert bad_auth.status_code == 400

    configured = GoogleWorkspaceClient(
        GoogleOAuthConfig(
            "cid-1",
            "csec-1",
            "http://127.0.0.1:8000/api/integrations/google/callback",
            "",
            token_path,
        ),
        async_transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(app_mod, "_get_google_client", lambda: configured)

    auth_res = client.get("/api/integrations/google/auth-url")
    assert auth_res.status_code == 200
    state = auth_res.json()["state"]
    assert "accounts.google.com" in auth_res.json()["url"]

    bad_cb = client.get("/api/integrations/google/callback?code=abc&state=wrong-state")
    assert bad_cb.status_code == 400

    ok_cb = client.get(f"/api/integrations/google/callback?code=valid-code&state={state}")
    assert ok_cb.status_code == 200
    assert "operator@gmail.com" in ok_cb.text
    assert "sadaops-google-connected" in ok_cb.text

    st_conn = client.get("/api/integrations/google/status")
    assert st_conn.status_code == 200
    assert st_conn.json()["connected"] is True
    assert st_conn.json()["email"] == "operator@gmail.com"

    disc = client.post("/api/integrations/google/disconnect")
    assert disc.status_code == 200
    assert disc.json()["disconnected"] is True
    assert client.get("/api/integrations/google/status").json()["connected"] is False
