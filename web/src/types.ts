export type UiLocale = "en" | "ar";

export type CockpitTab = "commander" | "telemetry";

export type MicMode = "vad" | "ptt";

export type AgentState =
  | "idle"
  | "initializing"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupted";

export interface TranscriptMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  interrupted?: boolean;
  source?: "voice" | "text";
  timestamp: string;
}

export interface TurnRouteInfo {
  domains: string[];
  supervisor: boolean;
  llm_model: string;
  tool_count: number;
  reason: string;
}

export interface LatencyWaterfall {
  type: "latency_waterfall";
  turn_id: string;
  session_id: string;
  stt_ms: number | null;
  llm_ttft_ms: number | null;
  mcp_ms: number;
  tts_ttfb_ms: number | null;
  mouth_to_ear_ms: number | null;
  interrupted: boolean;
  providers: {
    stt: string;
    llm: string;
    tts: string;
  };
}

export interface ToolExecutionEvent {
  type: "tool_event";
  call_id: string;
  tool: string;
  server: string;
  args: Record<string, unknown>;
  status: "running" | "completed" | "error" | "pending_approval" | "cancelled";
  duration_ms: number;
  preview: string;
}

export interface PendingActionItem {
  action_id: string;
  session_id: string;
  tool_name: string;
  payload: Record<string, unknown>;
  payload_hash: string;
  status: "pending" | "approved" | "rejected" | "executed" | "failed" | "expired";
  expires_at: string;
  result?: unknown;
}

export interface DataChannelPayload {
  type:
    | "transcript"
    | "latency_waterfall"
    | "agent_state"
    | "tool_event"
    | "hitl_pending"
    | "hitl_resolved"
    | "turn_route"
    | "voice_hitl_state"
    | "false_interruption";
  [key: string]: unknown;
}
