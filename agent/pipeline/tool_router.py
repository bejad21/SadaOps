"""Dual-speed MCP tool router with bilingual acoustic fillers and live UI tool cards."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from agent.pipeline.telemetry import TurnLatencyRecord
from agent.pipeline.turn_manager import TurnManager
from agent.safety.hitl_gate import HITLGate
from agent.safety.prompt_guard import (
    UNTRUSTED_READ_TOOLS,
    WRITE_TOOLS,
    resolve_connected_repo,
    wrap_untrusted_tool_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

MCP_SERVER_MODULES: dict[str, str] = {
    "ops_db": "mcp_servers.ops_db_mcp.server",
    "github": "mcp_servers.github_mcp.server",
    "workspace": "mcp_servers.workspace_mcp.server",
    "knowledge": "mcp_servers.knowledge_sandbox_mcp.server",
}

# Least-privilege per-server env forwarding so child MCP subprocesses receive domain settings
# without inheriting LLM, Speechmatics, Cartesia, or LiveKit secrets.
MCP_SERVER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "ops_db": ("OPS_READONLY_DATABASE_URL",),
    "github": ("GITHUB_MODE", "GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_ALLOWED_REPOS"),
    "workspace": (
        "DATABASE_URL",
        "MAILPIT_SMTP_HOST",
        "MAILPIT_SMTP_PORT",
        "MAILPIT_API_URL",
        "WORKSPACE_EMAIL_MODE",
        "WORKSPACE_CALENDAR_MODE",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
        "GOOGLE_REFRESH_TOKEN",
        "SADAOPS_GOOGLE_TOKEN_PATH",
    ),
    "knowledge": ("QDRANT_URL", "DOCKER_HOST", "HF_TOKEN", "HOME", "USERPROFILE"),
}


def build_mcp_server_env(server_name: str) -> dict[str, str]:
    """Return default sanitized environment plus explicit allowlisted domain variables."""
    env = get_default_environment()
    for key in MCP_SERVER_ENV_KEYS.get(server_name, ()):
        val = os.getenv(key)
        if val is not None and val != "":
            env[key] = val
    return env

TOOL_TO_SERVER: dict[str, str] = {
    "get_recent_incidents": "ops_db",
    "get_service_metrics": "ops_db",
    "run_readonly_sql": "ops_db",
    "list_repos": "github",
    "list_open_prs": "github",
    "list_commits": "github",
    "get_ci_logs": "github",
    "get_issue_details": "github",
    "create_issue": "github",
    "list_calendar_events": "workspace",
    "find_free_slots": "workspace",
    "create_calendar_event": "workspace",
    "update_calendar_event": "workspace",
    "delete_calendar_event": "workspace",
    "search_inbox": "workspace",
    "analyze_inbox": "workspace",
    "read_email": "workspace",
    "send_email": "workspace",
    "search_runbooks": "knowledge",
    "run_python_calc": "knowledge",
}

SLOW_TOOLS: frozenset[str] = frozenset(
    {
        "search_runbooks",
        "run_python_calc",
        "list_repos",
        "search_inbox",
        "analyze_inbox",
        "read_email",
        "get_ci_logs",
        "run_readonly_sql",
    }
)

FILLER_PHRASES: dict[str, tuple[str, str]] = {
    "ops_db": ("Checking the ops database now.", "خليني أشيك على قاعدة بيانات العمليات لحظة."),
    "github": ("Checking GitHub and CI logs.", "ثواني بشيك على GitHub وسجلات الـ CI."),
    "workspace": ("Checking your calendar and inbox.", "خليني أشيك على التقويم والإيميل لحظة."),
    "knowledge": ("Searching runbooks and running the check.", "ثواني بدور في الأدلة التشغيلية."),
}


def pick_filler_phrase(server: str, user_text: str) -> str:
    """Return an Arabic or English filler matching the operator's language."""
    en_msg, ar_msg = FILLER_PHRASES.get(server, ("One moment, checking that now.", "لحظة واحدة."))
    has_arabic = any("\u0600" <= ch <= "\u06ff" for ch in user_text)
    return ar_msg if has_arabic else en_msg


@dataclass
class MCPToolResult:
    """Result payload returned from an MCP tool invocation."""

    call_id: str
    tool_name: str
    server: str
    duration_ms: float
    output: Any
    error: str | None = None


