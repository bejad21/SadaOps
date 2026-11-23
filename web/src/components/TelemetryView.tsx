import { Activity, Cpu, Gauge, Search, Terminal } from "lucide-react";
import { useMemo, useState } from "react";
import { UI_STRINGS } from "../i18n/strings";
import type {
  LatencyWaterfall,
  ToolExecutionEvent,
  TurnRouteInfo,
  UiLocale,
} from "../types";
import { LatencyWaterfallPanel } from "./LatencyWaterfallPanel";
import { ToolExecutionPanel } from "./ToolExecutionPanel";

interface Props {
  waterfalls: LatencyWaterfall[];
  tools: ToolExecutionEvent[];
  routeHistory: TurnRouteInfo[];
  locale: UiLocale;
}

function percentile(values: number[], pct: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((pct / 100) * sorted.length) - 1));
  return Math.round(sorted[idx]);
}

export function TelemetryView({ waterfalls, tools, routeHistory, locale }: Props) {
  const t = UI_STRINGS[locale];
  const [serverFilter, setServerFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const stats = useMemo(() => {
    const totals = waterfalls
      .map((w) => w.mouth_to_ear_ms)
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const p50 = percentile(totals, 50);
    const p95 = percentile(totals, 95);

    const completedTools = tools.filter((ev) => ev.status === "completed").length;
    const errorTools = tools.filter((ev) => ev.status === "error").length;

    const supervisorTurns = routeHistory.filter((r) => r.supervisor).length;
    const fastTurns = routeHistory.length - supervisorTurns;
    const totalRoutes = routeHistory.length;
    const supPct = totalRoutes > 0 ? Math.round((supervisorTurns / totalRoutes) * 100) : 0;

    return {
      p50,
      p95,
      completedTools,
      errorTools,
      fastTurns,
      supervisorTurns,
      supPct,
    };
  }, [waterfalls, tools, routeHistory]);

  const servers = useMemo(() => {
    const known = new Set<string>(["ops_db", "github", "workspace", "knowledge", "hitl"]);
    tools.forEach((ev) => {
      if (ev.server) known.add(ev.server);
    });
    return ["all", ...Array.from(known)];
  }, [tools]);

  const filteredTools = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return tools.filter((ev) => {
      if (serverFilter !== "all" && ev.server !== serverFilter) return false;
      if (statusFilter !== "all" && ev.status !== statusFilter) return false;
      if (q) {
        const hay = `${ev.tool} ${ev.server} ${JSON.stringify(ev.args)} ${ev.preview}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [tools, serverFilter, statusFilter, searchQuery]);

  const trendTurns = useMemo(() => [...waterfalls].slice(0, 12).reverse(), [waterfalls]);
  const maxTrendMs = useMemo(() => {
    const vals = trendTurns.map(
      (w) => (w.stt_ms ?? 0) + (w.llm_ttft_ms ?? 0) + (w.mcp_ms ?? 0) + (w.tts_ttfb_ms ?? 0)
    );
    return Math.max(400, ...vals);
  }, [trendTurns]);

  return (
    <div className="telemetry-view" data-testid="telemetry-view">
      <section className="kpi-grid" aria-label="Session Telemetry Statistics">
        <div className="panel kpi-card">
          <div className="kpi-label">
            <Gauge size={14} aria-hidden="true" />
            <span>{t.kpiP50}</span>
          </div>
          <div className="kpi-value" data-testid="kpi-p50">
            {stats.p50 !== null ? `${stats.p50} ms` : "—"}
          </div>
        </div>

        <div className="panel kpi-card">
          <div className="kpi-label">
            <Activity size={14} aria-hidden="true" />
            <span>{t.kpiP95}</span>
          </div>
          <div className="kpi-value" data-testid="kpi-p95">
            {stats.p95 !== null ? `${stats.p95} ms` : "—"}
          </div>
        </div>

        <div className="panel kpi-card">
          <div className="kpi-label">
            <Terminal size={14} aria-hidden="true" />
            <span>{t.kpiMcpCalls}</span>
          </div>
          <div className="kpi-value" data-testid="kpi-mcp-count">
            {tools.length}{" "}
            <span className="kpi-sub">
              ({stats.completedTools} ok
              {stats.errorTools > 0 ? ` · ${stats.errorTools} err` : ""})
            </span>
          </div>
        </div>

        <div className="panel kpi-card">
          <div className="kpi-label">
            <Cpu size={14} aria-hidden="true" />
            <span>{t.kpiRoutingSplit}</span>
          </div>
          <div className="kpi-value" data-testid="kpi-routing-split">
            {stats.fastTurns} / {stats.supervisorTurns}{" "}
            <span className="kpi-sub">({stats.supPct}% 120B)</span>
          </div>
        </div>
      </section>

      {trendTurns.length > 0 && (
        <section className="panel" aria-label={t.turnTrendTitle} style={{ marginTop: "1.15rem" }}>
          <div className="panel-header">
            <h2>{t.turnTrendTitle}</h2>
            <div className="stage-legend">
              <span className="legend-item">
                <span className="legend-swatch bar-stt" aria-hidden="true" />
                <span>STT</span>
              </span>
              <span className="legend-item">
                <span className="legend-swatch bar-llm" aria-hidden="true" />
                <span>LLM</span>
              </span>
              <span className="legend-item">
                <span className="legend-swatch bar-mcp" aria-hidden="true" />
                <span>MCP</span>
              </span>
              <span className="legend-item">
                <span className="legend-swatch bar-tts" aria-hidden="true" />
                <span>TTS</span>
              </span>
            </div>
          </div>

          <div className="trend-chart">
            {trendTurns.map((wf) => {
              const stt = wf.stt_ms ?? 0;
              const llm = wf.llm_ttft_ms ?? 0;
              const mcp = wf.mcp_ms ?? 0;
              const tts = wf.tts_ttfb_ms ?? 0;
              const total = stt + llm + mcp + tts;
              const hPct = Math.max(12, Math.round((total / maxTrendMs) * 100));
              const denom = Math.max(1, total);
              return (
                <div
                  key={`${wf.session_id}-${wf.turn_id}`}
                  className="trend-col"
                  title={`${wf.turn_id}: ${Math.round(wf.mouth_to_ear_ms ?? total)} ms (STT ${stt}, LLM ${llm}, MCP ${mcp}, TTS ${tts})`}
                >
                  <div className="trend-bar-shell" style={{ height: `${hPct}%` }}>
                    <div className="bar-tts" style={{ height: `${(tts / denom) * 100}%` }} />
                    <div className="bar-mcp" style={{ height: `${(mcp / denom) * 100}%` }} />
                    <div className="bar-llm" style={{ height: `${(llm / denom) * 100}%` }} />
                    <div className="bar-stt" style={{ height: `${(stt / denom) * 100}%` }} />
                  </div>
                  <span className="trend-label">{wf.turn_id.replace("turn-", "T")}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <div className="grid telemetry-grid">
        <div className="column-stack">
          <LatencyWaterfallPanel waterfalls={waterfalls} locale={locale} />
        </div>

        <div className="column-stack">
          <section className="panel filter-bar" aria-label="Filter MCP Execution Logs">
            <div className="filter-search-wrap">
              <Search size={15} className="filter-search-icon" aria-hidden="true" />
              <input
                type="search"
                className="input-field filter-search-input"
                data-testid="mcp-search-input"
                dir="auto"
                value={searchQuery}
                placeholder={t.filterSearchPlaceholder}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>

            <div className="filter-chips" role="group" aria-label="Server filter">
              {servers.map((srv) => (
                <button
                  key={srv}
                  type="button"
                  className={`chip-btn ${serverFilter === srv ? "chip-active" : ""}`}
                  data-testid={`filter-server-${srv}`}
                  aria-pressed={serverFilter === srv}
                  onClick={() => setServerFilter(srv)}
                >
                  {srv === "all" ? t.filterAll : srv}
                </button>
              ))}
            </div>

            <div className="filter-chips" role="group" aria-label="Status filter">
              {["all", "completed", "pending_approval", "error"].map((st) => (
                <button
                  key={st}
                  type="button"
                  className={`chip-btn ${statusFilter === st ? "chip-active" : ""}`}
                  data-testid={`filter-status-${st}`}
                  aria-pressed={statusFilter === st}
                  onClick={() => setStatusFilter(st)}
                >
                  {st === "all" ? t.filterAll : st}
                </button>
              ))}
            </div>
          </section>

          <ToolExecutionPanel tools={filteredTools} locale={locale} />
        </div>
      </div>
    </div>
  );
}
