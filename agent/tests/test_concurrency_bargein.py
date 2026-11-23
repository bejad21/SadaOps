"""Concurrency and mid-tool barge-in stress tests for Phase 3."""

from __future__ import annotations

import asyncio
import uuid

import psycopg
import pytest

from agent.pipeline.telemetry import TurnLatencyRecord
from agent.pipeline.tool_router import DualSpeedToolRouter
from agent.pipeline.turn_manager import TurnManager, reconcile_interrupted_history
from agent.safety.hitl_gate import ActionConflictError, HITLGate


@pytest.mark.asyncio
async def test_10_mid_tool_barge_in_interruptions_cleanly_reconcile() -> None:
    """Simulate 10 consecutive mid-tool user interruptions without orphaned tool_call_ids."""
    tm = TurnManager()

    async def slow_mcp_tool(name: str, args: dict):
        await asyncio.sleep(2.0)
        return {"ok": True}

    router = DualSpeedToolRouter(caller=slow_mcp_tool, turn_manager=tm)

    for idx in range(10):
        turn_id = f"barge-{idx}"
        await tm.start_listening(turn_id)
        await tm.start_thinking()

        rec = TurnLatencyRecord(turn_id=turn_id, session_id="stress-test")
        history = [
            {"role": "user", "content": f"Check runbooks #{idx}"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{idx}",
                        "type": "function",
                        "function": {"name": "search_runbooks"},
                    }
                ],
            },
        ]

        tool_task = asyncio.create_task(
            router.execute("search_runbooks", {"query": "redis"}, latency_record=rec)
        )
        await asyncio.sleep(0.03)
        interrupted = await tm.handle_barge_in(history=history)
        assert interrupted is True

        res = await tool_task
        assert res.error == "cancelled by user interruption"
        assert rec.interrupted is True

        reconcile_interrupted_history(history)
        tool_replies = [m for m in history if m.get("role") == "tool"]
        assert len(tool_replies) == 1
        assert tool_replies[0]["tool_call_id"] == f"call_{idx}"
        await tm.finish_turn()


@pytest.mark.asyncio
async def test_simultaneous_voice_and_ui_approval_race_executes_once() -> None:
    """6 concurrent approve_and_execute calls on one locked action must execute exactly once."""
    sess_id = f"race-{uuid.uuid4().hex[:8]}"
    gate = HITLGate()
    exec_count = 0

    async def counting_executor(tool_name: str, payload: dict):
        nonlocal exec_count
        exec_count += 1
        await asyncio.sleep(0.05)
        return {"sent": True, "tool": tool_name, "to": payload["to"]}

    action = gate.propose_action(
        sess_id,
        "send_email",
        {"to": "ops-leads@example.com", "subject": "Race Check", "body": "Locked payload"},
    )
    try:
        results = await asyncio.gather(
            *[
                gate.approve_and_execute(action.action_id, action.payload_hash, counting_executor)
                for _ in range(6)
            ],
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, BaseException)]
        conflicts = [r for r in results if isinstance(r, ActionConflictError)]

        assert len(successes) == 1
        assert len(conflicts) == 5
        assert exec_count == 1
        assert successes[0].status == "executed"
    finally:
        with psycopg.connect(gate.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pending_actions WHERE session_id = %s", (sess_id,))
                conn.commit()


@pytest.mark.asyncio
async def test_concurrent_propose_action_same_idempotency_key_returns_single_row() -> None:
    """20 simultaneous propose_action calls with identical idempotency_key must not race."""
    sess_id = f"idem-race-{uuid.uuid4().hex[:8]}"
    idem_key = f"idem-key-{uuid.uuid4().hex[:8]}"
    gate = HITLGate()
    try:
        actions = await asyncio.gather(
            *[
                asyncio.to_thread(
                    gate.propose_action,
                    sess_id,
                    "send_email",
                    {"to": "ops-leads@example.com", "subject": "Race", "body": "Dedupe"},
                    idempotency_key=idem_key,
                )
                for _ in range(20)
            ]
        )
        assert len({act.action_id for act in actions}) == 1
        assert len(gate.list_pending(sess_id)) == 1
    finally:
        with psycopg.connect(gate.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pending_actions WHERE session_id = %s", (sess_id,))
                conn.commit()