class MCPServerPool:
    """Manages persistent stdio MCP client sessions for all 4 SadaOps MCP servers."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable
        self._sessions: dict[str, ClientSession] = {}
        self._stop_events: list[asyncio.Event] = []
        self._tasks: list[asyncio.Task[None]] = []

    async def _run_server_session(
        self,
        name: str,
        module: str,
        ready_fut: asyncio.Future[None],
        stop_event: asyncio.Event,
    ) -> None:
        params = StdioServerParameters(
            command=self.python_executable,
            args=["-m", module],
            cwd=str(REPO_ROOT),
            env=build_mcp_server_env(name),
        )
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._sessions[name] = session
                    if not ready_fut.done():
                        ready_fut.set_result(None)
                    await stop_event.wait()
        except Exception as exc:
            if not ready_fut.done():
                ready_fut.set_exception(exc)

    async def start(self) -> None:
        """Start persistent stdio client sessions for all configured MCP servers."""
        loop = asyncio.get_running_loop()
        ready_futures: list[asyncio.Future[None]] = []
        for name, module in MCP_SERVER_MODULES.items():
            ready_fut: asyncio.Future[None] = loop.create_future()
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._run_server_session(name, module, ready_fut, stop_event)
            )
            self._stop_events.append(stop_event)
            self._tasks.append(task)
            ready_futures.append(ready_fut)

        try:
            await asyncio.gather(*ready_futures)
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        """Gracefully terminate all active MCP server subprocess sessions."""
        for ev in self._stop_events:
            ev.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._stop_events.clear()
        self._tasks.clear()
        self._sessions.clear()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on its owning MCP server and parse the returned payload."""
        server = TOOL_TO_SERVER.get(tool_name)
        if not server or server not in self._sessions:
            raise KeyError(f"Unknown or uninitialized MCP tool: {tool_name}")
        result = await self._sessions[server].call_tool(tool_name, arguments=arguments)
        texts = [
            getattr(block, "text", str(block))
            for block in (result.content or [])
        ]
        joined = "\n".join(texts).strip()
        if getattr(result, "is_error", False):
            raise RuntimeError(joined or f"MCP tool {tool_name} failed")
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            if isinstance(structured, dict) and list(structured.keys()) == ["result"]:
                return structured["result"]
            return structured
        try:
            return json.loads(joined)
        except Exception:
            return joined

    async def list_tool_annotations(self) -> dict[str, dict[str, Any]]:
        """Query MCP ToolAnnotations across all active sessions for inspection/auditing."""
        metadata: dict[str, dict[str, Any]] = {}
        for server_name, session in self._sessions.items():
            listed = await session.list_tools()
            for tool in listed.tools:
                ann = tool.annotations
                metadata[tool.name] = {
                    "server": server_name,
                    "title": ann.title if ann else None,
                    "readOnlyHint": (
                        getattr(ann, "read_only_hint", getattr(ann, "readOnlyHint", None))
                        if ann
                        else None
                    ),
                    "destructiveHint": (
                        getattr(ann, "destructive_hint", getattr(ann, "destructiveHint", None))
                        if ann
                        else None
                    ),
                    "idempotentHint": (
                        getattr(ann, "idempotent_hint", getattr(ann, "idempotentHint", None))
                        if ann
                        else None
                    ),
                    "openWorldHint": (
                        getattr(ann, "open_world_hint", getattr(ann, "openWorldHint", None))
                        if ann
                        else None
                    ),
                }
        return metadata


