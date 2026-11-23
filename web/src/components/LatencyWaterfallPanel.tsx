import { motion } from "framer-motion";
import { Gauge } from "lucide-react";
import { UI_STRINGS } from "../i18n/strings";
import type { LatencyWaterfall, UiLocale } from "../types";

interface Props {
  waterfalls: LatencyWaterfall[];
  locale: UiLocale;
}

function totalBadgeClass(totalMs: number | null): string {
  if (totalMs === null) return "pill";
  if (totalMs <= 700) return "pill pill-ok";
  if (totalMs <= 1200) return "pill pill-warn";
  return "pill pill-bad";
}

export function LatencyWaterfallPanel({ waterfalls, locale }: Props) {
  const t = UI_STRINGS[locale];

  return (
    <section className="panel" aria-label={t.waterfallTitle}>
      <div className="panel-header">
        <h2>
          <Gauge size={16} aria-hidden="true" />
          <span>{t.waterfallTitle}</span>
        </h2>
      </div>

      {waterfalls.length === 0 ? (
        <p className="panel-empty">{t.waterfallEmpty}</p>
      ) : (
        <div className="rail-list">
          {waterfalls.map((wf, idx) => {
            const stt = wf.stt_ms ?? 0;
            const llm = wf.llm_ttft_ms ?? 0;
            const mcp = wf.mcp_ms ?? 0;
            const tts = wf.tts_ttfb_ms ?? 0;
            const denom = Math.max(1, stt + llm + mcp + tts);
            return (
              <motion.div
                key={`${wf.session_id}-${wf.turn_id}-${idx}`}
                className="rail-item"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
              >
                <div className="rail-row-head">
                  <span className="mono-code">
                    <code>{wf.turn_id}</code> · {wf.providers.stt} / {wf.providers.tts}
                  </span>
                  <span className={totalBadgeClass(wf.mouth_to_ear_ms)}>
                    {t.mouthToEar}:{" "}
                    {wf.mouth_to_ear_ms !== null ? `${Math.round(wf.mouth_to_ear_ms)} ms` : "—"}
                  </span>
                </div>

                <div className="bar-track">
                  <div
                    className="bar-segment bar-stt"
                    style={{ width: `${(stt / denom) * 100}%` }}
                    title={`STT: ${stt} ms`}
                  />
                  <div
                    className="bar-segment bar-llm"
                    style={{ width: `${(llm / denom) * 100}%` }}
                    title={`LLM TTFT: ${llm} ms`}
                  />
                  <div
                    className="bar-segment bar-mcp"
                    style={{ width: `${(mcp / denom) * 100}%` }}
                    title={`MCP: ${mcp} ms`}
                  />
                  <div
                    className="bar-segment bar-tts"
                    style={{ width: `${(tts / denom) * 100}%` }}
                    title={`TTS TTFB: ${tts} ms`}
                  />
                </div>

                <div className="stage-legend">
                  <span className="legend-item">
                    <span className="legend-swatch bar-stt" aria-hidden="true" />
                    <span>STT: {wf.stt_ms ?? "—"} ms</span>
                  </span>
                  <span className="legend-item">
                    <span className="legend-swatch bar-llm" aria-hidden="true" />
                    <span>LLM TTFT: {wf.llm_ttft_ms ?? "—"} ms</span>
                  </span>
                  <span className="legend-item">
                    <span className="legend-swatch bar-mcp" aria-hidden="true" />
                    <span>MCP: {wf.mcp_ms} ms</span>
                  </span>
                  <span className="legend-item">
                    <span className="legend-swatch bar-tts" aria-hidden="true" />
                    <span>TTS TTFB: {wf.tts_ttfb_ms ?? "—"} ms</span>
                  </span>
                </div>
              </motion.div>
            );
          })}
        </div>
      )}
    </section>
  );
}
