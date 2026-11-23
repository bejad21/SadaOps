"""Scoped multi-domain tool orchestration and 120B Supervisor routing."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from livekit.agents import AgentTask, RunContext, function_tool, llm

logger = logging.getLogger("sadaops.orchestrator")

ALL_DOMAINS: tuple[str, ...] = ("ops_db", "github", "workspace", "knowledge")

DOMAIN_TOOLS: dict[str, tuple[str, ...]] = {
    "ops_db": ("get_recent_incidents", "get_service_metrics", "run_readonly_sql"),
    "github": (
        "list_repos",
        "list_open_prs",
        "list_commits",
        "get_ci_logs",
        "get_issue_details",
        "create_issue",
    ),
    "workspace": (
        "list_calendar_events",
        "find_free_slots",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "search_inbox",
        "analyze_inbox",
        "read_email",
        "send_email",
    ),
    "knowledge": ("search_runbooks", "run_python_calc"),
}

_DOMAIN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "ops_db": (
        re.compile(
            r"\b(?:incident|incidents|inc-\d+|sev[1-4]|metric|metrics|latency|p95|p50|"
            r"error\s*rate|rpm|sql|select|ops_incidents|service_metrics|deployments|ops\s*db|"
            r"auth[\s_-]?gateway|billing[\s_-]?worker|vector[\s_-]?sync)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:حادث|حوادث|المشاكل|مقاييس|استعلام|قاعدة\s+البيانات|سيرفس|سرفيس|"
            r"ماتريكس|متريكس|سيكول|اس\s*كيو\s*ال|ديبلوي|ديبلويمينت|إنك|انك|كتوي|بلينفوركر)"
        ),
    ),
    "github": (
        re.compile(
            r"\b(?:prs?|pull\s*requests?|ci(?:/cd)?|logs?|workflow|github|repos?|repositories|"
            r"commits?|branches?|public|private|issues?|bejad21|bajad|sadaops-demo|run\s*\d+)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:لوجات|الرن|بي\s*آر|بيورس|ببيوراس|الأوبين|اوبين|سي\s*آي|سيئي|"
            r"مستودع|مستودعات|ريبو|ريبوهات|كوميت|كوميتس|"
            r"السييلرن|سيلرن|ايشو|إيشو|اقرأش|بجاد|سوداو|ستامو|كتاب|"
            r"مراجعة\s+كود|مشكلة\s*في\s*github)"
        ),
    ),
    "workspace": (
        re.compile(
            r"\b(?:emails?|e-mails?|mails?|gmail|inbox|unread|sender|senders|messaged|"
            r"messages?|contacted|send\s+(?:an\s+)?(?:email|mail|it)|calendar|events?|"
            r"meetings?|slots?|schedule|reschedule|postpone|appointment|invite|attendees?|"
            r"free\s+(?:today|tomorrow|this|next|at)|am\s+i\s+(?:free|busy)|who\s+sent|"
            r"(?:next|last)\s+(?:week|month)|cancel\s+(?:my\s+|the\s+)?(?:meeting|event))\b|"
            r"@\S+\.\S+",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:إيميل|ايميل|الإيميل|الايميل|جيميل|بريد|رسائل|رسالة|غير\s*مقروء|مين\s+بعت|"
            r"تقويم|التقويم|موعد|مواعيد|اجتماع|فاضي|مشغول|جدول|أجل|اجل|انقل|الغي|إلغاء|"
            r"احجز|أحجز|ابعت|ابعته|ابعث|ارسل|سجل\s+اجتماع|سامري|أكزامبل|اكزامبل|دوت\s*كوم)"
        ),
    ),
    "knowledge": (
        re.compile(
            r"\b(?:runbooks?|docs?|documentation|steps?|guides?|playbook|python|calc|"
            r"calculate|compute|multiply|times|redis|qdrant|token\s+refresh|502)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:دليل|أدلة|الأدلة|رانبوكس|الرانبوكس|بايثون|بالبايثون|احسب|احسبلي|"
            r"حل\s+مشكلة|كيرانت|كيودرانت|كيوترانت)"
        ),
    ),
}

_MULTI_STEP_PATTERN = re.compile(
    r"(?:\band\s+then\b|\bbased\s+on\b|\bsummariz(?:e|ing)\b|\bafter\s+that\b|"
    r"\bfirst\b.*\bthen\b|\band\s+(?:send|email|create|open|schedule|inspect|search|check|look)\b|"
    r"\bwho\s+sent\s+(?:the\s+)?most\b|\bmost\s+(?:emails?|messages?)\b|\btop\s+senders?\b|"
    r"\banalyze\b|\bcompare\b|"
    r"وبعدين|بعدين|بملخص|وشوف|ودور|وبعد\s+كده|ثم|مين\s+بعت\s+أكثر|مين\s+بعت\s+اكتر|أكثر\s+مرسل)",
    re.IGNORECASE,
)

_FOLLOWUP_PATTERN = re.compile(
    r"(?:\b(?:that|it|them|those|this|again|more|details?|same|yes|no)\b|"
    r"تاني|ده|دي|نفس|تمام|كمل|أكثر)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    """Routing decision specifying scoped domains, tools, and whether 120B Supervisor runs."""

    domains: tuple[str, ...]
    needs_supervisor: bool
    reason: str
    tool_names: tuple[str, ...]


def classify_turn_route(
    user_text: str,
    *,
    active_domains: tuple[str, ...] = (),
) -> RouteDecision:
    """Classify bilingual EN/AR user input into scoped domains and 20B vs 120B tier."""
    cleaned = (user_text or "").strip()
    if not cleaned:
        domains = active_domains or ALL_DOMAINS
        tools = tuple(t for d in domains for t in DOMAIN_TOOLS[d])
        return RouteDecision(
            domains=domains,
            needs_supervisor=False,
            reason="empty_fallback",
            tool_names=tools,
        )

    matched: list[str] = []
    for domain in ALL_DOMAINS:
        if any(pat.search(cleaned) for pat in _DOMAIN_PATTERNS[domain]):
            matched.append(domain)

    multi_step_cue = bool(_MULTI_STEP_PATTERN.search(cleaned))

    if not matched:
        is_short_followup = (
            bool(active_domains)
            and len(cleaned.split()) <= 8
            and bool(_FOLLOWUP_PATTERN.search(cleaned))
        )
        domains = active_domains if is_short_followup else ALL_DOMAINS
        tools = tuple(t for d in domains for t in DOMAIN_TOOLS[d])
        return RouteDecision(
            domains=domains,
            needs_supervisor=multi_step_cue and len(domains) > 1,
            reason="context_continuation" if is_short_followup else "all_domains_fallback",
            tool_names=tools,
        )

    domains_tuple = tuple(matched)
    needs_supervisor = len(domains_tuple) >= 2 or multi_step_cue
    scoped_tools = tuple(t for d in domains_tuple for t in DOMAIN_TOOLS[d])

    if len(domains_tuple) >= 2:
        reason = f"multi_domain:{'+'.join(domains_tuple)}"
    elif multi_step_cue:
        reason = f"multi_step:{domains_tuple[0]}"
    else:
        reason = f"single_domain:{domains_tuple[0]}"

    return RouteDecision(
        domains=domains_tuple,
        needs_supervisor=needs_supervisor,
        reason=reason,
        tool_names=scoped_tools,
    )


def tool_identifier(tool_obj: Any) -> str:
    """Extract canonical tool name from LiveKit FunctionTool or OpenAI tool schema dict."""
    if isinstance(tool_obj, dict):
        fn = tool_obj.get("function") or {}
        return str(fn.get("name") or tool_obj.get("name") or "")
    info = getattr(tool_obj, "info", None)
    if info is not None and getattr(info, "name", None):
        return str(info.name)
    return str(getattr(tool_obj, "id", ""))


def tool_description(tool_obj: Any) -> str:
    """Extract canonical description string from LiveKit FunctionTool or schema dict."""
    if isinstance(tool_obj, dict):
        fn = tool_obj.get("function") or {}
        return str(fn.get("description") or tool_obj.get("description") or "")
    info = getattr(tool_obj, "info", None)
    if info is not None and getattr(info, "description", None):
        return str(info.description)
    return str(getattr(tool_obj, "description", "") or "")


def _tokenize_semantic(text: str) -> set[str]:
    cleaned = re.sub(r"[_\-]+", " ", (text or "").lower())
    words = set(re.findall(r"[a-z0-9\u0600-\u06ff]{2,}", cleaned))
    # Light plural stem normalization (emails -> email, events -> event)
    stems = {w[:-1] for w in words if len(w) > 4 and w.endswith("s")}
    return words | stems


def rank_matching_tools(
    all_tools: Sequence[Any],
    query: str,
    *,
    top_k: int = 5,
) -> list[Any]:
    """Rank registered tools by semantic overlap between capability query and tool metadata."""
    q_tokens = _tokenize_semantic(query)
    if not q_tokens:
        return list(all_tools)[:top_k]

    scored: list[tuple[float, int, Any]] = []
    for idx, tool in enumerate(all_tools):
        name = tool_identifier(tool)
        desc = tool_description(tool)
        name_tokens = _tokenize_semantic(name)
        desc_tokens = _tokenize_semantic(desc)
        # Weight exact identifier token matches higher than body description matches
        score = (len(q_tokens & name_tokens) * 3.0) + (len(q_tokens & desc_tokens) * 1.2)
        if score > 0:
            scored.append((score, -idx, tool))

    scored.sort(reverse=True)
    if not scored:
        return list(all_tools)[:top_k]
    return [item[2] for item in scored[:top_k]]


def select_scoped_tools(
    all_tools: Sequence[Any],
    decision: RouteDecision,
    *,
    supervisor_tool_name: str = "escalate_to_supervisor",
    discover_tool_name: str = "discover_tools",
    include_supervisor_tool: bool = True,
) -> list[Any]:
    """Filter tools to active domains (+ optional discovery and escalation meta-tools)."""
    allowed = set(decision.tool_names)
    if include_supervisor_tool:
        allowed.add(discover_tool_name)
        if not decision.needs_supervisor:
            allowed.add(supervisor_tool_name)
    selected = [tool for tool in all_tools if tool_identifier(tool) in allowed]
    return selected or list(all_tools)


def build_scoped_instructions(base_instructions: str, decision: RouteDecision) -> str:
    """Append current wall-clock time/timezone and concise domain scope guidance."""
    now_local = datetime.now().astimezone()
    time_line = (
        f"Current local date/time: {now_local.strftime('%Y-%m-%d %H:%M %Z (%A)')} "
        f"(UTC{now_local.strftime('%z')})."
    )
    scope_line = f"Active domains for this turn: {', '.join(decision.domains)}."
    if decision.needs_supervisor:
        mode_line = (
            "Supervisor workflow active: execute all required read lookups across active "
            "domains, compute counts/rankings/insights from the real tool results, and whenever "
            "the operator requests or gives details for a write action (email, calendar event, "
            "or issue), invoke that write tool so it locks as pending_approval."
        )
    else:
        mode_line = (
            "Fast scoped mode: you only see tools for the active domains above. Answer "
            "flexibly with them (or directly for general chat). If the task needs data or "
            "actions from ANY other domain not shown (ops_db, github, workspace, knowledge), "
            "NEVER say you cannot—call `escalate_to_supervisor(goal, domains=...)` immediately."
        )
    return (
        f"{base_instructions}\n\n"
        f"[Live Session Context]\n- {time_line}\n"
        f"[Routing Scope]\n- {scope_line}\n- {mode_line}"
    )


class SupervisorWorkflowTask(AgentTask[str]):
    """Scoped LiveKit AgentTask pinned to the 120B supervisor LLM for multi-step workflows."""

    def __init__(
        self,
        *,
        goal: str,
        tools: list[Any],
        supervisor_llm: llm.LLM,
        base_instructions: str,
        chat_ctx: llm.ChatContext | None = None,
    ) -> None:
        self._goal = goal
        task_instructions = (
            f"{base_instructions}\n\n"
            "[120B Supervisor Task]\n"
            f"- Execute the multi-step goal cleanly using all relevant domain tools: {goal}\n"
            "- Complete required read lookups first, lock at most one write proposal if requested, "
            "then call `finish_supervisor_workflow` with a crisp bilingual operator summary "
            "(do not read full email/issue bodies verbatim unless explicitly requested)."
        )
        super().__init__(
            instructions=task_instructions,
            tools=list(tools),
            llm=supervisor_llm,
            chat_ctx=chat_ctx if chat_ctx is not None else llm.ChatContext.empty(),
            preserve_function_call_history=True,
        )

    async def on_enter(self) -> None:
        """Trigger the supervisor LLM to execute the delegated multi-domain goal."""
        await self.session.generate_reply(
            user_input=self._goal,
            instructions=(
                "Resolve the delegated workflow with tools now and call "
                "`finish_supervisor_workflow` once done."
            ),
        )

    @function_tool
    async def finish_supervisor_workflow(self, ctx: RunContext, summary: str) -> str:
        """Complete the delegated supervisor task and hand control back to the voice commander."""
        cleaned = (summary or "Supervisor workflow completed.").strip()
        if not self.done():
            self.complete(cleaned)
        return cleaned
