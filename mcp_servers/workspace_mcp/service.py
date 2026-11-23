"""Calendar (PostgreSQL / Google Calendar) and Email (Mailpit / Gmail) service for Workspace MCP."""

from __future__ import annotations

import os
import re
import smtplib
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import httpx
import psycopg
from pydantic import BaseModel, EmailStr, Field

from mcp_servers.workspace_mcp.google_client import (
    GoogleCalendarConflictError,
    GoogleWorkspaceClient,
)

DEFAULT_DATABASE_URL = "postgresql://sadaops:sadaops_dev_pass@127.0.0.1:5432/sadaops"


class CalendarEventInput(BaseModel):
    """Validated payload for creating a calendar event."""

    title: str = Field(min_length=2, max_length=200)
    start_iso: str
    duration_minutes: int = Field(default=30, ge=5, le=240)
    attendees: list[EmailStr] = Field(default_factory=list)
    organizer: EmailStr = "bejad@example.com"
    location: str = "Google Meet"
    notes: str = ""


class UpdateCalendarEventInput(BaseModel):
    """Validated payload for patching an existing calendar event."""

    event_id: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, min_length=2, max_length=200)
    start_iso: str | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=240)
    location: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)


class DeleteCalendarEventInput(BaseModel):
    """Validated payload for deleting a calendar event."""

    event_id: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=200)


class SendEmailInput(BaseModel):
    """Validated payload for sending an email message."""

    to: EmailStr
    subject: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=2, max_length=8000)
    sender: EmailStr = "bejad@example.com"


class CalendarConflictError(ValueError):
    """Raised when a new meeting overlaps an existing scheduled event."""


