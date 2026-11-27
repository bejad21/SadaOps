"""Integration & schema tests for github_mcp and workspace_mcp (Postgres + Mailpit)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mcp_servers.github_mcp.adapter import (
    CreateIssueInput,
    FixtureGitHubAdapter,
    LiveGitHubAdapter,
    build_github_adapter,
)
from mcp_servers.workspace_mcp.service import (
    CalendarConflictError,
    CalendarEventInput,
    SendEmailInput,
    WorkspaceService,
)


@pytest.mark.asyncio
async def test_github_fixture_adapter_crud_and_validation() -> None:
    adapter = FixtureGitHubAdapter()
    prs = await adapter.list_open_prs()
    assert len(prs) >= 2
    ci = await adapter.get_ci_logs(run_id=8891)
    assert ci["status"] == "failed"

    created = await adapter.create_issue(
        CreateIssueInput(title="Investigate auth 502", body="Found Redis pool limit")
    )
    assert created["number"] >= 103
    assert created["mode"] == "fixture"

    with pytest.raises(ValidationError):
        CreateIssueInput(title="x", body="short")


def test_workspace_pydantic_validation() -> None:
    with pytest.raises(ValidationError):
        SendEmailInput(to="not-an-email", subject="Hi", body="Body text")

    with pytest.raises(ValidationError):
        CalendarEventInput(title="x", start_iso="2026-11-20T10:00:00Z", duration_minutes=2)


@pytest.mark.asyncio
async def test_workspace_calendar_overlap_and_mailpit_delivery() -> None:
    import psycopg

    ws = WorkspaceService()
    with psycopg.connect(ws.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM calendar_events WHERE title LIKE 'SadaOps Test Sync %'")
            conn.commit()
    unique_tag = uuid.uuid4().hex[:6]
    day_start = datetime(2030, 5, 10, 11, 0, tzinfo=UTC)
    ev: dict | None = None
    try:
        ev = ws.create_calendar_event(
            CalendarEventInput(
                title=f"SadaOps Test Sync {unique_tag}",
                start_iso=day_start.isoformat(),
                duration_minutes=30,
                attendees=["tariq@example.com"],
            )
        )
        assert ev["status"] == "scheduled"

        with pytest.raises(CalendarConflictError):
            ws.create_calendar_event(
                CalendarEventInput(
                    title="Overlapping Meeting",
                    start_iso=(day_start + timedelta(minutes=15)).isoformat(),
                    duration_minutes=30,
                )
            )
    finally:
        if ev is not None:
            with psycopg.connect(ws.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM calendar_events WHERE id = %s", (ev["id"],))
                    conn.commit()

    sent = ws.send_email(
        SendEmailInput(
            to="ops-leads@example.com",
            subject=f"SadaOps Smoke {unique_tag}",
            body="Auth gateway triage summary.",
        )
    )
    assert sent["status"] == "sent"
    inbox = await ws.search_inbox(query=unique_tag)
    assert any(unique_tag in (m.get("subject") or "") for m in inbox)


@pytest.mark.asyncio
async def test_github_live_adapter_parity_and_env_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setenv("GITHUB_MODE", "fixture")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert isinstance(build_github_adapter(), FixtureGitHubAdapter)

    monkeypatch.setenv("GITHUB_MODE", "live")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_fake_test_only")
    monkeypatch.setenv("GITHUB_REPO", "bejad21/sadaops-demo")
    assert isinstance(build_github_adapter(), LiveGitHubAdapter)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 412,
                        "title": "fix(auth-gateway): increase Redis pool",
                        "user": {"login": "tariq"},
                        "state": "open",
                        "updated_at": "2026-11-19T08:10:00Z",
                    }
                ],
            )
        if path.endswith("/actions/runs/8891/jobs"):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "name": "pytest",
                            "conclusion": "failure",
                            "steps": [{"name": "Run pytest", "conclusion": "failure"}],
                        }
                    ]
                },
            )
        if request.method == "GET" and path.endswith("/issues/101"):
            return httpx.Response(
                200,
                json={
                    "number": 101,
                    "title": "auth-gateway 502 spikes",
                    "state": "open",
                    "user": {"login": "tariq"},
                    "body": "Redis pool exhausted.",
                },
            )
        if request.method == "POST" and path.endswith("/issues"):
            return httpx.Response(
                201,
                json={
                    "number": 501,
                    "title": "Live issue test",
                    "state": "open",
                    "html_url": "https://github.com/bejad21/sadaops-demo/issues/501",
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        live = LiveGitHubAdapter(
            token="test-token",
            default_repo="bejad21/sadaops-demo",
            client=mock_client,
        )
        prs = await live.list_open_prs()
        assert prs[0]["number"] == 412 and prs[0]["author"] == "tariq"

        logs = await live.get_ci_logs(run_id=8891)
        assert logs["run_id"] == 8891 and logs["jobs"][0]["failed_steps"] == ["Run pytest"]

        issue = await live.get_issue_details("bejad21/sadaops-demo", 101)
        assert issue["number"] == 101 and issue["state"] == "open"

        created = await live.create_issue(
            CreateIssueInput(title="Live issue test", body="Testing live REST parity")
        )
        assert created["mode"] == "live" and created["number"] == 501


@pytest.mark.asyncio
async def test_live_github_all_repos_commits_ci_and_guardrails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from agent.safety.prompt_guard import PolicyViolationError, validate_write_policy

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": "bejad21/sadaops",
                        "private": True,
                        "default_branch": "main",
                        "open_issues_count": 3,
                        "language": "Python",
                        "updated_at": "2026-11-23T10:00:00Z",
                    },
                    {
                        "full_name": "bejad21/portfolio",
                        "private": False,
                        "default_branch": "main",
                        "open_issues_count": 0,
                        "language": "TypeScript",
                        "updated_at": "2026-11-20T09:00:00Z",
                    },
                ],
            )
        if path == "/repos/bejad21/sadaops/commits":
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "abcdef1234567890",
                        "commit": {
                            "message": "feat: add list_repos",
                            "author": {"name": "Bejad", "date": "2026-11-23T12:00:00Z"},
                        },
                    }
                ],
            )
        if path == "/repos/bejad21/sadaops/actions/runs":
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 9001,
                            "name": "CI Pipeline",
                            "conclusion": "failure",
                            "status": "completed",
                            "head_branch": "main",
                            "created_at": "2026-11-23T12:05:00Z",
                            "html_url": "https://github.com/bejad21/sadaops/actions/runs/9001",
                        }
                    ]
                },
            )
        if path == "/repos/bejad21/sadaops/actions/runs/9001/jobs":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "name": "pytest",
                            "conclusion": "failure",
                            "steps": [{"name": "Run tests", "conclusion": "failure"}],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    monkeypatch.setenv("GITHUB_MODE", "live")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_fake_test_only")
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "*")

    adapter = build_github_adapter()
    assert isinstance(adapter, LiveGitHubAdapter)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        live = LiveGitHubAdapter(token="test-tok", default_repo="", client=mock_client)
        repos_info = await live.list_repos()
        assert repos_info["total_count"] == 2
        assert repos_info["public_count"] == 1
        assert repos_info["private_count"] == 1

        # Short repo name "sadaops" resolves to "bejad21/sadaops" automatically
        commits = await live.list_commits(repo="sadaops", limit=5)
        assert commits[0]["sha"] == "abcdef1"
        assert commits[0]["repo"] == "bejad21/sadaops"

        ci = await live.get_ci_logs(repo="sadaops")
        assert ci["failed_jobs"][0]["job"] == "pytest"
        assert ci["failed_jobs"][0]["failed_steps"] == ["Run tests"]

    # Ensure prompt_guard respects GITHUB_ALLOWED_REPOS=* when opted in, and blocks by default.
    validate_write_policy(
        "create_issue",
        {"repo": "bejad21/sadaops", "title": "Fix bug", "body": "Details"},
    )
    monkeypatch.delenv("GITHUB_ALLOWED_REPOS", raising=False)
    with pytest.raises(PolicyViolationError):
        validate_write_policy(
            "create_issue",
            {"repo": "bejad21/sadaops", "title": "Fix bug", "body": "Details"},
        )


@pytest.mark.asyncio
async def test_universal_github_request_and_google_api_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Verify universal read primitives allow exploration while blocking SSRF."""
    from mcp_servers.workspace_mcp.google_client import GoogleOAuthConfig, GoogleWorkspaceClient
    from mcp_servers.workspace_mcp.service import WorkspaceService

    def gh_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/bejad21/sadaops/branches":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "main",
                        "protected": True,
                        "node_id": "noisy-node-id",
                        "commit": {"sha": "abc1234", "url": "https://api.github.com/commits/abc"},
                    },
                    {"name": "feat/universal-tools", "protected": False},
                ],
            )
        return httpx.Response(404)

    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "*")
    async with httpx.AsyncClient(transport=httpx.MockTransport(gh_handler)) as mock_client:
        live = LiveGitHubAdapter(token="test-tok", default_repo="", client=mock_client)
        branches = await live.github_request("/repos/bejad21/sadaops/branches", limit=10)
        assert len(branches) == 2
        assert branches[0]["name"] == "main"
        assert "node_id" not in branches[0]

        for bad_path in ("https://evil.example/steal", "/repos/../../etc/passwd", ""):
            with pytest.raises(ValueError):
                await live.github_request(bad_path)

        monkeypatch.delenv("GITHUB_ALLOWED_REPOS", raising=False)
        with pytest.raises(ValueError, match="outside GITHUB_ALLOWED_REPOS"):
            await live.github_request("/repos/attacker/other-repo/branches")

    def google_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://gmail.googleapis.com/gmail/v1/users/me/labels"):
            return httpx.Response(
                200,
                json={
                    "kind": "gmail#listLabelsResponse",
                    "labels": [{"id": "INBOX", "name": "INBOX"}, {"id": "SENT", "name": "SENT"}],
                },
            )
        if url.startswith("https://www.googleapis.com/calendar/v3/users/me/calendarList"):
            return httpx.Response(
                200,
                json={
                    "etag": '"abc"',
                    "items": [{"id": "primary", "summary": "Work"}],
                },
            )
        return httpx.Response(404)

    token_path = tmp_path / "tok.json"
    token_path.write_text(
        '{"access_token": "ya29.test", "expires_at": 9999999999}', encoding="utf-8"
    )
    g_client = GoogleWorkspaceClient(
        GoogleOAuthConfig("cid", "csec", "http://127.0.0.1:8000/cb", "ref", token_path),
        async_transport=httpx.MockTransport(google_handler),
    )
    ws = WorkspaceService(google_client=g_client)

    labels = await ws.google_api_get(service="gmail", path="users/me/labels")
    assert "kind" not in labels
    assert [lbl["id"] for lbl in labels["labels"]] == ["INBOX", "SENT"]

    cals = await ws.google_api_get(service="calendar", path="/users/me/calendarList")
    assert "etag" not in cals
    assert cals["items"][0]["summary"] == "Work"

    with pytest.raises(ValueError):
        await ws.google_api_get(service="drive", path="/files")
    with pytest.raises(ValueError):
        await ws.google_api_get(service="gmail", path="https://evil.example/me")
