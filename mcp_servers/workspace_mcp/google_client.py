"""Google OAuth2 and REST API client for Gmail and Google Calendar."""

from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOKEN_PATH = REPO_ROOT / ".sadaops_google_token.json"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8000/api/integrations/google/callback"

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
)


class GoogleNotConfiguredError(RuntimeError):
    """Raised when Google OAuth client credentials or tokens are missing."""


class GoogleCalendarConflictError(ValueError):
    """Raised when a scheduled Google Calendar slot overlaps an existing event."""


def _default_token_path() -> Path:
    configured = os.getenv("SADAOPS_GOOGLE_TOKEN_PATH", "").strip()
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else (REPO_ROOT / p)
    return DEFAULT_TOKEN_PATH


def _parse_iso(value: str) -> datetime:
    cleaned = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return datetime.fromisoformat(f"{cleaned}T00:00:00+00:00")
    dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_email_date(raw_date: str | None, internal_ms: str | None) -> str:
    if raw_date:
        try:
            dt = parsedate_to_datetime(raw_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).isoformat()
        except Exception:
            pass
    if internal_ms:
        try:
            dt = datetime.fromtimestamp(int(internal_ms) / 1000.0, tz=UTC)
            return dt.isoformat()
        except Exception:
            pass
    return datetime.now(UTC).isoformat()


def _header_value(headers: list[dict[str, str]], name: str) -> str:
    target = name.lower()
    for item in headers:
        if str(item.get("name", "")).lower() == target:
            return str(item.get("value", ""))
    return ""


def _decode_b64url(data: str) -> str:
    if not data:
        return ""
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("ascii")).decode("utf-8", errors="replace")