def _parse_iso(value: str) -> datetime:
    cleaned = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return datetime.fromisoformat(f"{cleaned}T00:00:00+00:00")
    dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class WorkspaceService:
    """Manages calendar & email via Google (Gmail/GCal) when connected, or Mailpit/Postgres."""

    def __init__(
        self,
        database_url: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        mailpit_api_url: str | None = None,
        *,
        email_mode: str | None = None,
        calendar_mode: str | None = None,
        google_client: GoogleWorkspaceClient | None = None,
    ) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        self.smtp_host = smtp_host or os.getenv("MAILPIT_SMTP_HOST", "127.0.0.1")
        self.smtp_port = int(smtp_port or os.getenv("MAILPIT_SMTP_PORT", "1025"))
        self.mailpit_api_url = (
            mailpit_api_url or os.getenv("MAILPIT_API_URL", "http://127.0.0.1:8025")
        ).rstrip("/")
        self.email_mode = (email_mode or os.getenv("WORKSPACE_EMAIL_MODE", "auto")).strip().lower()
        self.calendar_mode = (
            calendar_mode or os.getenv("WORKSPACE_CALENDAR_MODE", "auto")
        ).strip().lower()
        self.google_client = google_client or GoogleWorkspaceClient()

    def _use_google_email(self) -> bool:
        if self.email_mode == "google":
            return True
        if self.email_mode == "mailpit":
            return False
        return self.google_client.is_connected()

    def _use_google_calendar(self) -> bool:
        if self.calendar_mode == "google":
            return True
        if self.calendar_mode == "postgres":
            return False
        return self.google_client.is_connected()

    def list_events(
        self,
        day_iso: str | None = None,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        query: str = "",
    ) -> list[dict[str, Any]]:
        if self._use_google_calendar():
            return self.google_client.calendar_list_events(
                day_iso=day_iso,
                start_date=start_date,
                end_date=end_date,
                query=query,
            )
        range_start = start_date or day_iso
        range_end = end_date or day_iso
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                if range_start:
                    start_dt = _parse_iso(range_start).astimezone(UTC).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    end_dt = (
                        _parse_iso(range_end).astimezone(UTC).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        + timedelta(days=1)
                        if range_end
                        else start_dt + timedelta(days=14)
                    )
                    cur.execute(
                        "SELECT id, title, organizer, attendees, start_time::text, "
                        "end_time::text, location, notes FROM calendar_events "
                        "WHERE start_time < %s AND end_time > %s ORDER BY start_time ASC",
                        (end_dt, start_dt),
                    )
                else:
                    cur.execute(
                        "SELECT id, title, organizer, attendees, start_time::text, "
                        "end_time::text, location, notes FROM calendar_events "
                        "ORDER BY start_time ASC LIMIT 50"
                    )
                cols = [d.name for d in cur.description or []]
                rows = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
        needle = query.strip().lower()
        if not needle:
            return rows
        return [
            r
            for r in rows
            if needle
            in f"{r.get('title', '')} {r.get('location', '')} {r.get('notes', '')}".lower()
        ]

    def find_free_slots(
        self, day_iso: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        """Find free 5..240-min slots between 09:00 and 18:00 UTC on the requested day."""
        if duration_minutes < 5 or duration_minutes > 240:
            raise ValueError("duration_minutes must be between 5 and 240.")
        day_dt = _parse_iso(day_iso).astimezone(UTC)
        work_start = day_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        work_end = day_dt.replace(hour=18, minute=0, second=0, microsecond=0)
        events = self.list_events(day_iso)
        busy = [(_parse_iso(ev["start_time"]), _parse_iso(ev["end_time"])) for ev in events]

        slots: list[dict[str, str]] = []
        cursor = work_start
        delta = timedelta(minutes=duration_minutes)
        while cursor + delta <= work_end and len(slots) < 8:
            candidate_end = cursor + delta
            overlaps = any(cursor < b_end and candidate_end > b_start for b_start, b_end in busy)
            if not overlaps:
                slots.append(
                    {"start_time": cursor.isoformat(), "end_time": candidate_end.isoformat()}
                )
            cursor += timedelta(minutes=30)
        return slots

    def create_calendar_event(self, payload: CalendarEventInput) -> dict[str, Any]:
        if self._use_google_calendar():
            try:
                return self.google_client.calendar_create_event(
                    title=payload.title,
                    start_iso=payload.start_iso,
                    duration_minutes=payload.duration_minutes,
                    attendees=[str(a) for a in payload.attendees],
                    location=payload.location,
                    notes=payload.notes,
                )
            except GoogleCalendarConflictError as exc:
                raise CalendarConflictError(str(exc)) from exc

        start_dt = _parse_iso(payload.start_iso)
        end_dt = start_dt + timedelta(minutes=payload.duration_minutes)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('sadaops_calendar'))")
                cur.execute(
                    "SELECT id, title FROM calendar_events "
                    "WHERE start_time < %s AND end_time > %s LIMIT 1",
                    (end_dt, start_dt),
                )
                conflict = cur.fetchone()
                if conflict:
                    raise CalendarConflictError(
                        f"Time slot conflicts with existing event #{conflict[0]} ('{conflict[1]}')"
                    )
                cur.execute(
                    "INSERT INTO calendar_events "
                    "(title, organizer, attendees, start_time, end_time, location, notes) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, title, start_time::text, end_time::text, location",
                    (
                        payload.title,
                        str(payload.organizer),
                        [str(a) for a in payload.attendees],
                        start_dt,
                        end_dt,
                        payload.location,
                        payload.notes,
                    ),
                )
                row = cur.fetchone()
                conn.commit()
                return {
                    "id": row[0],
                    "title": row[1],
                    "start_time": row[2],
                    "end_time": row[3],
                    "location": row[4],
                    "status": "scheduled",
                }

    def update_calendar_event(self, payload: UpdateCalendarEventInput) -> dict[str, Any]:
        if self._use_google_calendar():
            try:
                return self.google_client.calendar_update_event(
                    event_id=payload.event_id,
                    title=payload.title,
                    start_iso=payload.start_iso,
                    duration_minutes=payload.duration_minutes,
                    location=payload.location,
                    notes=payload.notes,
                )
            except GoogleCalendarConflictError as exc:
                raise CalendarConflictError(str(exc)) from exc

        row_id = int(payload.event_id)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT title, start_time, end_time, location, notes "
                    "FROM calendar_events WHERE id = %s",
                    (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Calendar event {payload.event_id} not found.")
                cur_title, cur_start, cur_end, cur_loc, cur_notes = row
                orig_dur = max(5, int((cur_end - cur_start).total_seconds() // 60))
                new_start = _parse_iso(payload.start_iso) if payload.start_iso else cur_start
                new_dur = (
                    payload.duration_minutes if payload.duration_minutes is not None else orig_dur
                )
                new_end = new_start + timedelta(minutes=new_dur)
                cur.execute(
                    "SELECT id, title FROM calendar_events "
                    "WHERE id <> %s AND start_time < %s AND end_time > %s LIMIT 1",
                    (row_id, new_end, new_start),
                )
                conflict = cur.fetchone()
                if conflict:
                    raise CalendarConflictError(
                        f"Time slot conflicts with existing event #{conflict[0]} ('{conflict[1]}')"
                    )
                cur.execute(
                    "UPDATE calendar_events "
                    "SET title = %s, start_time = %s, end_time = %s, location = %s, notes = %s "
                    "WHERE id = %s "
                    "RETURNING id, title, start_time::text, end_time::text, location",
                    (
                        payload.title if payload.title is not None else cur_title,
                        new_start,
                        new_end,
                        payload.location if payload.location is not None else cur_loc,
                        payload.notes if payload.notes is not None else cur_notes,
                        row_id,
                    ),
                )
                updated = cur.fetchone()
                conn.commit()
                return {
                    "id": updated[0],
                    "title": updated[1],
                    "start_time": updated[2],
                    "end_time": updated[3],
                    "location": updated[4],
                    "status": "updated",
                }

    def delete_calendar_event(self, payload: DeleteCalendarEventInput) -> dict[str, Any]:
        if self._use_google_calendar():
            return self.google_client.calendar_delete_event(event_id=payload.event_id)
        row_id = int(payload.event_id)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM calendar_events WHERE id = %s RETURNING id", (row_id,))
                deleted = cur.fetchone()
                conn.commit()
                if not deleted:
                    raise ValueError(f"Calendar event {payload.event_id} not found.")
        return {"id": payload.event_id, "status": "deleted"}

    def send_email(self, payload: SendEmailInput) -> dict[str, Any]:
        if self._use_google_email():
            return self.google_client.gmail_send_message(
                to=str(payload.to),
                subject=payload.subject,
                body=payload.body,
            )

        msg = EmailMessage()
        msg["From"] = payload.sender
        msg["To"] = payload.to
        msg["Subject"] = payload.subject
        msg.set_content(payload.body)

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=5) as smtp:
            smtp.send_message(msg)

        return {
            "status": "sent",
            "to": payload.to,
            "subject": payload.subject,
            "transport": "mailpit",
        }

    async def search_inbox(
        self,
        query: str = "",
        limit: int = 15,
        *,
        since_hours: int | None = None,
        sender: str = "",
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        if self._use_google_email():
            return await self.google_client.gmail_search_messages(
                query=query,
                limit=limit,
                since_hours=since_hours,
                sender=sender,
                unread_only=unread_only,
            )

        safe_limit = max(1, min(int(limit), 50))
        fetch_limit = 50 if (since_hours or sender.strip() or unread_only) else safe_limit
        async with httpx.AsyncClient(timeout=5.0) as client:
            if query.strip():
                resp = await client.get(
                    f"{self.mailpit_api_url}/api/v1/search",
                    params={"query": query.strip(), "limit": fetch_limit},
                )
            else:
                resp = await client.get(
                    f"{self.mailpit_api_url}/api/v1/messages",
                    params={"limit": fetch_limit},
                )
            resp.raise_for_status()
            messages = resp.json().get("messages", [])

        cutoff = (
            datetime.now(UTC) - timedelta(hours=int(since_hours))
            if since_hours is not None and int(since_hours) > 0
            else None
        )
        sender_needle = sender.strip().lower()
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if unread_only and m.get("Read", False):
                continue
            from_obj = m.get("From") or {}
            from_addr = str(from_obj.get("Address") or "")
            from_name = str(from_obj.get("Name") or "")
            if sender_needle and sender_needle not in f"{from_name} {from_addr}".lower():
                continue
            created_str = str(m.get("Created") or "")
            if cutoff is not None and created_str:
                try:
                    if _parse_iso(created_str) < cutoff:
                        continue
                except Exception:
                    pass
            filtered.append(
                {
                    "id": m.get("ID"),
                    "from": from_addr,
                    "to": [r.get("Address") for r in (m.get("To") or [])],
                    "subject": m.get("Subject"),
                    "snippet": m.get("Snippet"),
                    "created": created_str,
                    "unread": not bool(m.get("Read", False)),
                    "provider": "mailpit",
                }
            )

        matched_total = len(filtered)
        for item in filtered[:safe_limit]:
            item["matched_total"] = matched_total
        return filtered[:safe_limit]

    async def analyze_inbox(
        self,
        *,
        since_hours: int | None = 0,
        query: str = "",
        unread_only: bool = False,
        top_n: int = 5,
    ) -> dict[str, Any]:
        """Compute sender frequency leaderboard, total count, and unread count."""
        effective_hours = int(since_hours) if since_hours and int(since_hours) > 0 else 0
        if self._use_google_email():
            return await self.google_client.gmail_analyze_inbox(
                since_hours=effective_hours,
                query=query,
                unread_only=unread_only,
                top_n=top_n,
            )

        msgs = await self.search_inbox(
            query=query,
            limit=50,
            since_hours=effective_hours if effective_hours > 0 else None,
            unread_only=unread_only,
        )
        counts: Counter[str] = Counter()
        latest_subject: dict[str, str] = {}
        unread_count = 0
        for item in msgs:
            addr = str(item.get("from") or "(unknown)").strip().lower()
            counts[addr] += 1
            if item.get("unread"):
                unread_count += 1
            if addr not in latest_subject:
                latest_subject[addr] = str(item.get("subject") or "")
        safe_top = max(1, min(int(top_n), 25))
        return {
            "provider": "mailpit",
            "since_hours": since_hours,
            "query": query,
            "unread_only": unread_only,
            "total_matching": len(msgs),
            "analyzed_count": len(msgs),
            "unread_count": unread_count,
            "top_senders": [
                {
                    "rank": idx + 1,
                    "email": addr,
                    "sender": addr,
                    "count": cnt,
                    "latest_subject": latest_subject.get(addr, ""),
                }
                for idx, (addr, cnt) in enumerate(counts.most_common(safe_top))
            ],
            "recent_subjects": [
                {"id": m.get("id"), "from": m.get("from"), "subject": m.get("subject")}
                for m in msgs[:10]
            ],
        }

    async def read_email(self, message_id: str) -> dict[str, Any]:
        """Read full headers and body text for a specific message ID."""
        if self._use_google_email():
            return await self.google_client.gmail_read_message(message_id)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.mailpit_api_url}/api/v1/message/{message_id.strip()}")
            resp.raise_for_status()
            m = resp.json()
        return {
            "id": m.get("ID"),
            "from": (m.get("From") or {}).get("Address"),
            "to": [r.get("Address") for r in (m.get("To") or [])],
            "subject": m.get("Subject"),
            "created": m.get("Created"),
            "body": (m.get("Text") or m.get("Snippet") or "")[:6000],
            "provider": "mailpit",
        }
