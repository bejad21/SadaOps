"""OpenTelemetry instrumentation and per-turn latency waterfall emitter."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_TRACER_INITIALIZED = False


def init_telemetry(service_name: str, otlp_endpoint: str) -> trace.Tracer:
    """Configure OTLP HTTP tracing to Jaeger once and return a Tracer."""
    global _TRACER_INITIALIZED
    if not _TRACER_INITIALIZED:
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        endpoint = otlp_endpoint.rstrip("/") + "/v1/traces"
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _TRACER_INITIALIZED = True
    return trace.get_tracer(service_name)


@dataclass
class TurnLatencyRecord:
    """Tracks monotonic timestamps (ms) across a single conversational voice turn."""

    turn_id: str
    session_id: str
    t0_vad_end_ms: float = field(default_factory=lambda: time.monotonic() * 1000.0)
    t_stt_final_ms: float | None = None
    t_llm_ttft_ms: float | None = None
    t_mcp_start_ms: float | None = None
    t_mcp_end_ms: float | None = None
    total_mcp_ms: float = 0.0
    t_tts_ttfb_ms: float | None = None
    t_turn_end_ms: float | None = None
    interrupted: bool = False
    stt_provider: str = "speechmatics"
    llm_model: str = ""
    tts_provider: str = "edge"

    def mark_stt_final(self, ts_ms: float | None = None) -> None:
        """Record the timestamp when final STT transcript arrives."""
        self.t_stt_final_ms = ts_ms if ts_ms is not None else time.monotonic() * 1000.0

    def mark_llm_ttft(self, ts_ms: float | None = None) -> None:
        """Record the first LLM token timestamp for the turn if not already set."""
        if self.t_llm_ttft_ms is None:
            self.t_llm_ttft_ms = ts_ms if ts_ms is not None else time.monotonic() * 1000.0

    def mark_mcp_start(self, ts_ms: float | None = None) -> None:
        """Record the start timestamp of an MCP tool call."""
        self.t_mcp_start_ms = ts_ms if ts_ms is not None else time.monotonic() * 1000.0

    def mark_mcp_end(self, ts_ms: float | None = None) -> None:
        """Record the completion of an MCP tool call and accumulate total MCP time."""
        end_ts = ts_ms if ts_ms is not None else time.monotonic() * 1000.0
        self.t_mcp_end_ms = end_ts
        if self.t_mcp_start_ms is not None:
            self.total_mcp_ms += max(0.0, end_ts - self.t_mcp_start_ms)

    def mark_tts_ttfb(self, ts_ms: float | None = None) -> None:
        """Record the first TTS audio byte timestamp if not already set."""
        if self.t_tts_ttfb_ms is None:
            self.t_tts_ttfb_ms = ts_ms if ts_ms is not None else time.monotonic() * 1000.0

    def mark_turn_end(self, ts_ms: float | None = None) -> None:
        """Record the turn completion timestamp."""
        self.t_turn_end_ms = ts_ms if ts_ms is not None else time.monotonic() * 1000.0

    def to_waterfall_dict(self) -> dict[str, Any]:
        """Compute stage durations (ms) safely even if a stage was skipped or interrupted."""
        stt_ms = (
            round(max(0.0, self.t_stt_final_ms - self.t0_vad_end_ms), 2)
            if self.t_stt_final_ms is not None
            else None
        )
        llm_anchor = self.t_stt_final_ms if self.t_stt_final_ms is not None else self.t0_vad_end_ms
        llm_ttft_ms = (
            round(max(0.0, self.t_llm_ttft_ms - llm_anchor), 2)
            if self.t_llm_ttft_ms is not None
            else None
        )
        mcp_ms = (
            round(self.total_mcp_ms, 2)
            if self.total_mcp_ms > 0.0
            else (
                round(max(0.0, self.t_mcp_end_ms - self.t_mcp_start_ms), 2)
                if self.t_mcp_start_ms is not None and self.t_mcp_end_ms is not None
                else 0.0
            )
        )
        tts_anchor = (
            max(self.t_llm_ttft_ms or llm_anchor, self.t_mcp_end_ms or llm_anchor)
            if (self.t_llm_ttft_ms is not None or self.t_mcp_end_ms is not None)
            else llm_anchor
        )
        tts_ttfb_ms = (
            round(max(0.0, self.t_tts_ttfb_ms - tts_anchor), 2)
            if self.t_tts_ttfb_ms is not None
            else None
        )
        mouth_to_ear_ms = (
            round(max(0.0, self.t_tts_ttfb_ms - self.t0_vad_end_ms), 2)
            if self.t_tts_ttfb_ms is not None
            else None
        )
        return {
            "type": "latency_waterfall",
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "stt_ms": stt_ms,
            "llm_ttft_ms": llm_ttft_ms,
            "mcp_ms": mcp_ms,
            "tts_ttfb_ms": tts_ttfb_ms,
            "mouth_to_ear_ms": mouth_to_ear_ms,
            "interrupted": self.interrupted,
            "providers": {
                "stt": self.stt_provider,
                "llm": self.llm_model,
                "tts": self.tts_provider,
            },
        }

    def to_json(self) -> str:
        """Serialize the latency waterfall metrics to a JSON string."""
        return json.dumps(self.to_waterfall_dict())


def export_turn_span(tracer: trace.Tracer, record: TurnLatencyRecord) -> None:
    """Emit an OpenTelemetry span carrying the turn's waterfall metrics to Jaeger."""
    metrics = record.to_waterfall_dict()
    with tracer.start_as_current_span("voice.turn") as span:
        span.set_attribute("sadaops.turn_id", record.turn_id)
        span.set_attribute("sadaops.session_id", record.session_id)
        span.set_attribute("sadaops.interrupted", record.interrupted)
        span.set_attribute("sadaops.stt_provider", record.stt_provider)
        span.set_attribute("sadaops.llm_model", record.llm_model)
        span.set_attribute("sadaops.tts_provider", record.tts_provider)
        for key in ("stt_ms", "llm_ttft_ms", "mcp_ms", "tts_ttfb_ms", "mouth_to_ear_ms"):
            val = metrics.get(key)
            if val is not None:
                span.set_attribute(f"sadaops.{key}", float(val))
