"""FastAPI control plane for SadaOps (health checks, LiveKit token minting, HITL API)."""

from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import time
from datetime import timedelta
from typing import Any, Literal

import httpx
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from livekit.api import AccessToken, LiveKitAPI, SendDataRequest, VideoGrants
from pydantic import BaseModel, Field

from agent.safety.hitl_gate import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
    HITLGate,
    PayloadTamperedError,
)
from agent.safety.prompt_guard import WRITE_TOOLS, PolicyViolationError
from api.config import get_settings
from mcp_servers.github_mcp.adapter import CreateIssueInput, build_github_adapter
from mcp_servers.workspace_mcp.google_client import (
    GoogleNotConfiguredError,
    GoogleWorkspaceClient,
)
from mcp_servers.workspace_mcp.service import (
    CalendarConflictError,
    CalendarEventInput,
    DeleteCalendarEventInput,
    SendEmailInput,
    UpdateCalendarEventInput,
    WorkspaceService,
)

load_dotenv()
settings = get_settings()
hitl_gate = HITLGate(settings.database_url)
_google_oauth_states: dict[str, float] = {}


def _get_google_client() -> GoogleWorkspaceClient:
    return GoogleWorkspaceClient()

app = FastAPI(
    title="SadaOps Control Plane",
    version="0.1.0",
    description="Token minting, health probes, and HITL control endpoints for SadaOps.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TokenRequest(BaseModel):
    """Request payload for minting a LiveKit room access token."""

    room_name: str = Field(default="sadaops-main", min_length=1, max_length=64)
    identity: str = Field(default="operator-1", min_length=1, max_length=64)
    name: str | None = Field(default="Bejad", max_length=64)
    language: Literal["auto", "en", "ar"] = "auto"


class TokenResponse(BaseModel):
    """LiveKit JWT response returned to the WebRTC frontend."""

    token: str
    livekit_url: str
    room_name: str
    identity: str


def _check_postgres(database_url: str) -> bool:
    """Verify PostgreSQL accepts connections and responds to SELECT 1."""
    try:
        with psycopg.connect(database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
    except Exception:
        return False


async def _check_http_service(url: str) -> bool:
    """Verify an HTTP infrastructure dependency responds cleanly."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except Exception:
        return False


@app.get("/health")
async def health() -> dict[str, object]:
    """Check readiness of Postgres, Qdrant, LiveKit, and Mailpit."""
    pg_ok, qdrant_ok, livekit_ok, mailpit_ok = await asyncio.gather(
        asyncio.to_thread(_check_postgres, settings.database_url),
        _check_http_service(f"{settings.qdrant_url}/collections"),
        _check_http_service(f"{settings.livekit_http_url}/"),
        _check_http_service(f"{settings.mailpit_api_url}/api/v1/info"),
    )

    checks = {
        "postgres": "ok" if pg_ok else "unreachable",
        "qdrant": "ok" if qdrant_ok else "unreachable",
        "livekit": "ok" if livekit_ok else "unreachable",
        "mailpit": "ok" if mailpit_ok else "unreachable",
    }
    all_ok = pg_ok and qdrant_ok and livekit_ok and mailpit_ok
    if not all_ok:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "checks": checks},
        )
    return {"status": "ok", "checks": checks}


@app.post("/api/token", response_model=TokenResponse)
async def mint_livekit_token(req: TokenRequest) -> TokenResponse:
    """Mint a short-lived LiveKit room JWT for a WebRTC operator session."""
    grant = VideoGrants(
        room_join=True,
        room=req.room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(req.identity)
        .with_name(req.name or req.identity)
        .with_metadata(json.dumps({"language": req.language}))
        .with_grants(grant)
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )
    return TokenResponse(
        token=token,
        livekit_url=settings.livekit_url,
        room_name=req.room_name,
        identity=req.identity,
    )


class ProposeActionRequest(BaseModel):
    """Request payload for creating a parameter-locked HITL proposal."""

    session_id: str = Field(default="sadaops-main", min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    ttl_seconds: int = Field(default=120, ge=0, le=3600)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ApproveActionRequest(BaseModel):
    """Request payload for approving and executing a locked HITL action."""

    action_id: str = Field(min_length=1, max_length=64)
    payload_hash: str = Field(min_length=8, max_length=64)


class RejectActionRequest(BaseModel):
    """Request payload for rejecting a locked HITL action."""

    action_id: str = Field(min_length=1, max_length=64)


class UpdateActionRequest(BaseModel):
    """Request payload for inline editing of a pending HITL action and re-locking SHA-256."""

    action_id: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


def _validate_tool_payload_shape(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate edited payload fields against the tool's Pydantic schema before re-locking."""
    if tool_name == "send_email":
        return SendEmailInput(**payload).model_dump(mode="json")
    if tool_name == "create_calendar_event":
        return CalendarEventInput(**payload).model_dump(mode="json")
    if tool_name == "update_calendar_event":
        return UpdateCalendarEventInput(**payload).model_dump(mode="json")
    if tool_name == "delete_calendar_event":
        return DeleteCalendarEventInput(**payload).model_dump(mode="json")
    if tool_name == "create_issue":
        return CreateIssueInput(**payload).model_dump(mode="json")
    raise ValueError(f"Unsupported locked write tool: {tool_name}")


async def execute_locked_write_tool(tool_name: str, payload: dict[str, Any]) -> Any:
    """Execute a verified write tool using the locked payload from PostgreSQL."""
    if tool_name == "send_email":
        ws = WorkspaceService(settings.database_url)
        return await asyncio.to_thread(ws.send_email, SendEmailInput(**payload))
    if tool_name == "create_calendar_event":
        ws = WorkspaceService(settings.database_url)
        return await asyncio.to_thread(ws.create_calendar_event, CalendarEventInput(**payload))
    if tool_name == "update_calendar_event":
        ws = WorkspaceService(settings.database_url)
        return await asyncio.to_thread(
            ws.update_calendar_event, UpdateCalendarEventInput(**payload)
        )
    if tool_name == "delete_calendar_event":
        ws = WorkspaceService(settings.database_url)
        return await asyncio.to_thread(
            ws.delete_calendar_event, DeleteCalendarEventInput(**payload)
        )
    if tool_name == "create_issue":
        adapter = build_github_adapter()
        return await adapter.create_issue(CreateIssueInput(**payload))
    raise ValueError(f"Unsupported locked write tool: {tool_name}")


@app.get("/api/hitl/pending")
async def list_pending_actions(session_id: str | None = None) -> dict[str, Any]:
    """List active non-expired HITL pending actions."""
    actions = await asyncio.to_thread(hitl_gate.list_pending, session_id)
    return {"actions": [a.model_dump(mode="json") for a in actions]}


@app.post("/api/hitl/propose")
async def propose_hitl_action(req: ProposeActionRequest) -> dict[str, Any]:
    """Create a parameter-locked pending action validated against write guardrails."""
    if req.tool_name not in WRITE_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Tool '{req.tool_name}' is not a HITL-gated write tool.",
        )
    try:
        action = await asyncio.to_thread(
            hitl_gate.propose_action,
            req.session_id,
            req.tool_name,
            req.payload,
            idempotency_key=req.idempotency_key,
            ttl_seconds=req.ttl_seconds,
        )
        return action.model_dump(mode="json")
    except PolicyViolationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _notify_livekit_hitl_event(event_type: str, action_payload: dict[str, Any]) -> None:
    """Best-effort broadcast of UI HITL updates/resolutions into the session's LiveKit room."""
    room_name = str(action_payload.get("session_id") or "")
    if not room_name:
        return
    try:
        lk_http_url = settings.livekit_http_url
        lk = LiveKitAPI(
            url=lk_http_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        try:
            packet = json.dumps(
                {"type": event_type, **action_payload}, ensure_ascii=False
            ).encode("utf-8")
            await lk.room.send_data(
                SendDataRequest(room=room_name, data=packet, kind=0)
            )
        finally:
            await lk.aclose()
    except Exception:
        pass


@app.post("/api/hitl/update")
async def update_hitl_action(req: UpdateActionRequest) -> dict[str, Any]:
    """Edit a pending action's payload, re-run guardrails, and re-lock its SHA-256 hash."""
    existing = await asyncio.to_thread(hitl_gate.get_action, req.action_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Action '{req.action_id}' not found.")
    try:
        normalized_payload = _validate_tool_payload_shape(existing.tool_name, req.payload)
        updated = await asyncio.to_thread(
            hitl_gate.update_pending_payload,
            req.action_id,
            normalized_payload,
        )
        payload = updated.model_dump(mode="json")
        await _notify_livekit_hitl_event("hitl_pending", payload)
        return payload
    except ActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ActionExpiredError, PolicyViolationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/hitl/approve")
async def approve_hitl_action(req: ApproveActionRequest) -> dict[str, Any]:
    """Approve and execute a locked pending action once its payload hash is verified."""
    try:
        executed = await hitl_gate.approve_and_execute(
            req.action_id,
            req.payload_hash,
            execute_locked_write_tool,
        )
        payload = executed.model_dump(mode="json")
        await _notify_livekit_hitl_event("hitl_resolved", payload)
        return payload
    except ActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ActionExpiredError, PayloadTamperedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ActionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CalendarConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/hitl/reject")
async def reject_hitl_action(req: RejectActionRequest) -> dict[str, Any]:
    """Reject a pending HITL action without executing any side effect."""
    try:
        rejected = await asyncio.to_thread(hitl_gate.reject_action, req.action_id)
        payload = rejected.model_dump(mode="json")
        await _notify_livekit_hitl_event("hitl_resolved", payload)
        return payload
    except ActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionExpiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ActionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _prune_oauth_states() -> None:
    cutoff = time.time() - 600.0
    expired = [k for k, ts in _google_oauth_states.items() if ts < cutoff]
    for key in expired:
        _google_oauth_states.pop(key, None)


@app.get("/api/integrations/google/status")
async def google_integration_status() -> dict[str, Any]:
    """Return current Google Workspace (Gmail + Calendar) OAuth connection status."""
    client = _get_google_client()
    status = client.status()
    status["email_mode"] = os.getenv("WORKSPACE_EMAIL_MODE", "auto")
    status["calendar_mode"] = os.getenv("WORKSPACE_CALENDAR_MODE", "auto")
    return status


@app.get("/api/integrations/google/auth-url")
async def google_integration_auth_url() -> dict[str, str]:
    """Generate a Google OAuth2 authorization URL for Gmail & Google Calendar."""
    _prune_oauth_states()
    client = _get_google_client()
    state = secrets.token_urlsafe(24)
    try:
        url = client.build_auth_url(state)
    except GoogleNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _google_oauth_states[state] = time.time()
    return {"url": url, "state": state}


@app.get("/api/integrations/google/callback", response_class=HTMLResponse)
async def google_integration_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Handle Google OAuth2 redirect, persist tokens, and notify the Cockpit opener."""
    _prune_oauth_states()
    if error:
        safe_err = html.escape(error)
        return HTMLResponse(
            f"<html><body><h3>Google connection cancelled: {safe_err}</h3></body></html>",
            status_code=400,
        )
    if not code or not state or state not in _google_oauth_states:
        return HTMLResponse(
            "<html><body><h3>Invalid or expired OAuth state. "
            "Retry from Cockpit.</h3></body></html>",
            status_code=400,
        )
    _google_oauth_states.pop(state, None)
    client = _get_google_client()
    try:
        record = await client.exchange_code(code)
    except Exception as exc:
        safe_exc = html.escape(str(exc))
        return HTMLResponse(
            f"<html><body><h3>Token exchange failed: {safe_exc}</h3></body></html>",
            status_code=400,
        )

    safe_email = html.escape(str(record.get("email") or "Google account"))
    payload_js = json.dumps({"type": "sadaops-google-connected", "email": record.get("email")})
    body = (
        "<!doctype html><html><body style=\"font-family:system-ui;background:#1a1512;"
        "color:#f3ece1;padding:2rem;text-align:center\">"
        f"<h2>Connected {safe_email}</h2>"
        "<p>Gmail &amp; Google Calendar are now active for SadaOps. You can close this window.</p>"
        f"<script>if(window.opener){{window.opener.postMessage({payload_js},'*');}}"
        "setTimeout(()=>window.close(),1200);</script></body></html>"
    )
    return HTMLResponse(body)


@app.post("/api/integrations/google/disconnect")
async def google_integration_disconnect() -> dict[str, bool]:
    """Remove saved local Google OAuth tokens and revert to Mailpit/Postgres fallback."""
    client = _get_google_client()
    return {"disconnected": client.disconnect()}
