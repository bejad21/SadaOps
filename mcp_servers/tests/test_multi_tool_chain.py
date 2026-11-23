"""Integration test for multi-tool chaining via MCP stdio pool and DualSpeedToolRouter."""

from __future__ import annotations

import asyncio

import pytest

from agent.pipeline.telemetry import TurnLatencyRecord
from agent.pipeline.tool_router import DualSpeedToolRouter, MCPServerPool


@pytest.mark.asyncio
async def test_chained_mcp_workflow_and_dual_speed_filler() -> None:
    """Verify real stdio MCP servers execute a chained Ops -> Runbook -> Inbox flow."""
    pool = MCPServerPool()
    await pool.start()
    events: list[dict] = []
    fillers: list[str] = []

    async def emit(ev: dict) -> None:
        events.append(ev)

    async def speak(phrase: str) -> None:
        fillers.append(phrase)

    async def delayed_caller(name: str, args: dict):
        if name == "search_runbooks":
            await asyncio.sleep(0.15)
        return await pool.call_tool(name, args)

    try:
        router = DualSpeedToolRouter(
            caller=delayed_caller,
            emit_event=emit,
            speak_filler=speak,
            filler_delay_s=0.05,
        )
        rec = TurnLatencyRecord(turn_id="chain-1", session_id="test")

        inc_res = await router.execute(
            "get_recent_incidents",
            {"severity": "sev1"},
            last_user_text="Check sev1 incidents",
            latency_record=rec,
        )
        assert inc_res.error is None
        assert inc_res.output[0]["service_name"] == "auth-gateway"

        # Arabic query should trigger an Arabic filler phrase when the slow tool runs.
        rb_res = await router.execute(
            "search_runbooks",
            {"query": "auth-gateway Redis 502"},
            last_user_text="شوفلي الحل في الدليل",
            latency_record=rec,
        )
        assert rb_res.error is None
        assert rb_res.output["untrusted_external_data"] is True
        assert rb_res.output["data"][0]["id"] == "rb-001"
        assert any("الأدلة" in f for f in fillers)

        ci_res = await router.execute(
            "get_ci_logs",
            {"run_id": 8891},
            last_user_text="Check CI logs",
            latency_record=rec,
        )
        assert ci_res.error is None
        assert ci_res.output["untrusted_external_data"] is True
        assert ci_res.output["data"]["status"] == "failed"

        completed = [e for e in events if e["status"] == "completed"]
        assert len(completed) == 3
    finally:
        await pool.close()


def test_ops_db_service_normalization_matches_asr_variants() -> None:
    """Case/hyphen variations from voice ASR (e.g. AuthGateway, Sev1) must still match."""
    from mcp_servers.ops_db_mcp.service import OpsDBService

    svc = OpsDBService()
    for variant in ("auth-gateway", "AuthGateway", "auth gateway"):
        rows = svc.get_recent_incidents(service=variant, severity="Sev1", limit=5)
        assert rows and rows[0]["incident_code"] == "INC-1041"
        metrics = svc.get_service_metrics(service=variant)
        assert metrics and metrics[0]["service_name"] == "auth-gateway"
