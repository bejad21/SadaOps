import { motion } from "framer-motion";
import { Terminal } from "lucide-react";
import { UI_STRINGS } from "../i18n/strings";
import type { ToolExecutionEvent, UiLocale } from "../types";

interface Props {
  tools: ToolExecutionEvent[];
  locale: UiLocale;
}

function statusPillClass(status: ToolExecutionEvent["status"]): string {
  if (status === "completed") return "pill pill-ok";
  if (status === "running" || status === "pending_approval") return "pill pill-warn";
  return "pill pill-bad";
}

export function ToolExecutionPanel({ tools, locale }: Props) {
  const t = UI_STRINGS[locale];

  return (
    <section className="panel" aria-label={t.toolsTitle}>
      <div className="panel-header">
        <h2>
          <Terminal size={16} aria-hidden="true" />
          <span>{t.toolsTitle}</span>
        </h2>
        <span className="pill pill-mono">{tools.length}</span>
      </div>

      {tools.length === 0 ? (
        <p className="panel-empty">{t.toolsEmpty}</p>
      ) : (
        <div className="rail-list">
          {tools.map((ev) => (
            <motion.div
              key={ev.call_id}
              className="rail-item"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            >
              <div className="rail-row-head">
                <span style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem" }}>
                  <code className="mono-code">{ev.server}</code>
                  <span>·</span>
                  <strong>{ev.tool}</strong>
                </span>
                <span className={statusPillClass(ev.status)}>
                  {ev.status} {ev.duration_ms > 0 ? `(${Math.round(ev.duration_ms)} ms)` : ""}
                </span>
              </div>
              <pre dir="ltr" className="code-block">
                {JSON.stringify(ev.args)}
                {ev.preview ? `\n→ ${ev.preview}` : ""}
              </pre>
            </motion.div>
          ))}
        </div>
      )}
    </section>
  );
}
