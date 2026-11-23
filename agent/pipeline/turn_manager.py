"""Safe barge-in turn manager and LLM conversation history reconciler."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class TurnState(StrEnum):
    """Lifecycle states for a single voice conversation turn."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


@dataclass
class TurnManager:
    """Tracks conversational state, cancels in-flight output on barge-in, and repairs history."""

    state: TurnState = TurnState.IDLE
    active_turn_id: str | None = None
    interruption_count: int = 0
    _cancel_callbacks: list[Callable[[], Awaitable[None] | None]] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def register_cancel_hook(self, cb: Callable[[], Awaitable[None] | None]) -> None:
        """Register a hook (e.g., TTS stop or active MCP task cancel) for the current turn."""
        self._cancel_callbacks.append(cb)

    def clear_cancel_hooks(self) -> None:
        """Remove all registered cancellation hooks for the active turn."""
        self._cancel_callbacks.clear()

    async def start_listening(self, turn_id: str) -> None:
        """Transition the turn manager into LISTENING state for a new turn."""
        async with self._lock:
            self.active_turn_id = turn_id
            self.state = TurnState.LISTENING

    async def start_thinking(self) -> None:
        """Transition into THINKING state unless the turn was already interrupted."""
        async with self._lock:
            if self.state != TurnState.INTERRUPTED:
                self.state = TurnState.THINKING

    async def start_speaking(self) -> None:
        """Transition into SPEAKING state unless the turn was already interrupted."""
        async with self._lock:
            if self.state != TurnState.INTERRUPTED:
                self.state = TurnState.SPEAKING

    async def finish_turn(self) -> None:
        """Reset state to IDLE and clear cancellation hooks after turn completion."""
        async with self._lock:
            self.clear_cancel_hooks()
            self.state = TurnState.IDLE

    async def handle_barge_in(
        self,
        history: list[dict[str, Any]] | None = None,
        partial_assistant_text: str = "",
    ) -> bool:
        """Interrupt active speech/generation cleanly.

        Returns True if an active speaking/thinking turn was interrupted,
        or False if the agent was already idle/interrupted (idempotent against double triggers).
        """
        async with self._lock:
            if self.state not in (TurnState.THINKING, TurnState.SPEAKING):
                return False

            self.state = TurnState.INTERRUPTED
            self.interruption_count += 1
            callbacks = list(self._cancel_callbacks)
            self._cancel_callbacks.clear()

        for cb in callbacks:
            try:
                res = cb()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.warning("Barge-in cancel callback failed: %s", exc)

        if history is not None:
            reconcile_interrupted_history(history, partial_assistant_text=partial_assistant_text)

        return True

    async def resume_after_false_interruption(self) -> bool:
        """Restore SPEAKING state when a noise/cough spike is a false barge-in."""
        async with self._lock:
            if self.state != TurnState.INTERRUPTED:
                return False
            self.state = TurnState.SPEAKING
            return True


_HALLUCINATION_PHRASES: frozenset[str] = frozenset(
    {
        "اشتركوا في القناة",
        "اشترك في القناة",
        "موسيقى",
        "subscribe to the channel",
        "thank you for watching",
    }
)
_HALLUCINATION_SUBSTRINGS: tuple[str, ...] = (
    "اشتركوا في القناة",
    "اشترك في القناة",
    "أفحكم الجاهلية",
    "subscribe to the channel",
    "thank you for watching",
    "موسيقى",
)
_MIN_FINAL_CONFIDENCE = 0.35


def is_valid_utterance(
    text: str | None,
    *,
    confidence: float | None = None,
) -> bool:
    """Reject empty, low-confidence, or known STT silence-hallucination boilerplate."""
    if not text:
        return False
    if confidence is not None and 0.0 < confidence < _MIN_FINAL_CONFIDENCE:
        return False
    stripped = text.strip().rstrip(".!؟?,،").strip().lower()
    if stripped in _HALLUCINATION_PHRASES:
        return False
    if any(sub in stripped for sub in _HALLUCINATION_SUBSTRINGS):
        return False
    cleaned = "".join(ch for ch in text if ch.isalnum())
    return len(cleaned) > 0


def reconcile_interrupted_history(
    history: list[dict[str, Any]],
    partial_assistant_text: str = "",
) -> list[dict[str, Any]]:
    """Guarantee OpenAI/Groq message history remains valid after a barge-in.

    1. Every assistant `tool_call` must have a matching `role == "tool"` message with
       matching `tool_call_id`; any dangling ones get a synthetic cancellation tool result.
    2. Appends/updates an explicit `[interrupted by user]` marker if partial speech occurred.
    """
    answered_ids = {
        msg.get("tool_call_id")
        for msg in history
        if msg.get("role") == "tool" and msg.get("tool_call_id")
    }

    repaired: list[dict[str, Any]] = []
    for msg in history:
        repaired.append(msg)
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id")
                if tc_id and tc_id not in answered_ids:
                    repaired.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": "[cancelled by user interruption]",
                        }
                    )
                    answered_ids.add(tc_id)

    trimmed = partial_assistant_text.strip()
    if trimmed:
        repaired.append(
            {
                "role": "assistant",
                "content": f"{trimmed} [interrupted by user]",
            }
        )

    history[:] = repaired
    return history
