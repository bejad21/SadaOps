"""FastMCP server exposing Calendar and Mailpit Email tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_servers.workspace_mcp.service import (
    CalendarEventInput,
    DeleteCalendarEventInput,
    SendEmailInput,
    UpdateCalendarEventInput,
    WorkspaceService,
)

mcp = MCPServer("sadaops-workspace")
_service = WorkspaceService()


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Calendar Events",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def list_calendar_events(
    day_iso: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    query: str = "",
) -> list[dict[str, Any]]:
    """List calendar events for a specific date or open start_date..end_date range."""
    return _service.list_events(
        day_iso=day_iso, start_date=start_date, end_date=end_date, query=query
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Find Free Calendar Slots",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def find_free_slots(day_iso: str, duration_minutes: int = 30) -> list[dict[str, str]]:
    """Find open meeting slots on a given ISO date between 09:00 and 18:00 UTC."""
    return _service.find_free_slots(day_iso=day_iso, duration_minutes=duration_minutes)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Calendar Event",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def create_calendar_event(
    title: str,
    start_iso: str,
    duration_minutes: int = 30,
    attendees: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Schedule a calendar event (write action — gated by HITL when invoked from voice)."""
    payload = CalendarEventInput(
        title=title,
        start_iso=start_iso,
        duration_minutes=duration_minutes,
        attendees=attendees or [],
        notes=notes,
    )
    return _service.create_calendar_event(payload)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Calendar Event",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def update_calendar_event(
    event_id: str,
    title: str | None = None,
    start_iso: str | None = None,
    duration_minutes: int | None = None,
    location: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Update or reschedule a calendar event by ID (HITL-gated)."""
    payload = UpdateCalendarEventInput(
        event_id=event_id,
        title=title,
        start_iso=start_iso,
        duration_minutes=duration_minutes,
        location=location,
        notes=notes,
    )
    return _service.update_calendar_event(payload)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Calendar Event",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def delete_calendar_event(event_id: str, title: str = "") -> dict[str, Any]:
    """Cancel/delete a calendar event by ID (HITL-gated)."""
    payload = DeleteCalendarEventInput(event_id=event_id, title=title)
    return _service.delete_calendar_event(payload)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search Operator Inbox",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def search_inbox(
    query: str = "",
    limit: int = 15,
    since_hours: int | None = None,
    sender: str = "",
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    """Search emails; supports Gmail syntax (has:attachment, category:primary, after:YYYY/MM/DD)."""
    return await _service.search_inbox(
        query=query,
        limit=limit,
        since_hours=since_hours,
        sender=sender,
        unread_only=unread_only,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Analyze Inbox Senders and Counts",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def analyze_inbox(
    since_hours: int = 0,
    query: str = "",
    unread_only: bool = False,
    top_n: int = 5,
) -> dict[str, Any]:
    """Count total/unread emails and top senders (since_hours=0 means all time)."""
    return await _service.analyze_inbox(
        since_hours=since_hours,
        query=query,
        unread_only=unread_only,
        top_n=top_n,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read Full Email Message",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def read_email(message_id: str) -> dict[str, Any]:
    """Read the full headers and body text for a single email message ID."""
    return await _service.read_email(message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Google Workspace Read Request",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def google_api_get(
    service: str,
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 20,
) -> Any:
    """Execute a read-only GET request against Gmail v1 ('gmail') or Calendar v3 ('calendar')."""
    return await _service.google_api_get(
        service=service, path=path, params=params, limit=limit
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Send Email Summary",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email summary (write action — gated by HITL when invoked from voice)."""
    payload = SendEmailInput(to=to, subject=subject, body=body)
    return _service.send_email(payload)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run()
