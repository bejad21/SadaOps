"""PostgreSQL-backed Human-in-the-Loop (HITL) parameter-locked approval gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg
from pydantic import BaseModel

from agent.safety.prompt_guard import validate_write_policy

DEFAULT_DATABASE_URL = "postgresql://sadaops:sadaops_dev_pass@127.0.0.1:5432/sadaops"

_REJECT_PATTERN = re.compile(
    r"(?:(?<!\w)(?:don't send|do not send|not approved|no|cancel|reject|abort|stop)(?!\w)"
    r"|(?<!\w)(?:مش موافق|غير موافق|لا|إلغاء|الغاء|ارفض|وقف)(?!\w))",
    re.IGNORECASE,
)
_APPROVE_PATTERN = re.compile(
    r"(?:(?<!\w)(?:yes|confirm|approve|approved|send it|go ahead|proceed)(?!\w)"
    r"|(?<!\w)(?:أيوه|ايوه|تمام|موافق|وافق|ابعته|أكد|اكد)(?!\w))",
    re.IGNORECASE,
)
_REVISION_PATTERN = re.compile(
    r"(?:(?<!\w)(?:change|edit|update|modify|fix|replace|instead|make\s+it|set\s+the)(?!\w)"
    r"|(?:عدل|عدّل|غير|غيّر|بدل|بدّل|صلح|خلي|خلّي))",
    re.IGNORECASE,
)


class ActionNotFoundError(LookupError):
    """Raised when an action_id does not exist."""


class ActionExpiredError(ValueError):
    """Raised when a pending action TTL has elapsed."""


class ActionConflictError(RuntimeError):
    """Raised when an action has already been decided or executed."""


class PayloadTamperedError(ValueError):
    """Raised when expected_payload_hash does not match the locked payload hash."""


class PendingAction(BaseModel):
    """Locked write-tool proposal stored in PostgreSQL."""

    action_id: str
    session_id: str
    tool_name: str
    payload: dict[str, Any]
    payload_hash: str
    idempotency_key: str
    status: Literal["pending", "approved", "rejected", "executed", "failed", "expired"]
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None = None
    result: Any = None


def compute_payload_hash(tool_name: str, payload: dict[str, Any]) -> str:
    """Compute canonical SHA-256 hash locking tool_name and exact payload arguments."""
    canonical = json.dumps(
        {"tool": tool_name, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_voice_decision(text: str) -> Literal["approve", "reject", "none"]:
    """Classify short bilingual operator confirmation or rejection utterances safely."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "none"
    # Revision utterances ("No, change the recipient to ...") should route to update_pending_action
    # rather than immediately rejecting the pending HITL card.
    if _REVISION_PATTERN.search(cleaned):
        return "none"
    if _REJECT_PATTERN.search(cleaned):
        return "reject"
    if _APPROVE_PATTERN.search(cleaned):
        return "approve"
    return "none"


def _row_to_action(row: tuple[Any, ...]) -> PendingAction:
    (
        action_id,
        session_id,
        tool_name,
        payload_json,
        payload_hash,
        idempotency_key,
        status,
        expires_at,
        created_at,
        decided_at,
        result_json,
    ) = row
    payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
    if isinstance(result_json, str):
        result = json.loads(result_json)
    else:
        result = result_json
    return PendingAction(
        action_id=action_id,
        session_id=session_id,
        tool_name=tool_name,
        payload=payload,
        payload_hash=payload_hash,
        idempotency_key=idempotency_key,
        status=status,
        expires_at=expires_at,
        created_at=created_at,
        decided_at=decided_at,
        result=result,
    )


_SELECT_COLS = (
    "action_id, session_id, tool_name, payload_json, payload_hash, "
    "idempotency_key, status, expires_at, created_at, decided_at, result_json"
)


