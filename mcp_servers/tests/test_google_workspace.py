"""Unit & mocked-HTTP tests for Gmail, Google Calendar, OAuth tokens, and email allowlists."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agent.safety.prompt_guard import PolicyViolationError, validate_write_policy
from mcp_servers.workspace_mcp.google_client import (
    GoogleOAuthConfig,
    GoogleWorkspaceClient,
)
from mcp_servers.workspace_mcp.service import (
    CalendarConflictError,
    CalendarEventInput,
    SendEmailInput,
    WorkspaceService,
)


def _make_config(tmp_path: Path, *, refresh_token: str = "rtok-1") -> GoogleOAuthConfig:
    return GoogleOAuthConfig(
        client_id="client-id-123",
        client_secret="client-secret-456",
        redirect_uri="http://127.0.0.1:8000/api/integrations/google/callback",
        refresh_token=refresh_token,
        token_path=tmp_path / "google_token.json",
    )


@pytest.mark.asyncio
async def test_google_oauth_exchange_and_gmail_search_count_send(tmp_path: Path) -> None:
    seen_requests: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_requests.append((request.method, url, dict(request.url.params)))
        path = request.url.path
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "acc-fresh-1",
                    "refresh_token": "rtok-new",
                    "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/gmail.readonly",
                },
            )
        if url.startswith("https://www.googleapis.com/oauth2/v2/userinfo"):
            return httpx.Response(200, json={"email": "bejad.ops@gmail.com"})
        if path.endswith("/messages/send"):
            body = json.loads(request.content.decode("utf-8"))
            raw_decoded = base64.urlsafe_b64decode(body["raw"]).decode("utf-8")
            assert "To: colleague@example.com" in raw_decoded
            assert "Subject: Incident Update" in raw_decoded
            return httpx.Response(200, json={"id": "msg-sent-99", "threadId": "thr-99"})
        if path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": "m1"}, {"id": "m2"}],
                    "resultSizeEstimate": 2,
                },
            )
        if path.endswith("/messages/m1") or path.endswith("/messages/m2"):
            mid = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": mid,
                    "snippet": f"Snippet for {mid}",
                    "internalDate": "1795400000000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Ali <ali@vendor.com>"},
                            {"name": "To", "value": "bejad.ops@gmail.com"},
                            {"name": "Subject", "value": f"Alert {mid}"},
                            {"name": "Date", "value": "Mon, 23 Nov 2026 10:00:00 +0000"},
                        ]
                    },
                },
            )
        return httpx.Response(404)

    client = GoogleWorkspaceClient(
        _make_config(tmp_path, refresh_token=""),
        transport=httpx.MockTransport(handler),
        async_transport=httpx.MockTransport(handler),
    )

    auth_url = client.build_auth_url("csrf-state-1")
    parsed_qs = parse_qs(urlparse(auth_url).query)
    assert parsed_qs["state"] == ["csrf-state-1"]
    assert "gmail.readonly" in parsed_qs["scope"][0]

    record = await client.exchange_code("auth-code-xyz")
    assert record["email"] == "bejad.ops@gmail.com"
    assert client.is_connected() is True
    assert client.status()["email"] == "bejad.ops@gmail.com"

    ws = WorkspaceService(google_client=client)
    msgs = await ws.search_inbox(query="invoice", since_hours=24, sender="ali@vendor.com", limit=10)
    assert len(msgs) == 2
    assert msgs[0]["provider"] == "gmail"
    assert msgs[0]["matched_total"] == 2
    list_req = [r for r in seen_requests if "/messages?" in r[1] and r[0] == "GET"][0]
    assert "from:ali@vendor.com" in list_req[2]["q"]
    assert "after:" in list_req[2]["q"]
    assert "invoice" in list_req[2]["q"]

    sent = ws.send_email(
        SendEmailInput(to="colleague@example.com", subject="Incident Update", body="Resolved.")
    )
    assert sent["status"] == "sent"
    assert sent["transport"] == "gmail"
    assert sent["id"] == "msg-sent-99"


@pytest.mark.asyncio
async def test_gmail_analyze_inbox_top_senders_and_read_email_body(tmp_path: Path) -> None:
    sample_body = base64.urlsafe_b64encode(b"Hi Bejad, your flight departs at 09:30.").decode(
        "ascii"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/labels/INBOX"):
            return httpx.Response(200, json={"messagesTotal": 1420, "messagesUnread": 318})
        if path.endswith("/labels/UNREAD"):
            return httpx.Response(200, json={"messagesTotal": 412, "messagesUnread": 412})
        if path.endswith("/labels/CATEGORY_PROMOTIONS"):
            return httpx.Response(200, json={"messagesUnread": 220})
        if path.endswith("/threads"):
            return httpx.Response(
                200,
                json={"threads": [{"id": f"th-{i}"} for i in range(91)], "resultSizeEstimate": 91},
            )
        if path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}],
                    "resultSizeEstimate": 3,
                },
            )
        if path.endswith("/messages/a1"):
            return httpx.Response(
                200,
                json={
                    "id": "a1",
                    "threadId": "t1",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": sample_body},
                        "headers": [
                            {"name": "From", "value": "GitHub <notifications@github.com>"},
                            {"name": "Subject", "value": "PR #412 merged"},
                            {"name": "Date", "value": "Mon, 23 Nov 2026 11:00:00 +0000"},
                        ],
                    },
                },
            )
        if path.endswith("/messages/a2"):
            return httpx.Response(
                200,
                json={
                    "id": "a2",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "notifications@github.com"},
                            {"name": "Subject", "value": "CI Run failed"},
                            {"name": "Date", "value": "Mon, 23 Nov 2026 10:30:00 +0000"},
                        ]
                    },
                },
            )
        if path.endswith("/messages/a3"):
            return httpx.Response(
                200,
                json={
                    "id": "a3",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Ali <ali@vendor.com>"},
                            {"name": "Subject", "value": "Invoice #88"},
                            {"name": "Date", "value": "Mon, 23 Nov 2026 09:00:00 +0000"},
                        ]
                    },
                },
            )
        return httpx.Response(404)

    cfg = _make_config(tmp_path)
    cfg.token_path.write_text(
        json.dumps(
            {
                "access_token": "valid-acc",
                "refresh_token": "rtok-1",
                "expires_at": time.time() + 1800,
                "email": "bejad.ops@gmail.com",
            }
        ),
        encoding="utf-8",
    )
    client = GoogleWorkspaceClient(
        cfg,
        transport=httpx.MockTransport(handler),
        async_transport=httpx.MockTransport(handler),
    )
    ws = WorkspaceService(google_client=client)

    analytics = await ws.analyze_inbox(since_hours=24, top_n=5)
    assert analytics["total_matching"] == 3
    assert analytics["unread_count"] == 2
    assert analytics["top_senders"][0]["email"] == "notifications@github.com"
    assert analytics["top_senders"][0]["count"] == 2
    assert analytics["top_senders"][1]["email"] == "ali@vendor.com"
    assert analytics["top_senders"][1]["count"] == 1

    msg = await ws.read_email("a1")
    assert "flight departs at 09:30" in msg["body"]
    assert msg["subject"] == "PR #412 merged"

    # Default since_hours=0 returns exact all-time Gmail INBOX & UNREAD counts
    all_time = await ws.analyze_inbox()
    assert all_time["since_hours"] == 0
    assert all_time["inbox_total"] == 1420
    assert all_time["inbox_unread"] == 318
    assert all_time["all_unread_total"] == 412
    assert all_time["primary_inbox_unread"] == 91
    assert all_time["promotions_unread"] == 220
    assert all_time["unread_count"] == 412


def test_google_calendar_list_slots_conflict_and_create(tmp_path: Path) -> None:
    day = datetime(2030, 7, 15, tzinfo=UTC)
    existing_start = day.replace(hour=10, minute=0).isoformat()
    existing_end = (day.replace(hour=10, minute=0) + timedelta(minutes=60)).isoformat()
    created_events: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "refreshed-tok", "expires_in": 3600})
        if "/calendar/v3/calendars/primary/events" in url and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "gcal-1",
                            "summary": "Ops Standup",
                            "organizer": {"email": "bejad.ops@gmail.com"},
                            "attendees": [{"email": "tariq@example.com"}],
                            "start": {"dateTime": existing_start},
                            "end": {"dateTime": existing_end},
                            "location": "Google Meet",
                            "description": "Daily triage",
                        }
                    ]
                },
            )
        if "/calendar/v3/calendars/primary/events" in url and request.method == "POST":
            payload = json.loads(request.content.decode("utf-8"))
            created_events.append(payload)
            return httpx.Response(
                200,
                json={
                    "id": "gcal-created-2",
                    "summary": payload["summary"],
                    "start": payload["start"],
                    "end": payload["end"],
                    "location": payload.get("location", ""),
                    "htmlLink": "https://calendar.google.com/event?eid=abc",
                },
            )
        return httpx.Response(404)

    cfg = _make_config(tmp_path)
    # Force expired cached token so _refresh_access_token_sync refreshes automatically
    cfg.token_path.write_text(
        json.dumps(
            {
                "access_token": "expired-tok",
                "refresh_token": "rtok-1",
                "expires_at": time.time() - 100,
                "email": "bejad.ops@gmail.com",
            }
        ),
        encoding="utf-8",
    )
    client = GoogleWorkspaceClient(cfg, transport=httpx.MockTransport(handler))
    ws = WorkspaceService(google_client=client)

    events = ws.list_events("2030-07-15")
    assert len(events) == 1
    assert events[0]["title"] == "Ops Standup"
    assert events[0]["provider"] == "google_calendar"

    # Slot 10:00-11:00 UTC is occupied, so find_free_slots should exclude 10:00
    slots = ws.find_free_slots("2030-07-15", duration_minutes=30)
    slot_starts = [s["start_time"] for s in slots]
    assert not any("T10:00:00" in st or "T10:30:00" in st for st in slot_starts)
    assert any("T09:00:00" in st for st in slot_starts)

    # Attempting to schedule during 10:15-10:45 raises CalendarConflictError
    with pytest.raises(CalendarConflictError):
        ws.create_calendar_event(
            CalendarEventInput(
                title="Conflicting Review",
                start_iso="2030-07-15T10:15:00Z",
                duration_minutes=30,
            )
        )

    # Scheduling during open 14:00-14:30 UTC succeeds
    scheduled = ws.create_calendar_event(
        CalendarEventInput(
            title="Architecture Sync",
            start_iso="2030-07-15T14:00:00Z",
            duration_minutes=30,
            attendees=["tariq@example.com"],
            notes="Discuss SLOs",
        )
    )
    assert scheduled["transport"] == "google_calendar"
    assert scheduled["id"] == "gcal-created-2"
    assert len(created_events) == 1


def test_allowed_email_domains_env_override_preserves_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOWED_EMAIL_DOMAINS", raising=False)
    with pytest.raises(PolicyViolationError):
        validate_write_policy(
            "send_email",
            {"to": "friend@gmail.com", "subject": "Hi", "body": "Body"},
        )

    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "example.com, gmail.com")
    validate_write_policy(
        "send_email",
        {"to": "friend@gmail.com", "subject": "Hi", "body": "Body"},
    )
    with pytest.raises(PolicyViolationError):
        validate_write_policy(
            "send_email",
            {"to": "attacker@evil.com", "subject": "Hi", "body": "Body"},
        )


def test_google_calendar_open_range_update_and_delete(tmp_path: Path) -> None:
    from mcp_servers.workspace_mcp.service import (
        DeleteCalendarEventInput,
        UpdateCalendarEventInput,
    )

    seen_calls: list[tuple[str, str, dict[str, Any]]] = []
    ev_start = "2030-12-05T11:00:00Z"
    ev_end = "2030-12-05T11:30:00Z"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen_calls.append((request.method, path, dict(request.url.params)))
        if path.endswith("/events/ev-99") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "ev-99",
                    "summary": "Monthly Review",
                    "start": {"dateTime": ev_start},
                    "end": {"dateTime": ev_end},
                },
            )
        if path.endswith("/events/ev-99") and request.method == "PATCH":
            patch = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "ev-99",
                    "summary": patch.get("summary", "Monthly Review"),
                    "start": patch.get("start", {"dateTime": ev_start}),
                    "end": patch.get("end", {"dateTime": ev_end}),
                },
            )
        if path.endswith("/events/ev-99") and request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/events") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "timeZone": "Asia/Dubai",
                    "items": [
                        {
                            "id": "ev-99",
                            "summary": "Monthly Review",
                            "start": {"dateTime": ev_start},
                            "end": {"dateTime": ev_end},
                        }
                    ],
                },
            )
        return httpx.Response(404)

    cfg = _make_config(tmp_path)
    cfg.token_path.write_text(
        json.dumps(
            {
                "access_token": "valid-tok",
                "refresh_token": "rtok-1",
                "expires_at": time.time() + 1800,
            }
        ),
        encoding="utf-8",
    )
    ws = WorkspaceService(
        google_client=GoogleWorkspaceClient(cfg, transport=httpx.MockTransport(handler))
    )

    month_events = ws.list_events(start_date="2030-12-01", end_date="2030-12-31")
    assert len(month_events) == 1
    assert month_events[0]["timezone"] == "Asia/Dubai"
    assert "+04:00" in month_events[0]["local_start"]

    updated = ws.update_calendar_event(
        UpdateCalendarEventInput(
            event_id="ev-99",
            title="Monthly Ops Sync",
            start_iso="2030-12-06T14:00:00Z",
        )
    )
    assert updated["status"] == "updated"
    assert updated["title"] == "Monthly Ops Sync"

    deleted = ws.delete_calendar_event(DeleteCalendarEventInput(event_id="ev-99"))
    assert deleted["status"] == "deleted"
    assert any(m == "DELETE" and p.endswith("/events/ev-99") for m, p, _ in seen_calls)
