"""Unit tests for TurnManager barge-in and history reconciliation."""

from __future__ import annotations

import pytest

from agent.pipeline.turn_manager import (
    TurnManager,
    TurnState,
    is_valid_utterance,
    reconcile_interrupted_history,
)


@pytest.mark.asyncio
async def test_barge_in_cancels_hooks_and_is_idempotent() -> None:
    """Barge-in should stop speaking once, run cancel hooks, and ignore duplicate triggers."""
    tm = TurnManager()
    cancelled = 0

    async def stop_tts() -> None:
        nonlocal cancelled
        cancelled += 1

    tm.register_cancel_hook(stop_tts)
    await tm.start_listening("turn-1")
    await tm.start_thinking()
    await tm.start_speaking()

    first = await tm.handle_barge_in()
    second = await tm.handle_barge_in()

    assert first is True
    assert second is False
    assert cancelled == 1
    assert tm.state == TurnState.INTERRUPTED
    assert tm.interruption_count == 1


@pytest.mark.asyncio
async def test_barge_in_when_idle_does_nothing() -> None:
    tm = TurnManager()
    assert await tm.handle_barge_in() is False
    assert tm.interruption_count == 0


def test_reconcile_interrupted_history_repairs_orphaned_tool_calls() -> None:
    """Interrupted mid-tool turns must still have matching tool responses for every tool_call_id."""
    history = [
        {"role": "user", "content": "Check auth-gateway incidents and send summary"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "get_incidents"}},
                {"id": "call_2", "type": "function", "function": {"name": "get_metrics"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"count": 1}'},
    ]

    reconcile_interrupted_history(history, partial_assistant_text="Sure, I found one incident")

    tool_ids = {m["tool_call_id"] for m in history if m.get("role") == "tool"}
    assert tool_ids == {"call_1", "call_2"}
    cancelled_msg = [m for m in history if m.get("tool_call_id") == "call_2"][0]
    assert "cancelled" in cancelled_msg["content"]
    assert history[-1] == {
        "role": "assistant",
        "content": "Sure, I found one incident [interrupted by user]",
    }


def test_is_valid_utterance_filters_empty_and_punctuation() -> None:
    assert is_valid_utterance(None) is False
    assert is_valid_utterance("   ") is False
    assert is_valid_utterance("... ؟ !") is False
    assert is_valid_utterance("اشتركوا في القناة") is False
    assert is_valid_utterance("موسيقى Jesus") is False
    assert is_valid_utterance("Thank you", confidence=0.08) is False
    assert is_valid_utterance("شوفلي الـ incidents", confidence=0.82) is True
    assert is_valid_utterance("شوفلي الـ incidents") is True
    assert is_valid_utterance("check incidents") is True


@pytest.mark.asyncio
async def test_barge_in_isolates_failing_cancel_hooks() -> None:
    """A failing cancel callback must not block subsequent barge-in cleanup hooks."""
    tm = TurnManager()
    second_ran = False

    async def failing_hook() -> None:
        raise RuntimeError("boom")

    async def healthy_hook() -> None:
        nonlocal second_ran
        second_ran = True

    tm.register_cancel_hook(failing_hook)
    tm.register_cancel_hook(healthy_hook)
    await tm.start_listening("turn-err")
    await tm.start_speaking()

    interrupted = await tm.handle_barge_in()
    assert interrupted is True
    assert second_ran is True
    assert tm.state == TurnState.INTERRUPTED


@pytest.mark.asyncio
async def test_false_interruption_restores_speaking_state() -> None:
    """Transient noise/cough false interruptions must restore SPEAKING without getting stuck."""
    tm = TurnManager()
    assert await tm.resume_after_false_interruption() is False

    await tm.start_listening("turn-cough")
    await tm.start_speaking()
    assert await tm.handle_barge_in() is True
    assert tm.state == TurnState.INTERRUPTED

    assert await tm.resume_after_false_interruption() is True
    assert tm.state == TurnState.SPEAKING
    assert await tm.resume_after_false_interruption() is False