class HITLGate:
    """Manages parameter-locked write proposals and atomic approval in PostgreSQL."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

    def propose_action(
        self,
        session_id: str,
        tool_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        ttl_seconds: int = 120,
        writes_in_turn: int = 0,
        controls_enabled: bool = True,
    ) -> PendingAction:
        """Validate policy, lock payload hash, and persist a pending approval record."""
        validate_write_policy(
            tool_name,
            payload,
            controls_enabled=controls_enabled,
            writes_in_turn=writes_in_turn,
        )
        payload_hash = compute_payload_hash(tool_name, payload)
        idem_key = idempotency_key or f"idem-{uuid.uuid4().hex}"
        action_id = f"act-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)

        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pending_actions "
                    "(action_id, session_id, tool_name, payload_json, payload_hash, "
                    "idempotency_key, status, expires_at, created_at) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s, %s, 'pending', %s, %s) "
                    "ON CONFLICT (idempotency_key) DO NOTHING "
                    f"RETURNING {_SELECT_COLS}",
                    (
                        action_id,
                        session_id,
                        tool_name,
                        json.dumps(payload, ensure_ascii=False),
                        payload_hash,
                        idem_key,
                        expires_at,
                        now,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        f"SELECT {_SELECT_COLS} FROM pending_actions WHERE idempotency_key = %s",
                        (idem_key,),
                    )
                    row = cur.fetchone()
                conn.commit()
                assert row is not None
                return _row_to_action(row)

    def _claim_for_execution(self, action_id: str, expected_payload_hash: str) -> PendingAction:
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM pending_actions "
                    "WHERE action_id = %s FOR UPDATE",
                    (action_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ActionNotFoundError(f"Action '{action_id}' not found.")

                action = _row_to_action(row)
                if action.status != "pending":
                    raise ActionConflictError(
                        f"Action '{action_id}' is already in status '{action.status}'."
                    )

                if action.expires_at <= now:
                    cur.execute(
                        "UPDATE pending_actions SET status = 'expired', decided_at = %s "
                        "WHERE action_id = %s",
                        (now, action_id),
                    )
                    conn.commit()
                    raise ActionExpiredError(f"Action '{action_id}' has expired.")

                if expected_payload_hash != action.payload_hash:
                    raise PayloadTamperedError(
                        f"Payload hash mismatch for action '{action_id}'."
                    )

                cur.execute(
                    "UPDATE pending_actions SET status = 'approved', decided_at = %s "
                    f"WHERE action_id = %s RETURNING {_SELECT_COLS}",
                    (now, action_id),
                )
                claimed = cur.fetchone()
                conn.commit()
                assert claimed is not None
                return _row_to_action(claimed)

    def _mark_outcome(self, action_id: str, status: str, result: Any) -> PendingAction:
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pending_actions SET status = %s, result_json = %s::jsonb "
                    f"WHERE action_id = %s RETURNING {_SELECT_COLS}",
                    (status, json.dumps(result, ensure_ascii=False, default=str), action_id),
                )
                row = cur.fetchone()
                conn.commit()
                assert row is not None
                return _row_to_action(row)

    async def approve_and_execute(
        self,
        action_id: str,
        expected_payload_hash: str,
        executor: Callable[[str, dict[str, Any]], Awaitable[Any]],
    ) -> PendingAction:
        """Atomically claim a locked action and execute its exact persisted payload once."""
        claimed = await asyncio.to_thread(
            self._claim_for_execution, action_id, expected_payload_hash
        )
        try:
            result = await executor(claimed.tool_name, claimed.payload)
        except Exception as exc:
            await asyncio.to_thread(
                self._mark_outcome, action_id, "failed", {"error": str(exc)}
            )
            raise
        return await asyncio.to_thread(self._mark_outcome, action_id, "executed", result)

    def reject_action(self, action_id: str) -> PendingAction:
        """Reject a pending action atomically."""
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM pending_actions "
                    "WHERE action_id = %s FOR UPDATE",
                    (action_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ActionNotFoundError(f"Action '{action_id}' not found.")
                action = _row_to_action(row)
                if action.status != "pending":
                    raise ActionConflictError(
                        f"Action '{action_id}' is already in status '{action.status}'."
                    )
                if action.expires_at <= now:
                    cur.execute(
                        "UPDATE pending_actions SET status = 'expired', decided_at = %s "
                        "WHERE action_id = %s",
                        (now, action_id),
                    )
                    conn.commit()
                    raise ActionExpiredError(f"Action '{action_id}' has expired.")
                cur.execute(
                    "UPDATE pending_actions SET status = 'rejected', decided_at = %s "
                    f"WHERE action_id = %s RETURNING {_SELECT_COLS}",
                    (now, action_id),
                )
                updated = cur.fetchone()
                conn.commit()
                assert updated is not None
                return _row_to_action(updated)

    def list_pending(self, session_id: str | None = None) -> list[PendingAction]:
        """Expire stale pending rows and list active pending proposals."""
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pending_actions SET status = 'expired', decided_at = %s "
                    "WHERE status = 'pending' AND expires_at <= %s",
                    (now, now),
                )
                if session_id:
                    cur.execute(
                        f"SELECT {_SELECT_COLS} FROM pending_actions "
                        "WHERE status = 'pending' AND session_id = %s "
                        "ORDER BY created_at DESC LIMIT 50",
                        (session_id,),
                    )
                else:
                    cur.execute(
                        f"SELECT {_SELECT_COLS} FROM pending_actions "
                        "WHERE status = 'pending' ORDER BY created_at DESC LIMIT 50"
                    )
                rows = cur.fetchall()
                conn.commit()
                return [_row_to_action(r) for r in rows]

    def get_action(self, action_id: str) -> PendingAction | None:
        """Fetch an action by ID or return None."""
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM pending_actions WHERE action_id = %s",
                    (action_id,),
                )
                row = cur.fetchone()
                return _row_to_action(row) if row is not None else None

    def update_pending_payload(
        self,
        action_id: str,
        new_payload: dict[str, Any],
        *,
        controls_enabled: bool = True,
    ) -> PendingAction:
        """Re-validate policy, update parameters, and recompute canonical SHA-256 lock hash."""
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM pending_actions "
                    "WHERE action_id = %s FOR UPDATE",
                    (action_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ActionNotFoundError(f"Action '{action_id}' not found.")

                action = _row_to_action(row)
                if action.status != "pending":
                    raise ActionConflictError(
                        f"Action '{action_id}' is already in status '{action.status}'."
                    )

                if action.expires_at <= now:
                    cur.execute(
                        "UPDATE pending_actions SET status = 'expired', decided_at = %s "
                        "WHERE action_id = %s",
                        (now, action_id),
                    )
                    conn.commit()
                    raise ActionExpiredError(f"Action '{action_id}' has expired.")

                validate_write_policy(
                    action.tool_name,
                    new_payload,
                    controls_enabled=controls_enabled,
                    writes_in_turn=0,
                )
                new_hash = compute_payload_hash(action.tool_name, new_payload)
                cur.execute(
                    "UPDATE pending_actions SET payload_json = %s::jsonb, payload_hash = %s "
                    f"WHERE action_id = %s RETURNING {_SELECT_COLS}",
                    (
                        json.dumps(new_payload, ensure_ascii=False),
                        new_hash,
                        action_id,
                    ),
                )
                updated = cur.fetchone()
                conn.commit()
                assert updated is not None
                return _row_to_action(updated)
