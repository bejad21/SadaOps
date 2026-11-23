import { ArrowUpRight, Gauge, Terminal } from "lucide-react";
import { UI_STRINGS } from "../i18n/strings";
import type { LatencyWaterfall, ToolExecutionEvent, UiLocale } from "../types";

interface Props {
  waterfalls: LatencyWaterfall[];
  tools: ToolExecutionEvent[];
  locale: UiLocale;
  onOpenTelemetry: () => void;
}

export function CompactTelemetryBar({ waterfalls, tools, locale, onOpenTelemetry }: Props) {
  const t = UI_STRINGS[locale];
  const latest = waterfalls[0] ?? null;
  const errCount = tools.filter((ev) => ev.status === "error").length;

  const stt = latest?.stt_ms ?? 0;
  const llm = latest?.llm_ttft_ms ?? 0;
  const mcp = latest?.mcp_ms ?? 0;
  const tts = latest?.tts_ttfb_ms ?? 0;
  const denom = Math.max(1, stt + llm + mcp + tts);

  return (
    <section className="panel compact-telemetry" aria-label={t.compactSummaryTitle}>
      <div className="panel-header" style={{ marginBottom: "0.65rem" }}>
        <h2>
          <Gauge size={16} aria-hidden="true" />
          <span>{t.compactSummaryTitle}</span>
        </h2>
        <button
          type="button"
          className="btn btn-ghost compact-open-btn"
          data-testid="open-telemetry-btn"
          onClick={onOpenTelemetry}
        >
          <span>{t.openTelemetryBtn}</span>
          <ArrowUpRight size={14} aria-hidden="true" />
        </button>
      </div>

      {latest ? (
        <div className="compact-metrics">
          <div className="rail-row-head">
            <span className="mono-code">
              {latest.turn_id} · {latest.providers.stt} / {latest.providers.tts}
            </span>
            <span
              className={`pill ${
                latest.mouth_to_ear_ms !== null && latest.mouth_to_ear_ms <= 700
                  ? "pill-ok"
                  : latest.mouth_to_ear_ms !== null && latest.mouth_to_ear_ms <= 1200
                    ? "pill-warn"
                    : "pill-bad"
              }`}
            >
              {t.mouthToEar}:{" "}
              {latest.mouth_to_ear_ms !== null ? `${Math.round(latest.mouth_to_ear_ms)} ms` : "—"}
            </span>
          </div>

          <div className="bar-track" style={{ margin: "0.5rem 0 0.45rem" }}>
            <div className="bar-segment bar-stt" style={{ width: `${(stt / denom) * 100}%` }} />
            <div className="bar-segment bar-llm" style={{ width: `${(llm / denom) * 100}%` }} />
            <div className="bar-segment bar-mcp" style={{ width: `${(mcp / denom) * 100}%` }} />
            <div className="bar-segment bar-tts" style={{ width: `${(tts / denom) * 100}%` }} />
          </div>
        </div>
      ) : (
        <p className="panel-empty" style={{ fontSize: "0.85rem" }}>
          {t.noTurnsCompact}
        </p>
      )}

      <div className="compact-footer">
        <span className="mono-code" style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}>
          <Terminal size={13} aria-hidden="true" />
          <span>
            {tools.length} {t.kpiMcpCalls}
            {errCount > 0 ? ` · ${errCount} err` : ""}
          </span>
        </span>
        <span className="mono-code">
          {waterfalls.length} {t.turnsUnit}
        </span>
      </div>
    </section>
  );
}
