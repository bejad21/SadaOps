"""Unit tests for Scoped Agents, AgentTask supervisor delegation, and 20B/120B routing."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents import AgentTask, llm

from agent.pipeline.orchestrator import (
    DOMAIN_TOOLS,
    SupervisorWorkflowTask,
    classify_turn_route,
    select_scoped_tools,
)
from agent.pipeline.tool_router import DualSpeedToolRouter
from agent.safety.hitl_gate import HITLGate


def test_classify_turn_route_single_vs_supervisor_bilingual() -> None:
    # Single-domain fast EN & AR turns stay on 20B with narrow toolsets
    ops = classify_turn_route("Check recent sev1 incidents for auth-gateway.")
    assert ops.domains == ("ops_db",)
    assert ops.needs_supervisor is False
    assert set(ops.tool_names) == set(DOMAIN_TOOLS["ops_db"])

    ar_pr = classify_turn_route("إيه الـ open PRs المفتوحة على bejad21/sadaops-demo؟")
    assert ar_pr.domains == ("github",)
    assert ar_pr.needs_supervisor is False

    # Single-domain multi-step cues trigger Supervisor
    gh_multi = classify_turn_route("Check open PRs and inspect failing CI run 8891.")
    assert gh_multi.domains == ("github",)
    assert gh_multi.needs_supervisor is True

    # Multi-domain EN & AR compound commands trigger 120B Supervisor & exclude unrelated domains
    en_compound = classify_turn_route(
        "Check recent Sev1 ops_incidents on Auth-gateway, search Runbooks for 502, "
        "and send an email to ops-leads@example.com summarizing the incident."
    )
    assert en_compound.needs_supervisor is True
    assert set(en_compound.domains) == {"ops_db", "knowledge", "workspace"}
    assert "list_open_prs" not in en_compound.tool_names

    ar_compound = classify_turn_route(
        "لو سمحت تشوف لي ال Open PRs وبعدين أحجز اجتماعي في التقويم يوم 15 يوليو 2030"
    )
    assert ar_compound.needs_supervisor is True
    assert set(ar_compound.domains) == {"github", "workspace"}
    assert "send_email" in ar_compound.tool_names
    assert "get_recent_incidents" not in ar_compound.tool_names

    # Ambiguous short continuation preserves prior domain scope
    cont = classify_turn_route("Yes check that again please.", active_domains=("ops_db",))
    assert cont.domains == ("ops_db",)
    assert cont.reason == "context_continuation"

    # Analytical inbox queries route flexibly across workspace + Supervisor
    top_sender = classify_turn_route(
        "Who sent me the most emails in the last 24 hours?", active_domains=("ops_db",)
    )
    assert "workspace" in top_sender.domains
    assert "analyze_inbox" in top_sender.tool_names
    assert top_sender.needs_supervisor is True

    # Open general questions shouldn't get trapped inside an unrelated prior domain
    open_q = classify_turn_route(
        "Can you summarize what matters most right now across everything?",
        active_domains=("github",),
    )
    assert open_q.reason == "all_domains_fallback"
    assert "workspace" in open_q.domains


@pytest.mark.asyncio
async def test_sadaops_agent_scopes_tools_and_swaps_llm_per_turn() -> None:
    from agent.main import SadaOpsAgent

    fast_llm = MagicMock(spec=llm.LLM)
    supervisor_llm = MagicMock(spec=llm.LLM)
    routes_seen: list[tuple[tuple[str, ...], bool, str, int]] = []

    async def fake_mcp(_name: str, _args: dict[str, Any]) -> Any:
        return {}

    async def fake_emit(_ev: dict[str, Any]) -> None:
        return None

    async def on_route(decision: Any, model_name: str) -> None:
        routes_seen.append(
            (decision.domains, decision.needs_supervisor, model_name, len(decision.tool_names))
        )

    router = DualSpeedToolRouter(caller=fake_mcp, emit_event=fake_emit, hitl_gate=HITLGate())
    agent = SadaOpsAgent(
        router=router,
        get_context_state=lambda: ("", None),
        hitl_gate=HITLGate(),
        mcp_caller=fake_mcp,
        emit_event=fake_emit,
        fast_llm=fast_llm,
        supervisor_llm=supervisor_llm,
        on_route_decided=on_route,
    )
    agent.update_tools = AsyncMock()  # type: ignore[method-assign]
    agent.update_instructions = AsyncMock()  # type: ignore[method-assign]

    # Turn 1: fast single-domain query -> 20B + 3 ops_db tools + escalate_to_supervisor
    msg1 = llm.ChatMessage(role="user", content=["Show me service metrics for auth-gateway"])
    await agent.on_user_turn_completed(llm.ChatContext.empty(), msg1)
    assert agent._llm is fast_llm
    updated_tools_1 = agent.update_tools.await_args.args[0]
    names_1 = {t.id for t in updated_tools_1}
    assert names_1 == {
        "get_recent_incidents",
        "get_service_metrics",
        "run_readonly_sql",
        "discover_tools",
        "escalate_to_supervisor",
    }

    # Turn 2: cross-domain compound -> swaps to 120B Supervisor without escalate loop
    msg2 = llm.ChatMessage(
        role="user",
        content=["Check billing-worker metrics and send email to ops-leads@example.com"],
    )
    await agent.on_user_turn_completed(llm.ChatContext.empty(), msg2)
    assert agent._llm is supervisor_llm
    updated_tools_2 = agent.update_tools.await_args.args[0]
    names_2 = {t.id for t in updated_tools_2}
    assert "get_service_metrics" in names_2
    assert "send_email" in names_2
    assert "list_open_prs" not in names_2
    assert "escalate_to_supervisor" not in names_2
    assert routes_seen == [
        (("ops_db",), False, "openai/gpt-oss-20b", 3),
        (("ops_db", "workspace"), True, "openai/gpt-oss-120b", 12),
    ]

    # Turn 3: discover_tools unlocks missing workspace tools dynamically on demand
    msg3 = llm.ChatMessage(role="user", content=["Check recent sev1 incidents"])
    await agent.on_user_turn_completed(llm.ChatContext.empty(), msg3)
    disc_res = json.loads(
        await agent.discover_tools(MagicMock(), "reschedule or cancel calendar meeting")
    )
    unlocked_names = {item["name"] for item in disc_res["unlocked_tools"]}
    assert "update_calendar_event" in unlocked_names or "delete_calendar_event" in unlocked_names

    # Verify SupervisorWorkflowTask is a valid LiveKit AgentTask pinned to 120B & excludes recursion
    decision = classify_turn_route("Check metrics and send email")
    scoped_for_task = select_scoped_tools(
        agent._mcp_tools, decision, include_supervisor_tool=False
    )
    task = SupervisorWorkflowTask(
        goal="Check metrics and send email",
        tools=scoped_for_task,
        supervisor_llm=supervisor_llm,
        base_instructions="Base instructions",
    )
    assert isinstance(task, AgentTask)
    assert task.llm is supervisor_llm
    assert "finish_supervisor_workflow" in {t.id for t in task.tools}
    assert "escalate_to_supervisor" not in {t.id for t in task.tools}

    # Verify build_scoped_instructions injects live wall-clock context and cross-domain cues
    from agent.pipeline.orchestrator import build_scoped_instructions

    prompt = build_scoped_instructions("Base", classify_turn_route("Check sev1 incidents"))
    assert "[Live Session Context]" in prompt
    assert "Current local date/time:" in prompt
    assert "escalate_to_supervisor" in prompt


@pytest.mark.asyncio
async def test_sadaops_agent_retains_domains_across_split_stt_fragments() -> None:
    from agent.main import SadaOpsAgent

    fast_llm = MagicMock(spec=llm.LLM)
    supervisor_llm = MagicMock(spec=llm.LLM)

    async def fake_mcp(_name: str, _args: dict[str, Any]) -> Any:
        return {}

    async def fake_emit(_ev: dict[str, Any]) -> None:
        return None

    router = DualSpeedToolRouter(caller=fake_mcp, emit_event=fake_emit, hitl_gate=HITLGate())
    agent = SadaOpsAgent(
        router=router,
        get_context_state=lambda: ("", None),
        hitl_gate=HITLGate(),
        mcp_caller=fake_mcp,
        emit_event=fake_emit,
        fast_llm=fast_llm,
        supervisor_llm=supervisor_llm,
    )
    agent.update_tools = AsyncMock()  # type: ignore[method-assign]
    agent.update_instructions = AsyncMock()  # type: ignore[method-assign]

    ctx = llm.ChatContext.empty()
    ctx.add_message(role="assistant", content="Hello! Ready to help.")
    ctx.add_message(
        role="user",
        content=(
            "Check recent Sev1 ops_incidents on Auth-gateway. "
            "Search Runbooks for 502 token refresh. and send an email to ups-leads at"
        ),
    )
    tail_fragment = llm.ChatMessage(
        role="user", content=["Example.com, summarizing the incident."]
    )
    await agent.on_user_turn_completed(ctx, tail_fragment)

    assert agent._llm is supervisor_llm
    names = {t.id for t in agent.update_tools.await_args.args[0]}
    assert {"get_recent_incidents", "search_runbooks", "send_email"} <= names
    assert "list_open_prs" not in names


@pytest.mark.asyncio
async def test_voice_update_pending_action_and_revision_classifier() -> None:
    import json
    from datetime import UTC, datetime, timedelta

    from agent.main import SadaOpsAgent
    from agent.safety.hitl_gate import PendingAction, classify_voice_decision
    from agent.safety.prompt_guard import PolicyViolationError

    # Mixed revision utterance should NOT prematurely trigger HITL reject
    assert classify_voice_decision("No, change recipient to ops-leads@example.com") == "none"
    assert classify_voice_decision("عدّل الموضوع إلى مراجعة عاجلة") == "none"
    assert classify_voice_decision("No, cancel this") == "reject"
    assert classify_voice_decision("موافق") == "approve"

    fast_llm = MagicMock(spec=llm.LLM)
    events: list[dict[str, Any]] = []
    relocked: list[str] = []

    pending_row = PendingAction(
        action_id="act-voice-1",
        session_id="sess-v1",
        tool_name="send_email",
        payload={"to": "wrong@example.com", "subject": "Old", "body": "Draft"},
        payload_hash="oldhash123456",
        idempotency_key="idem-1",
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
        created_at=datetime.now(UTC),
    )

    class FakeGate(HITLGate):
        def list_pending(self, session_id: str | None = None) -> list[PendingAction]:
            return [pending_row]

        def update_pending_payload(
            self,
            action_id: str,
            new_payload: dict[str, Any],
            *,
            controls_enabled: bool = True,
        ) -> PendingAction:
            if new_payload.get("to") == "attacker@evil.invalid":
                raise PolicyViolationError("Disallowed recipient domain")
            pending_row.payload = dict(new_payload)
            pending_row.payload_hash = "newhashabcdef"
            return pending_row

    gate = FakeGate()

    async def fake_mcp(_name: str, _args: dict[str, Any]) -> Any:
        return {}

    async def fake_emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    async def on_pending_updated(act: PendingAction) -> None:
        relocked.append(act.action_id)

    router = DualSpeedToolRouter(caller=fake_mcp, emit_event=fake_emit, hitl_gate=gate)
    agent = SadaOpsAgent(
        router=router,
        get_context_state=lambda: ("", None),
        hitl_gate=gate,
        mcp_caller=fake_mcp,
        emit_event=fake_emit,
        fast_llm=fast_llm,
        session_id="sess-v1",
        on_pending_updated=on_pending_updated,
    )
    agent.update_tools = AsyncMock()  # type: ignore[method-assign]
    agent.update_instructions = AsyncMock()  # type: ignore[method-assign]

    # When pending action exists, update_pending_action is exposed cleanly in scoped tools
    agent.mark_pending_action("act-voice-1", is_pending=True)
    await agent.apply_turn_routing("Check recent incidents")
    scoped_names = {t.id for t in agent.update_tools.await_args.args[0]}
    assert "update_pending_action" in scoped_names

    res_json = await agent.update_pending_action(
        MagicMock(),
        updates_json=json.dumps({"to": "ops-leads@example.com", "subject": "Updated Subject"}),
    )
    res = json.loads(res_json)
    assert res["status"] == "pending_approval"
    assert res["payload_hash"] == "newhashabcdef"
    assert res["payload"] == {
        "to": "ops-leads@example.com",
        "subject": "Updated Subject",
        "body": "Draft",
    }
    assert relocked == ["act-voice-1"]
    assert any(
        e.get("type") == "hitl_pending" and e.get("payload_hash") == "newhashabcdef"
        for e in events
    )
    assert any(
        e.get("type") == "tool_event" and e.get("tool") == "update_pending_action"
        for e in events
    )

    # Disallowed domain update surfaces clean JSON error without throwing
    err_json = await agent.update_pending_action(
        MagicMock(), updates_json=json.dumps({"to": "attacker@evil.invalid"})
    )
    assert "Disallowed recipient domain" in json.loads(err_json)["error"]

    # Clearing pending state removes revision tool from normal turns
    agent.mark_pending_action("act-voice-1", is_pending=False)
    await agent.apply_turn_routing("Check recent incidents")
    scoped_after = {t.id for t in agent.update_tools.await_args.args[0]}
    assert "update_pending_action" not in scoped_after


@pytest.mark.asyncio
async def test_voice_hitl_decision_stops_parallel_llm_reply() -> None:
    from livekit.agents.llm import StopResponse

    from agent.main import SadaOpsAgent

    fast_llm = MagicMock(spec=llm.LLM)
    handled: list[str] = []

    async def fake_mcp(_name: str, _args: dict[str, Any]) -> Any:
        return {}

    async def fake_emit(_ev: dict[str, Any]) -> None:
        return None

    async def fake_voice_hitl(text: str) -> bool:
        handled.append(text)
        return text.lower() == "yes confirm"

    router = DualSpeedToolRouter(caller=fake_mcp, emit_event=fake_emit, hitl_gate=HITLGate())
    agent = SadaOpsAgent(
        router=router,
        get_context_state=lambda: ("", None),
        hitl_gate=HITLGate(),
        mcp_caller=fake_mcp,
        emit_event=fake_emit,
        fast_llm=fast_llm,
        handle_voice_hitl=fake_voice_hitl,
    )
    agent.update_tools = AsyncMock()  # type: ignore[method-assign]
    agent.update_instructions = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(
            llm.ChatContext.empty(),
            llm.ChatMessage(role="user", content=["Yes confirm"]),
        )
    assert handled == ["Yes confirm"]


def test_voice_hitl_ui_only_lock_policy_blocks_execution() -> None:
    from agent.main import resolve_voice_hitl_policy

    action, notice = resolve_voice_hitl_policy(
        "Yes, confirm",
        voice_hitl_enabled=False,
        has_pending=True,
        same_turn=False,
        tool_name="send_email",
    )
    assert action == "ui_only_blocked"
    assert notice is not None and "UI-Only Lock" in notice and "send_email" in notice

    same_turn_action, _ = resolve_voice_hitl_policy(
        "Yes, confirm",
        voice_hitl_enabled=False,
        has_pending=True,
        same_turn=True,
        tool_name="send_email",
    )
    assert same_turn_action == "ignore"

    enabled_action, enabled_notice = resolve_voice_hitl_policy(
        "Yes, confirm",
        voice_hitl_enabled=True,
        has_pending=True,
        same_turn=False,
        tool_name="send_email",
    )
    assert enabled_action == "approve"
    assert enabled_notice is None