class DualSpeedToolRouter:
    """Routes MCP calls with fillers, HITL write locking, and DataChannel events."""

    def __init__(
        self,
        caller: Callable[[str, dict[str, Any]], Awaitable[Any]],
        emit_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        speak_filler: Callable[[str], Awaitable[None]] | None = None,
        filler_delay_s: float = 0.35,
        hitl_gate: HITLGate | None = None,
        session_id: str = "default",
        controls_enabled: bool = True,
        turn_manager: TurnManager | None = None,
    ) -> None:
        self._caller = caller
        self._emit_event = emit_event
        self._speak_filler = speak_filler
        self.filler_delay_s = filler_delay_s
        self._hitl_gate = hitl_gate
        self.session_id = session_id
        self._controls_enabled = controls_enabled
        self._turn_manager = turn_manager
        self._writes_in_turn = 0

    def reset_turn_writes(self) -> None:
        """Reset per-turn write counter at the start of each new operator turn."""
        self._writes_in_turn = 0

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        last_user_text: str = "",
        latency_record: TurnLatencyRecord | None = None,
    ) -> MCPToolResult:
        """Execute an MCP tool call with HITL write gating, latency tracking, and fillers."""
        call_id = f"mcp-{uuid.uuid4().hex[:8]}"
        server = TOOL_TO_SERVER.get(tool_name, "unknown")
        if server == "github":
            arguments = dict(arguments)
            arguments["repo"] = resolve_connected_repo(arguments.get("repo"))
        start_ms = time.monotonic() * 1000.0
        if latency_record is not None:
            latency_record.mark_mcp_start(start_ms)

        if self._emit_event is not None:
            await self._emit_event(
                {
                    "type": "tool_event",
                    "call_id": call_id,
                    "tool": tool_name,
                    "server": server,
                    "args": arguments,
                    "status": "running",
                    "duration_ms": 0,
                    "preview": "",
                }
            )

        # Intercept write tools when safety controls are active so mutations require HITL approval.
        if tool_name in WRITE_TOOLS and self._controls_enabled and self._hitl_gate is not None:
            try:
                pending = await asyncio.to_thread(
                    self._hitl_gate.propose_action,
                    self.session_id,
                    tool_name,
                    arguments,
                    writes_in_turn=self._writes_in_turn,
                    controls_enabled=self._controls_enabled,
                )
                self._writes_in_turn += 1
                duration_ms = round((time.monotonic() * 1000.0) - start_ms, 2)
                if latency_record is not None:
                    latency_record.mark_mcp_end()
                locked_out = {
                    "status": "pending_approval",
                    "action_id": pending.action_id,
                    "payload_hash": pending.payload_hash,
                    "tool_name": pending.tool_name,
                    "payload": pending.payload,
                    "message": "Action locked and awaiting operator approval.",
                }
                if self._emit_event is not None:
                    await self._emit_event(
                        {
                            "type": "hitl_pending",
                            **pending.model_dump(mode="json"),
                        }
                    )
                    await self._emit_event(
                        {
                            "type": "tool_event",
                            "call_id": call_id,
                            "tool": tool_name,
                            "server": server,
                            "args": arguments,
                            "status": "pending_approval",
                            "duration_ms": duration_ms,
                            "preview": f"Locked as {pending.action_id} awaiting approval",
                        }
                    )
                return MCPToolResult(
                    call_id=call_id,
                    tool_name=tool_name,
                    server=server,
                    duration_ms=duration_ms,
                    output=locked_out,
                )
            except Exception as exc:
                duration_ms = round((time.monotonic() * 1000.0) - start_ms, 2)
                if latency_record is not None:
                    latency_record.mark_mcp_end()
                if self._emit_event is not None:
                    await self._emit_event(
                        {
                            "type": "tool_event",
                            "call_id": call_id,
                            "tool": tool_name,
                            "server": server,
                            "args": arguments,
                            "status": "error",
                            "duration_ms": duration_ms,
                            "preview": str(exc)[:220],
                        }
                    )
                return MCPToolResult(
                    call_id=call_id,
                    tool_name=tool_name,
                    server=server,
                    duration_ms=duration_ms,
                    output=None,
                    error=str(exc),
                )

        filler_task: asyncio.Task[None] | None = None
        if self._speak_filler is not None and tool_name in SLOW_TOOLS:
            phrase = pick_filler_phrase(server, last_user_text)

            async def _delayed_filler() -> None:
                await asyncio.sleep(self.filler_delay_s)
                await self._speak_filler(phrase)  # type: ignore[misc]

            filler_task = asyncio.create_task(_delayed_filler())

        call_task = asyncio.create_task(self._caller(tool_name, arguments))
        interrupted_by_turn = False
        if self._turn_manager is not None:

            def _cancel_on_barge_in() -> None:
                nonlocal interrupted_by_turn
                interrupted_by_turn = True
                if not call_task.done():
                    call_task.cancel()

            self._turn_manager.register_cancel_hook(_cancel_on_barge_in)

        try:
            output = await call_task
            if filler_task and not filler_task.done():
                filler_task.cancel()
            if tool_name in UNTRUSTED_READ_TOOLS and self._controls_enabled:
                output = wrap_untrusted_tool_output(tool_name, output)

            duration_ms = round((time.monotonic() * 1000.0) - start_ms, 2)
            if latency_record is not None:
                latency_record.mark_mcp_end()

            preview = json.dumps(output, ensure_ascii=False)[:220]
            if self._emit_event is not None:
                await self._emit_event(
                    {
                        "type": "tool_event",
                        "call_id": call_id,
                        "tool": tool_name,
                        "server": server,
                        "args": arguments,
                        "status": "completed",
                        "duration_ms": duration_ms,
                        "preview": preview,
                    }
                )
            return MCPToolResult(
                call_id=call_id,
                tool_name=tool_name,
                server=server,
                duration_ms=duration_ms,
                output=output,
            )
        except asyncio.CancelledError:
            if filler_task and not filler_task.done():
                filler_task.cancel()
            if not call_task.done():
                call_task.cancel()
            await asyncio.gather(call_task, return_exceptions=True)
            if not interrupted_by_turn:
                raise
            duration_ms = round((time.monotonic() * 1000.0) - start_ms, 2)
            if latency_record is not None:
                latency_record.interrupted = True
                latency_record.mark_mcp_end()
            if self._emit_event is not None:
                await self._emit_event(
                    {
                        "type": "tool_event",
                        "call_id": call_id,
                        "tool": tool_name,
                        "server": server,
                        "args": arguments,
                        "status": "cancelled",
                        "duration_ms": duration_ms,
                        "preview": "Cancelled by operator interruption",
                    }
                )
            return MCPToolResult(
                call_id=call_id,
                tool_name=tool_name,
                server=server,
                duration_ms=duration_ms,
                output=None,
                error="cancelled by user interruption",
            )
        except Exception as exc:
            if filler_task and not filler_task.done():
                filler_task.cancel()
            duration_ms = round((time.monotonic() * 1000.0) - start_ms, 2)
            if latency_record is not None:
                latency_record.mark_mcp_end()
            if self._emit_event is not None:
                await self._emit_event(
                    {
                        "type": "tool_event",
                        "call_id": call_id,
                        "tool": tool_name,
                        "server": server,
                        "args": arguments,
                        "status": "error",
                        "duration_ms": duration_ms,
                        "preview": str(exc)[:220],
                    }
                )
            return MCPToolResult(
                call_id=call_id,
                tool_name=tool_name,
                server=server,
                duration_ms=duration_ms,
                output=None,
                error=str(exc),
            )