def _strip_html(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", html.unescape(cleaned)).strip()


def _extract_mime_body(payload: dict[str, Any], *, max_chars: int = 6000) -> str:
    """Recursively extract plain text (or stripped HTML) from a Gmail MIME payload."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def _walk(node: dict[str, Any]) -> None:
        mime = str(node.get("mimeType") or "").lower()
        body_data = str((node.get("body") or {}).get("data") or "")
        if body_data and mime == "text/plain":
            plain_parts.append(_decode_b64url(body_data))
        elif body_data and mime == "text/html":
            html_parts.append(_strip_html(_decode_b64url(body_data)))
        for child in node.get("parts") or []:
            if isinstance(child, dict):
                _walk(child)

    _walk(payload)
    text = "\n".join(p.strip() for p in plain_parts if p.strip())
    if not text:
        text = "\n".join(h for h in html_parts if h)
    return text[:max_chars]


@dataclass(frozen=True)
class GoogleOAuthConfig:
    """OAuth2 client configuration loaded from environment variables."""

    client_id: str
    client_secret: str
    redirect_uri: str
    refresh_token: str
    token_path: Path

    @classmethod
    def from_env(cls) -> GoogleOAuthConfig:
        """Construct OAuth configuration from environment variables."""
        return cls(
            client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
            or DEFAULT_REDIRECT_URI,
            refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN", "").strip(),
            token_path=_default_token_path(),
        )

    @property
    def is_client_configured(self) -> bool:
        """Return True when both OAuth client ID and client secret are set."""
        return bool(self.client_id and self.client_secret)


class GoogleWorkspaceClient:
    """Async/sync REST client for Google OAuth2, Gmail, and Google Calendar."""

    def __init__(
        self,
        config: GoogleOAuthConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or GoogleOAuthConfig.from_env()
        self._transport = transport
        self._async_transport = async_transport

    def _load_token_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.config.token_path.exists():
            try:
                loaded = json.loads(self.config.token_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        if self.config.refresh_token and not data.get("refresh_token"):
            data["refresh_token"] = self.config.refresh_token
        return data

    def _save_token_data(self, data: dict[str, Any]) -> None:
        self.config.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.token_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            os.chmod(self.config.token_path, 0o600)
        except OSError:
            pass

    def disconnect(self) -> bool:
        """Delete the cached OAuth token file if present."""
        if self.config.token_path.exists():
            self.config.token_path.unlink()
            return True
        return False

    def is_connected(self) -> bool:
        """Return True when OAuth client settings and a saved token are available."""
        if not self.config.is_client_configured:
            return False
        data = self._load_token_data()
        return bool(data.get("refresh_token") or data.get("access_token"))

    def status(self) -> dict[str, Any]:
        """Return current Google Workspace OAuth configuration and connection status."""
        data = self._load_token_data()
        connected = bool(
            self.config.is_client_configured
            and (data.get("refresh_token") or data.get("access_token"))
        )
        return {
            "configured": self.config.is_client_configured,
            "connected": connected,
            "email": data.get("email") if connected else None,
            "scopes": data.get("scopes") or list(GOOGLE_SCOPES) if connected else [],
            "redirect_uri": self.config.redirect_uri,
        }

    def build_auth_url(self, state: str) -> str:
        """Build the Google OAuth2 authorization URL with offline refresh access."""
        if not self.config.is_client_configured:
            raise GoogleNotConfiguredError(
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first."
            )
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    def _sync_client(self) -> httpx.Client:
        return httpx.Client(timeout=15.0, transport=self._transport)

    def _async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15.0, transport=self._async_transport)

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange an OAuth authorization code for tokens and cache profile email."""
        if not self.config.is_client_configured:
            raise GoogleNotConfiguredError(
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first."
            )
        async with self._async_client() as client:
            resp = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "redirect_uri": self.config.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            tok = resp.json()

            access_token = str(tok.get("access_token", ""))
            expires_in = int(tok.get("expires_in", 3600))
            email: str | None = None
            if access_token:
                u_resp = await client.get(
                    GOOGLE_USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if u_resp.status_code < 400:
                    email = u_resp.json().get("email")

        existing = self._load_token_data()
        record = {
            "access_token": access_token,
            "refresh_token": tok.get("refresh_token") or existing.get("refresh_token", ""),
            "expires_at": time.time() + max(60, expires_in - 60),
            "email": email or existing.get("email"),
            "scopes": str(tok.get("scope", "")).split() or list(GOOGLE_SCOPES),
        }
        self._save_token_data(record)
        return record

    def _refresh_access_token_sync(self) -> str:
        data = self._load_token_data()
        now = time.time()
        cached_token = str(data.get("access_token", ""))
        if cached_token and float(data.get("expires_at", 0)) > now + 30:
            return cached_token
        refresh_token = str(data.get("refresh_token", "") or self.config.refresh_token)
        if not self.config.is_client_configured or not refresh_token:
            raise GoogleNotConfiguredError(
                "Google account is not connected. Connect Gmail & Calendar in the Cockpit UI."
            )
        with self._sync_client() as client:
            resp = client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            tok = resp.json()
        access_token = str(tok["access_token"])
        data["access_token"] = access_token
        data["refresh_token"] = refresh_token
        data["expires_at"] = now + max(60, int(tok.get("expires_in", 3600)) - 60)
        self._save_token_data(data)
        return access_token

    async def _refresh_access_token_async(self) -> str:
        data = self._load_token_data()
        now = time.time()
        cached_token = str(data.get("access_token", ""))
        if cached_token and float(data.get("expires_at", 0)) > now + 30:
            return cached_token
        refresh_token = str(data.get("refresh_token", "") or self.config.refresh_token)
        if not self.config.is_client_configured or not refresh_token:
            raise GoogleNotConfiguredError(
                "Google account is not connected. Connect Gmail & Calendar in the Cockpit UI."
            )
        async with self._async_client() as client:
            resp = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            tok = resp.json()
        access_token = str(tok["access_token"])
        data["access_token"] = access_token
        data["refresh_token"] = refresh_token
        data["expires_at"] = now + max(60, int(tok.get("expires_in", 3600)) - 60)
        self._save_token_data(data)
        return access_token

    async def _fetch_gmail_metadata(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        msg_ids: list[str],
        *,
        concurrency: int = 12,
    ) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(concurrency)

        async def _get_one(mid: str) -> dict[str, Any] | None:
            async with sem:
                resp = await client.get(
                    f"{GMAIL_API_BASE}/messages/{mid}",
                    headers=headers,
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["From", "To", "Subject", "Date"],
                    },
                )
                if resp.status_code >= 400:
                    resp.raise_for_status()
                return resp.json()

        raw_items = await asyncio.gather(*(_get_one(mid) for mid in msg_ids))
        return [item for item in raw_items if item is not None]

    def _build_gmail_query(
        self,
        *,
        query: str = "",
        since_hours: int | None = None,
        sender: str = "",
        unread_only: bool = False,
    ) -> str:
        q_parts: list[str] = []
        if unread_only:
            q_parts.append("is:unread")
        if sender.strip():
            cleaned_sender = sender.strip().replace('"', "")
            sender_term = (
                f'from:"{cleaned_sender}"'
                if " " in cleaned_sender
                else f"from:{cleaned_sender}"
            )
            q_parts.append(sender_term)
        if since_hours is not None and int(since_hours) > 0:
            cutoff_epoch = int((datetime.now(UTC) - timedelta(hours=int(since_hours))).timestamp())
            q_parts.append(f"after:{cutoff_epoch}")
        if query.strip():
            q_parts.append(query.strip())
        return " ".join(q_parts).strip()

    async def gmail_search_messages(
        self,
        *,
        query: str = "",
        limit: int = 15,
        since_hours: int | None = None,
        sender: str = "",
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Search Gmail messages matching query, sender, unread_only, and since_hours."""
        token = await self._refresh_access_token_async()
        headers = {"Authorization": f"Bearer {token}"}
        safe_limit = max(1, min(int(limit), 50))
        gmail_q = self._build_gmail_query(
            query=query,
            since_hours=since_hours,
            sender=sender,
            unread_only=unread_only,
        )

        async with self._async_client() as client:
            all_ids: list[str] = []
            page_token: str | None = None
            estimate = 0
            max_id_pages = 3 if (since_hours or sender.strip() or unread_only) else 1
            for _ in range(max_id_pages):
                params: dict[str, Any] = {"maxResults": min(100, max(safe_limit, 50))}
                if gmail_q:
                    params["q"] = gmail_q
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(
                    f"{GMAIL_API_BASE}/messages", headers=headers, params=params
                )
                resp.raise_for_status()
                body = resp.json()
                if not estimate:
                    estimate = int(body.get("resultSizeEstimate", 0) or 0)
                batch = [str(m["id"]) for m in body.get("messages", []) if m.get("id")]
                all_ids.extend(batch)
                page_token = body.get("nextPageToken")
                if not page_token or len(all_ids) >= 250:
                    break

            matched_total = len(all_ids) if not page_token else max(len(all_ids), estimate)
            meta_items = await self._fetch_gmail_metadata(
                client, headers, all_ids[:safe_limit], concurrency=12
            )
            results: list[dict[str, Any]] = []
            for m_data in meta_items:
                hdrs = (m_data.get("payload") or {}).get("headers") or []
                labels = m_data.get("labelIds") or []
                results.append(
                    {
                        "id": str(m_data.get("id", "")),
                        "from": _header_value(hdrs, "From"),
                        "subject": _header_value(hdrs, "Subject")[:90],
                        "snippet": str(m_data.get("snippet", ""))[:140],
                        "created": _parse_email_date(
                            _header_value(hdrs, "Date"), m_data.get("internalDate")
                        ),
                        "unread": "UNREAD" in labels,
                        "provider": "gmail",
                        "matched_total": matched_total,
                    }
                )
            return results

    async def _fetch_label_counts(
        self, client: httpx.AsyncClient, headers: dict[str, str]
    ) -> dict[str, int]:
        """Fetch Primary Inbox unread plus category and total Gmail unread counts."""
        out: dict[str, int] = {}

        async def _get_label(label_id: str) -> None:
            try:
                resp = await client.get(
                    f"{GMAIL_API_BASE}/labels/{label_id}", headers=headers
                )
                if resp.status_code != 200:
                    return
                data = resp.json()
                unread_msgs = int(data.get("messagesUnread", 0) or 0)
                if label_id == "INBOX":
                    out["inbox_total"] = int(data.get("messagesTotal", 0) or 0)
                    out["inbox_unread"] = unread_msgs
                    out["all_inbox_unread"] = unread_msgs
                elif label_id == "UNREAD":
                    out["all_unread_total"] = int(data.get("messagesTotal", 0) or 0)
                elif label_id == "CATEGORY_PROMOTIONS":
                    out["promotions_unread"] = unread_msgs
                elif label_id == "CATEGORY_SOCIAL":
                    out["social_unread"] = unread_msgs
                elif label_id == "CATEGORY_UPDATES":
                    out["updates_unread"] = unread_msgs
            except Exception:
                pass

        async def _get_primary_unread() -> None:
            try:
                count = 0
                page_tok: str | None = None
                est = 0
                for _ in range(3):
                    params: dict[str, Any] = {
                        "q": "in:inbox is:unread category:primary",
                        "maxResults": 500,
                    }
                    if page_tok:
                        params["pageToken"] = page_tok
                    resp = await client.get(
                        f"{GMAIL_API_BASE}/threads", headers=headers, params=params
                    )
                    if resp.status_code != 200:
                        return
                    body = resp.json()
                    if not est:
                        est = int(body.get("resultSizeEstimate", 0) or 0)
                    count += len(body.get("threads") or [])
                    page_tok = body.get("nextPageToken")
                    if not page_tok:
                        break
                out["primary_inbox_unread"] = count if not page_tok else max(count, est)
            except Exception:
                pass

        await asyncio.gather(
            _get_label("INBOX"),
            _get_label("UNREAD"),
            _get_label("CATEGORY_PROMOTIONS"),
            _get_label("CATEGORY_SOCIAL"),
            _get_label("CATEGORY_UPDATES"),
            _get_primary_unread(),
        )
        return out

    async def gmail_analyze_inbox(
        self,
        *,
        since_hours: int | None = 0,
        query: str = "",
        unread_only: bool = False,
        top_n: int = 5,
    ) -> dict[str, Any]:
        """Count total/unread emails and rank top senders (since_hours=0 means all time)."""
        effective_hours = int(since_hours) if since_hours and int(since_hours) > 0 else 0
        token = await self._refresh_access_token_async()
        headers = {"Authorization": f"Bearer {token}"}
        gmail_q = self._build_gmail_query(
            query=query,
            since_hours=effective_hours if effective_hours > 0 else None,
            unread_only=unread_only,
        )

        async with self._async_client() as client:
            label_counts = (
                await self._fetch_label_counts(client, headers)
                if effective_hours == 0 and not query.strip()
                else {}
            )
            all_ids: list[str] = []
            page_token: str | None = None
            estimate = 0
            max_pages = 3 if effective_hours == 0 else 5
            for _ in range(max_pages):
                params: dict[str, Any] = {"maxResults": 100}
                if gmail_q:
                    params["q"] = gmail_q
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(
                    f"{GMAIL_API_BASE}/messages", headers=headers, params=params
                )
                resp.raise_for_status()
                body = resp.json()
                if not estimate:
                    estimate = int(body.get("resultSizeEstimate", 0) or 0)
                batch = [str(m["id"]) for m in body.get("messages", []) if m.get("id")]
                all_ids.extend(batch)
                page_token = body.get("nextPageToken")
                if not page_token or len(all_ids) >= (150 if effective_hours == 0 else 500):
                    break

            total_matching = len(all_ids) if not page_token else max(len(all_ids), estimate)
            if unread_only and "all_unread_total" in label_counts:
                total_matching = max(total_matching, label_counts["all_unread_total"])
            meta_limit = 50 if effective_hours == 0 else 250
            meta_items = await self._fetch_gmail_metadata(
                client, headers, all_ids[:meta_limit], concurrency=20
            )

        counts: Counter[str] = Counter()
        display_names: dict[str, str] = {}
        unread_per_sender: Counter[str] = Counter()
        latest_subject: dict[str, str] = {}
        unread_count = 0
        recent_subjects: list[dict[str, str]] = []

        for m_data in meta_items:
            hdrs = (m_data.get("payload") or {}).get("headers") or []
            raw_from = _header_value(hdrs, "From")
            name, addr = parseaddr(raw_from)
            key = (addr or raw_from or "(unknown)").strip().lower()
            subject = _header_value(hdrs, "Subject")[:80]
            is_unread = "UNREAD" in (m_data.get("labelIds") or [])
            if is_unread:
                unread_count += 1
                unread_per_sender[key] += 1
            counts[key] += 1
            if key not in display_names:
                display_names[key] = raw_from or name or key
            if key not in latest_subject and subject:
                latest_subject[key] = subject
            if len(recent_subjects) < 5:
                recent_subjects.append(
                    {"id": str(m_data.get("id", "")), "from": raw_from[:60], "subject": subject}
                )

        safe_top = max(1, min(int(top_n), 15))
        top_senders = [
            {
                "rank": idx + 1,
                "email": key,
                "sender": display_names.get(key, key)[:70],
                "count": cnt,
                "unread_count": unread_per_sender.get(key, 0),
                "latest_subject": latest_subject.get(key, ""),
            }
            for idx, (key, cnt) in enumerate(counts.most_common(safe_top))
        ]

        result: dict[str, Any] = {
            "provider": "gmail",
            "since_hours": effective_hours,
            "query": query,
            "unread_only": unread_only,
            "total_matching": total_matching,
            "analyzed_count": len(meta_items),
            "unread_count": label_counts.get("all_unread_total", unread_count),
            "top_senders": top_senders,
            "recent_subjects": recent_subjects,
        }
        if label_counts:
            result.update(label_counts)
        return result

    async def gmail_read_message(self, message_id: str) -> dict[str, Any]:
        """Fetch full headers and decoded body text for a single Gmail message ID."""
        cleaned_id = (message_id or "").strip()
        if not cleaned_id:
            raise ValueError("message_id is required")
        token = await self._refresh_access_token_async()
        headers = {"Authorization": f"Bearer {token}"}
        async with self._async_client() as client:
            resp = await client.get(
                f"{GMAIL_API_BASE}/messages/{cleaned_id}",
                headers=headers,
                params={"format": "full"},
            )
            resp.raise_for_status()
            m_data = resp.json()

        payload = m_data.get("payload") or {}
        hdrs = payload.get("headers") or []
        labels = m_data.get("labelIds") or []
        body_text = _extract_mime_body(payload) or str(m_data.get("snippet") or "")
        return {
            "id": str(m_data.get("id", cleaned_id)),
            "thread_id": str(m_data.get("threadId", "")),
            "from": _header_value(hdrs, "From"),
            "to": _header_value(hdrs, "To"),
            "cc": _header_value(hdrs, "Cc"),
            "subject": _header_value(hdrs, "Subject"),
            "created": _parse_email_date(_header_value(hdrs, "Date"), m_data.get("internalDate")),
            "unread": "UNREAD" in labels,
            "body": body_text,
            "provider": "gmail",
        }

    def gmail_send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        sender: str | None = None,
    ) -> dict[str, Any]:
        """Send a UTF-8 email through the connected user's Gmail account."""
        token = self._refresh_access_token_sync()
        data = self._load_token_data()
        from_addr = sender or str(data.get("email") or "me")

        msg = EmailMessage()
        if from_addr != "me":
            msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        encoded_raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        with self._sync_client() as client:
            resp = client.post(
                f"{GMAIL_API_BASE}/messages/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": encoded_raw},
            )
            resp.raise_for_status()
            sent = resp.json()

        return {
            "status": "sent",
            "id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": to,
            "subject": subject,
            "transport": "gmail",
        }

    def calendar_list_events(
        self,
        day_iso: str | None = None,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        query: str = "",
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """List primary Google Calendar events across any date, open range, or keyword query."""
        token = self._refresh_access_token_sync()
        range_start = start_date or day_iso
        range_end = end_date or day_iso
        if window_start is not None and window_end is not None:
            t_min, t_max = window_start.astimezone(UTC), window_end.astimezone(UTC)
        elif range_start:
            s_dt = _parse_iso(range_start).astimezone(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            e_dt = (
                _parse_iso(range_end).astimezone(UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                if range_end
                else s_dt + timedelta(days=14)
            )
            # Include 14h tz padding before exact calendar-timezone window filtering below
            t_min = s_dt - timedelta(hours=14)
            t_max = e_dt + timedelta(hours=38)
        else:
            t_min = datetime.now(UTC) - timedelta(hours=1)
            t_max = datetime.now(UTC) + timedelta(days=14)

        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": t_min.isoformat(),
            "timeMax": t_max.isoformat(),
            "maxResults": 100,
        }
        if query.strip():
            params["q"] = query.strip()

        with self._sync_client() as client:
            resp = client.get(
                f"{CALENDAR_API_BASE}/events",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            body = resp.json()
            items = body.get("items", [])

        cal_tz_name = str(body.get("timeZone") or "").strip()
        cal_tz: tzinfo = UTC
        if cal_tz_name:
            try:
                cal_tz = ZoneInfo(cal_tz_name)
            except Exception:
                cal_tz = UTC

        day_filter_start: datetime | None = None
        day_filter_end: datetime | None = None
        if range_start and window_start is None and window_end is None:
            b_start = _parse_iso(range_start)
            day_filter_start = datetime(
                b_start.year, b_start.month, b_start.day, 0, 0, 0, tzinfo=cal_tz
            )
            if range_end:
                b_end = _parse_iso(range_end)
                day_filter_end = datetime(
                    b_end.year, b_end.month, b_end.day, 0, 0, 0, tzinfo=cal_tz
                ) + timedelta(days=1)
            else:
                day_filter_end = day_filter_start + timedelta(days=14)

        normalized: list[dict[str, Any]] = []
        for ev in items:
            if ev.get("status") == "cancelled":
                continue
            start_obj = ev.get("start") or {}
            end_obj = ev.get("end") or {}
            start_val = start_obj.get("dateTime") or start_obj.get("date")
            end_val = end_obj.get("dateTime") or end_obj.get("date")
            if not start_val or not end_val:
                continue
            s_utc = _parse_iso(str(start_val)).astimezone(UTC)
            e_utc = _parse_iso(str(end_val)).astimezone(UTC)
            if day_filter_start is not None and day_filter_end is not None:
                win_start = day_filter_start.astimezone(UTC)
                win_end = day_filter_end.astimezone(UTC)
                if not (s_utc < win_end and e_utc > win_start):
                    continue
            normalized.append(
                {
                    "id": ev.get("id"),
                    "title": ev.get("summary") or "(Untitled Event)",
                    "organizer": (ev.get("organizer") or {}).get("email", ""),
                    "attendees": [
                        a.get("email") for a in (ev.get("attendees") or []) if a.get("email")
                    ],
                    "start_time": s_utc.isoformat(),
                    "end_time": e_utc.isoformat(),
                    "local_start": s_utc.astimezone(cal_tz).isoformat(),
                    "local_end": e_utc.astimezone(cal_tz).isoformat(),
                    "timezone": cal_tz_name or "UTC",
                    "location": ev.get("location") or "",
                    "notes": ev.get("description") or "",
                    "provider": "google_calendar",
                }
            )
        return normalized

    def calendar_create_event(
        self,
        *,
        title: str,
        start_iso: str,
        duration_minutes: int = 30,
        attendees: list[str] | None = None,
        location: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Schedule an event on the user's primary Google Calendar after overlap check."""
        start_dt = _parse_iso(start_iso).astimezone(UTC)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        existing = self.calendar_list_events(window_start=start_dt, window_end=end_dt)
        for item in existing:
            b_start = _parse_iso(str(item["start_time"])).astimezone(UTC)
            b_end = _parse_iso(str(item["end_time"])).astimezone(UTC)
            if start_dt < b_end and end_dt > b_start:
                raise GoogleCalendarConflictError(
                    f"Time slot conflicts with Google Calendar event '{item['title']}'"
                )

        token = self._refresh_access_token_sync()
        payload: dict[str, Any] = {
            "summary": title,
            "description": notes,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }
        if location:
            payload["location"] = location
        if attendees:
            payload["attendees"] = [{"email": addr} for addr in attendees]

        with self._sync_client() as client:
            resp = client.post(
                f"{CALENDAR_API_BASE}/events",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            resp.raise_for_status()
            created = resp.json()

        return {
            "id": created.get("id"),
            "title": created.get("summary") or title,
            "start_time": (created.get("start") or {}).get("dateTime") or start_dt.isoformat(),
            "end_time": (created.get("end") or {}).get("dateTime") or end_dt.isoformat(),
            "location": created.get("location") or location,
            "html_link": created.get("htmlLink") or "",
            "status": "scheduled",
            "transport": "google_calendar",
        }

    def calendar_update_event(
        self,
        *,
        event_id: str,
        title: str | None = None,
        start_iso: str | None = None,
        duration_minutes: int | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update/reschedule an existing primary Google Calendar event."""
        token = self._refresh_access_token_sync()
        headers = {"Authorization": f"Bearer {token}"}
        with self._sync_client() as client:
            cur_resp = client.get(f"{CALENDAR_API_BASE}/events/{event_id}", headers=headers)
            cur_resp.raise_for_status()
            existing_ev = cur_resp.json()

        patch: dict[str, Any] = {}
        if title is not None:
            patch["summary"] = title
        if location is not None:
            patch["location"] = location
        if notes is not None:
            patch["description"] = notes

        if start_iso is not None or duration_minutes is not None:
            orig_start_raw = (existing_ev.get("start") or {}).get("dateTime") or (
                existing_ev.get("start") or {}
            ).get("date")
            orig_end_raw = (existing_ev.get("end") or {}).get("dateTime") or (
                existing_ev.get("end") or {}
            ).get("date")
            orig_start = _parse_iso(str(orig_start_raw)).astimezone(UTC)
            orig_end = _parse_iso(str(orig_end_raw)).astimezone(UTC)
            orig_dur = max(15, int((orig_end - orig_start).total_seconds() // 60))
            new_start = _parse_iso(start_iso).astimezone(UTC) if start_iso else orig_start
            new_dur = int(duration_minutes) if duration_minutes is not None else orig_dur
            new_end = new_start + timedelta(minutes=new_dur)

            overlaps = self.calendar_list_events(window_start=new_start, window_end=new_end)
            for item in overlaps:
                if str(item.get("id")) == str(event_id):
                    continue
                b_start = _parse_iso(str(item["start_time"])).astimezone(UTC)
                b_end = _parse_iso(str(item["end_time"])).astimezone(UTC)
                if new_start < b_end and new_end > b_start:
                    raise GoogleCalendarConflictError(
                        f"New slot conflicts with Google Calendar event '{item['title']}'"
                    )
            patch["start"] = {"dateTime": new_start.isoformat()}
            patch["end"] = {"dateTime": new_end.isoformat()}

        with self._sync_client() as client:
            resp = client.patch(
                f"{CALENDAR_API_BASE}/events/{event_id}",
                headers=headers,
                json=patch,
            )
            resp.raise_for_status()
            updated = resp.json()

        return {
            "id": updated.get("id", event_id),
            "title": updated.get("summary") or title or "",
            "start_time": (updated.get("start") or {}).get("dateTime") or "",
            "end_time": (updated.get("end") or {}).get("dateTime") or "",
            "location": updated.get("location") or "",
            "status": "updated",
            "transport": "google_calendar",
        }

    def calendar_delete_event(self, *, event_id: str) -> dict[str, Any]:
        """Cancel/delete an event from the primary Google Calendar."""
        token = self._refresh_access_token_sync()
        with self._sync_client() as client:
            resp = client.delete(
                f"{CALENDAR_API_BASE}/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        return {"id": event_id, "status": "deleted", "transport": "google_calendar"}

    async def api_get(
        self,
        *,
        service: str,
        path: str,
        params: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> Any:
        """Execute a read-only GET request against Gmail v1 or Google Calendar v3."""
        svc = (service or "").strip().lower()
        bases = {
            "gmail": "https://gmail.googleapis.com/gmail/v1",
            "calendar": "https://www.googleapis.com/calendar/v3",
        }
        if svc not in bases:
            raise ValueError("service must be 'gmail' or 'calendar'.")
        cleaned = (path or "").strip()
        if (
            not cleaned
            or "://" in cleaned
            or ".." in cleaned
            or "@" in cleaned
            or any(ch.isspace() for ch in cleaned)
        ):
            raise ValueError(f"Invalid Google API path: {cleaned!r}")
        if not cleaned.startswith("/"):
            cleaned = f"/{cleaned}"
        redundant_prefix = "/gmail/v1" if svc == "gmail" else "/calendar/v3"
        if cleaned.lower().startswith(f"{redundant_prefix}/"):
            cleaned = cleaned[len(redundant_prefix) :]
        elif cleaned.lower() == redundant_prefix:
            cleaned = "/"
        token = await self._refresh_access_token_async()
        safe_limit = max(1, min(int(limit), 30))
        async with self._async_client() as client:
            resp = await client.get(
                f"{bases[svc]}{cleaned}",
                headers={"Authorization": f"Bearer {token}"},
                params=dict(params) if params else None,
            )
            if resp.status_code >= 400:
                return {
                    "error": f"Google {svc} API returned HTTP {resp.status_code}",
                    "path": cleaned,
                    "details": resp.text[:300],
                }
            return _compact_google_payload(resp.json(), limit=safe_limit)


def _compact_google_payload(data: Any, limit: int = 20, _depth: int = 0) -> Any:
    """Strip verbose metadata and cap list lengths so Google API JSON stays token-safe."""
    if _depth > 5:
        return "..."
    safe_limit = max(1, min(int(limit), 30))
    if isinstance(data, list):
        return [
            _compact_google_payload(item, limit=safe_limit, _depth=_depth + 1)
            for item in data[:safe_limit]
        ]
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            if k in ("etag", "kind", "htmlLink", "iCalUID", "sequence"):
                continue
            if k == "data" and isinstance(v, str) and len(v) > 200:
                continue
            out[k] = _compact_google_payload(v, limit=safe_limit, _depth=_depth + 1)
        return out
    if isinstance(data, str) and len(data) > 1200:
        return data[:1200] + "...[truncated]"
    return data
