"""Unit tests for TurnLatencyRecord and waterfall math."""

from __future__ import annotations

import json

from agent.pipeline.telemetry import TurnLatencyRecord


def test_latency_waterfall_math_with_mcp() -> None:
    rec = TurnLatencyRecord(
        turn_id="t-1",
        session_id="s-1",
        t0_vad_end_ms=1000.0,
        stt_provider="speechmatics",
        llm_model="openai/gpt-oss-20b",
        tts_provider="cartesia",
    )
    rec.mark_stt_final(1180.0)
    rec.mark_llm_ttft(1330.0)
    rec.mark_mcp_start(1340.0)
    rec.mark_mcp_end(1490.0)
    rec.mark_mcp_start(1500.0)
    rec.mark_mcp_end(1550.0)
    rec.mark_tts_ttfb(1680.0)

    wf = rec.to_waterfall_dict()
    assert wf["stt_ms"] == 180.0
    assert wf["llm_ttft_ms"] == 150.0
    assert wf["mcp_ms"] == 200.0
    assert wf["tts_ttfb_ms"] == 130.0
    assert wf["mouth_to_ear_ms"] == 680.0
    assert json.loads(rec.to_json())["turn_id"] == "t-1"


def test_latency_waterfall_handles_interrupted_partial_turn() -> None:
    rec = TurnLatencyRecord(turn_id="t-2", session_id="s-1", t0_vad_end_ms=500.0, interrupted=True)
    rec.mark_stt_final(650.0)
    wf = rec.to_waterfall_dict()
    assert wf["stt_ms"] == 150.0
    assert wf["llm_ttft_ms"] is None
    assert wf["mouth_to_ear_ms"] is None
    assert wf["mcp_ms"] == 0.0
    assert wf["interrupted"] is True
