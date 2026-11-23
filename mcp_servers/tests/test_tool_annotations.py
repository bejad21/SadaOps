"""Integration test verifying MCP ToolAnnotations across all 4 FastMCP stdio servers."""

from __future__ import annotations

import pytest

from agent.pipeline.tool_router import MCPServerPool
from agent.safety.prompt_guard import WRITE_TOOLS

EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        "get_recent_incidents",
        "get_service_metrics",
        "run_readonly_sql",
        "list_repos",
        "list_open_prs",
        "list_commits",
        "get_ci_logs",
        "get_issue_details",
        "create_issue",
        "list_calendar_events",
        "find_free_slots",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "search_inbox",
        "analyze_inbox",
        "read_email",
        "send_email",
        "search_runbooks",
        "run_python_calc",
    }
)


@pytest.mark.asyncio
async def test_all_mcp_tools_expose_spec_annotations() -> None:
    """Every tool across the 4 FastMCP servers must emit MCP ToolAnnotations hints."""
    pool = MCPServerPool()
    await pool.start()
    try:
        annotations = await pool.list_tool_annotations()
        assert set(annotations.keys()) == EXPECTED_TOOLS
        for tool_name, meta in annotations.items():
            assert meta["title"], f"{tool_name} missing ToolAnnotations.title"
            assert isinstance(meta["readOnlyHint"], bool)
            assert isinstance(meta["destructiveHint"], bool)
            assert isinstance(meta["idempotentHint"], bool)
            assert isinstance(meta["openWorldHint"], bool)
            if tool_name in WRITE_TOOLS:
                assert meta["readOnlyHint"] is False
                assert meta["idempotentHint"] is False
            else:
                assert meta["readOnlyHint"] is True
                assert meta["destructiveHint"] is False
                assert meta["idempotentHint"] is True
        assert annotations["send_email"]["destructiveHint"] is True
        assert annotations["create_issue"]["openWorldHint"] is True
    finally:
        await pool.close()
