import {
  LiveKitRoom,
  RoomAudioRenderer,
  type TrackReferenceOrPlaceholder,
  useDataChannel,
  useLocalParticipant,
  useRemoteParticipants,
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";
import { MotionConfig } from "framer-motion";
import { Activity, BarChart3, ExternalLink, Languages, Mail, Mic, PhoneOff, Radio } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AcousticStage } from "./components/AcousticStage";
import { CompactTelemetryBar } from "./components/CompactTelemetryBar";
import { HITLApprovalPanel } from "./components/HITLApprovalPanel";
import { TelemetryView } from "./components/TelemetryView";
import { TranscriptPanel } from "./components/TranscriptPanel";
import { UI_STRINGS } from "./i18n/strings";
import "./styles.css";
import type {
  AgentState,
  CockpitTab,
  DataChannelPayload,
  LatencyWaterfall,
  MicMode,
  PendingActionItem,
  ToolExecutionEvent,
  TranscriptMessage,
  TurnRouteInfo,
  UiLocale,
} from "./types";

interface HealthState {
  status: string;
  checks?: Record<string, string>;
}

interface TokenSession {
  token: string;
  livekit_url: string;
  room_name: string;
}

interface GoogleIntegrationStatus {
  configured: boolean;
  connected: boolean;
  email: string | null;
}

const defaultApiBase =
  typeof window !== "undefined" && window.location.hostname === "127.0.0.1"
    ? "http://127.0.0.1:8000"
    : "http://localhost:8000";
const API_BASE = import.meta.env.VITE_API_URL ?? defaultApiBase;
const JAEGER_URL = import.meta.env.VITE_JAEGER_URL ?? "http://localhost:16686";

interface RoomBridges {
  sendText: (text: string) => Promise<void>;
  notifyHitlEdited: (action: PendingActionItem) => Promise<void>;
  setVoiceHitl: (enabled: boolean) => Promise<void>;
}

function RoomSessionContent({
  micMode,
  micMuted,
  pttHolding,
  onTranscript,
  onWaterfall,
  onAgentState,
  onAudioTrack,
  onLocalAudioTrack,
  onToolEvent,
  onRouteInfo,
  onVoiceHitlState,
  onHitlUpdate,
  onRegisterBridges,
}: {
  micMode: MicMode;
  micMuted: boolean;
  pttHolding: boolean;
  onTranscript: (msg: TranscriptMessage) => void;
  onWaterfall: (wf: LatencyWaterfall) => void;
  onAgentState: (state: AgentState) => void;
  onAudioTrack: (track: TrackReferenceOrPlaceholder | undefined) => void;
  onLocalAudioTrack: (track: TrackReferenceOrPlaceholder | undefined) => void;
  onToolEvent: (ev: ToolExecutionEvent) => void;
  onRouteInfo: (route: TurnRouteInfo) => void;
  onVoiceHitlState: (enabled: boolean) => void;
  onHitlUpdate: () => void;
  onRegisterBridges: (bridges: RoomBridges | null) => void;
}) {
  const { state, audioTrack, agent } = useVoiceAssistant();
  const { localParticipant, microphoneTrack } = useLocalParticipant();
  const room = useRoomContext();
  const remoteParticipants = useRemoteParticipants();

  const agentIdentity =
    agent?.identity ??
    remoteParticipants.find((p) => p.isAgent || p.identity.startsWith("agent-"))?.identity;

  useEffect(() => {
    onAudioTrack(audioTrack);
    return () => onAudioTrack(undefined);
  }, [audioTrack, onAudioTrack]);

  useEffect(() => {
    if (microphoneTrack) {
      onLocalAudioTrack({
        participant: localParticipant,
        publication: microphoneTrack,
        source: microphoneTrack.source,
      });
    } else {
      onLocalAudioTrack(undefined);
    }
    return () => onLocalAudioTrack(undefined);
  }, [localParticipant, microphoneTrack, onLocalAudioTrack]);

  // LiveKitRoom only applies its `audio` prop on SignalConnected; sync runtime Mute / PTT here.
  useEffect(() => {
    const shouldListen = micMode === "ptt" ? pttHolding : !micMuted;
    if (
      typeof localParticipant?.setMicrophoneEnabled === "function" &&
      localParticipant.isMicrophoneEnabled !== shouldListen
    ) {
      void localParticipant.setMicrophoneEnabled(shouldListen).catch(() => undefined);
    }
  }, [localParticipant, microphoneTrack, micMode, micMuted, pttHolding]);

  useEffect(() => {
    if (
      state === "idle" ||
      state === "initializing" ||
      state === "listening" ||
      state === "thinking" ||
      state === "speaking"
    ) {
      onAgentState(state);
    }
  }, [state, onAgentState]);

  useEffect(() => {
    const sendText = async (text: string) => {
      const msgId = `u-${
        typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID().slice(0, 8)
          : String(Date.now())
      }`;
      if (agentIdentity) {
        try {
          await room.localParticipant.performRpc({
            destinationIdentity: agentIdentity,
            method: "sadaops.send_text",
            payload: JSON.stringify({ id: msgId, text }),
          });
          return;
        } catch {
          // Fall back to LiveKit TextStream (lk.chat) if RPC method isn't ready yet
        }
      }
      await room.localParticipant.sendText(text, { topic: "lk.chat" });
    };

    const notifyHitlEdited = async (action: PendingActionItem) => {
      if (!agentIdentity) return;
      try {
        await room.localParticipant.performRpc({
          destinationIdentity: agentIdentity,
          method: "sadaops.notify_hitl_edited",
          payload: JSON.stringify({
            action_id: action.action_id,
            tool_name: action.tool_name,
            payload: action.payload,
          }),
        });
      } catch {
        // Non-fatal if agent already disconnected
      }
    };

    const setVoiceHitl = async (enabled: boolean) => {
      if (!agentIdentity) return;
      try {
        await room.localParticipant.performRpc({
          destinationIdentity: agentIdentity,
          method: "sadaops.set_voice_hitl",
          payload: JSON.stringify({ enabled }),
        });
      } catch {
        // Non-fatal if agent already disconnected
      }
    };

    onRegisterBridges({ sendText, notifyHitlEdited, setVoiceHitl });
    return () => onRegisterBridges(null);
  }, [room, agentIdentity, onRegisterBridges]);

  useDataChannel((msg) => {
    try {
      if (msg.from && !msg.from.isAgent) {
        return;
      }
      const text = new TextDecoder().decode(msg.payload);
      const parsed = JSON.parse(text) as DataChannelPayload;
      if (parsed.type === "transcript") {
        onTranscript({
          id: String(parsed.id ?? `${Date.now()}-${Math.random()}`),
          role: (parsed.role as "user" | "assistant" | "system") ?? "assistant",
          text: String(parsed.text ?? ""),
          interrupted: Boolean(parsed.interrupted),
          source: parsed.source === "text" ? "text" : "voice",
          timestamp: new Date().toLocaleTimeString(),
        });
      } else if (parsed.type === "latency_waterfall") {
        onWaterfall(parsed as unknown as LatencyWaterfall);
      } else if (parsed.type === "agent_state" && typeof parsed.state === "string") {
        onAgentState(parsed.state as AgentState);
      } else if (parsed.type === "tool_event") {
        onToolEvent(parsed as unknown as ToolExecutionEvent);
      } else if (parsed.type === "turn_route") {
        onRouteInfo(parsed as unknown as TurnRouteInfo);
      } else if (parsed.type === "voice_hitl_state" && typeof parsed.enabled === "boolean") {
        onVoiceHitlState(parsed.enabled);
      } else if (parsed.type === "hitl_pending" || parsed.type === "hitl_resolved") {
        onHitlUpdate();
      }
    } catch {
      // Ignore malformed non-JSON DataChannel packets
    }
  });

  return <RoomAudioRenderer />;
}

export default function App() {
  const [locale, setLocale] = useState<UiLocale>("en");
  const [activeTab, setActiveTab] = useState<CockpitTab>("commander");
  const [health, setHealth] = useState<HealthState | null>(null);
  const [session, setSession] = useState<TokenSession | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentState, setAgentState] = useState<AgentState>("idle");
  const [audioTrack, setAudioTrack] = useState<TrackReferenceOrPlaceholder | undefined>(undefined);
  const [localAudioTrack, setLocalAudioTrack] = useState<TrackReferenceOrPlaceholder | undefined>(
    undefined
  );
  const [micMode, setMicMode] = useState<MicMode>("vad");
  const [micMuted, setMicMuted] = useState(false);
  const [pttHolding, setPttHolding] = useState(false);
  const [connectAudio, setConnectAudio] = useState(true);
  const [voiceHitlEnabled, setVoiceHitlEnabled] = useState(true);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [waterfalls, setWaterfalls] = useState<LatencyWaterfall[]>([]);
  const [toolEvents, setToolEvents] = useState<ToolExecutionEvent[]>([]);
  const [pendingActions, setPendingActions] = useState<PendingActionItem[]>([]);
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [routeInfo, setRouteInfo] = useState<TurnRouteInfo | null>(null);
  const [routeHistory, setRouteHistory] = useState<TurnRouteInfo[]>([]);
  const [chatReady, setChatReady] = useState(false);
  const [googleStatus, setGoogleStatus] = useState<GoogleIntegrationStatus | null>(null);
  const [showGooglePanel, setShowGooglePanel] = useState(false);
  const bridgesRef = useRef<RoomBridges | null>(null);

  const handleRegisterBridges = useCallback((bridges: RoomBridges | null) => {
    bridgesRef.current = bridges;
    setChatReady(bridges !== null);
    if (bridges) {
      void bridges.setVoiceHitl(voiceHitlEnabled);
    }
  }, [voiceHitlEnabled]);

  const t = UI_STRINGS[locale];

  const refreshPendingActions = useCallback(async () => {
    try {
      const qs = session?.room_name
        ? `?session_id=${encodeURIComponent(session.room_name)}`
        : "";
      const res = await fetch(`${API_BASE}/api/hitl/pending${qs}`);
      if (!res.ok) return;
      const data = (await res.json()) as { actions?: PendingActionItem[] };
      setPendingActions(data.actions ?? []);
    } catch {
      // Non-fatal if control plane is momentarily unreachable
    }
  }, [session?.room_name]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const refreshGoogleStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/integrations/google/status`);
      if (!res.ok) return;
      const data = (await res.json()) as Partial<GoogleIntegrationStatus>;
      if (typeof data.connected === "boolean") {
        setGoogleStatus({
          configured: Boolean(data.configured),
          connected: data.connected,
          email: data.email ?? null,
        });
      }
    } catch {
      // Non-fatal if control plane is momentarily unreachable
    }
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then(async (res) => {
        const data = await res.json();
        setHealth(res.ok ? data : (data.detail ?? { status: "degraded" }));
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to reach control-plane API");
      });
    void refreshPendingActions();
    void refreshGoogleStatus();
    const onOAuthMessage = (ev: MessageEvent) => {
      if (ev.data && typeof ev.data === "object" && ev.data.type === "sadaops-google-connected") {
        void refreshGoogleStatus();
        setShowGooglePanel(false);
      }
    };
    window.addEventListener("message", onOAuthMessage);
    const timer = window.setInterval(() => {
      void refreshPendingActions();
    }, 10_000);
    return () => {
      window.removeEventListener("message", onOAuthMessage);
      window.clearInterval(timer);
    };
  }, [refreshPendingActions, refreshGoogleStatus]);

  const handleConnect = useCallback(async () => {
    setConnecting(true);
    setError(null);
    setConnectAudio(micMode === "vad" ? !micMuted : pttHolding);
    const roomSuffix =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID().slice(0, 8)
        : String(Date.now());
    const roomName = `sadaops-${roomSuffix}`;
    try {
      const resp = await fetch(`${API_BASE}/api/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room_name: roomName,
          identity: `operator-${Date.now()}`,
          name: "Operator",
          language: "auto",
        }),
      });
      if (!resp.ok) {
        throw new Error(`Token request failed (${resp.status})`);
      }
      const data = (await resp.json()) as TokenSession;
      setSession(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setConnecting(false);
    }
  }, [micMode, micMuted, pttHolding]);

  const handleDisconnect = useCallback(() => {
    setSession(null);
    setAgentState("idle");
    setAudioTrack(undefined);
    setLocalAudioTrack(undefined);
    setPttHolding(false);
    setRouteInfo(null);
    handleRegisterBridges(null);
  }, [handleRegisterBridges]);

  const onTranscript = useCallback((msg: TranscriptMessage) => {
    setMessages((prev) =>
      prev.some((existing) => existing.id === msg.id) ? prev : [...prev, msg].slice(-200)
    );
  }, []);

  const onWaterfall = useCallback((wf: LatencyWaterfall) => {
    setWaterfalls((prev) => [wf, ...prev].slice(0, 30));
  }, []);

  const onToolEvent = useCallback((ev: ToolExecutionEvent) => {
    setToolEvents((prev) => {
      const idx = prev.findIndex((item) => item.call_id === ev.call_id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = ev;
        return next;
      }
      return [ev, ...prev].slice(0, 50);
    });
  }, []);

  const onRouteInfo = useCallback((route: TurnRouteInfo) => {
    setRouteInfo(route);
    setRouteHistory((prev) => [...prev, route].slice(-50));
  }, []);

  const handleChangeMicMode = useCallback((nextMode: MicMode) => {
    setMicMode(nextMode);
    setPttHolding(false);
  }, []);

  const handleToggleMute = useCallback(() => {
    setMicMuted((prev) => !prev);
  }, []);

  const handleToggleVoiceHitl = useCallback(() => {
    setVoiceHitlEnabled((prev) => {
      const next = !prev;
      void bridgesRef.current?.setVoiceHitl(next);
      return next;
    });
  }, []);

  const handleApproveAction = useCallback(
    async (act: PendingActionItem) => {
      setBusyActionId(act.action_id);
      setActionFeedback(null);
      try {
        const res = await fetch(`${API_BASE}/api/hitl/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action_id: act.action_id,
            payload_hash: act.payload_hash,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(String(data.detail ?? `Approve failed (${res.status})`));
        }
        setActionFeedback(`${t.approvedLabel}: ${act.tool_name} (${act.action_id})`);
        await refreshPendingActions();
      } catch (err: unknown) {
        setActionFeedback(err instanceof Error ? err.message : "Approval failed");
      } finally {
        setBusyActionId(null);
      }
    },
    [refreshPendingActions, t.approvedLabel]
  );

  const handleRejectAction = useCallback(
    async (act: PendingActionItem) => {
      setBusyActionId(act.action_id);
      setActionFeedback(null);
      try {
        const res = await fetch(`${API_BASE}/api/hitl/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action_id: act.action_id }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(String(data.detail ?? `Reject failed (${res.status})`));
        }
        setActionFeedback(`${t.rejectedLabel}: ${act.tool_name} (${act.action_id})`);
        await refreshPendingActions();
      } catch (err: unknown) {
        setActionFeedback(err instanceof Error ? err.message : "Rejection failed");
      } finally {
        setBusyActionId(null);
      }
    },
    [refreshPendingActions, t.rejectedLabel]
  );

  const handleEditAction = useCallback(
    async (act: PendingActionItem, updatedPayload: Record<string, unknown>): Promise<boolean> => {
      setBusyActionId(act.action_id);
      setActionFeedback(null);
      try {
        const res = await fetch(`${API_BASE}/api/hitl/update`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action_id: act.action_id,
            payload: updatedPayload,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(String(data.detail ?? `Update failed (${res.status})`));
        }
        const updated = data as PendingActionItem;
        setPendingActions((prev) =>
          prev.map((item) => (item.action_id === updated.action_id ? updated : item))
        );
        setActionFeedback(
          `${t.relockedLabel}: ${updated.tool_name} (${updated.payload_hash.slice(0, 12)}…)`
        );
        await bridgesRef.current?.notifyHitlEdited(updated);
        await refreshPendingActions();
        return true;
      } catch (err: unknown) {
        setActionFeedback(err instanceof Error ? err.message : "Update failed");
        return false;
      } finally {
        setBusyActionId(null);
      }
    },
    [refreshPendingActions, t.relockedLabel]
  );

  const handleSendText = useCallback(async (text: string) => {
    if (!bridgesRef.current) return;
    await bridgesRef.current.sendText(text);
  }, []);

  const handleGoogleConnectClick = useCallback(async () => {
    if (googleStatus?.connected) {
      setShowGooglePanel((prev) => !prev);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/integrations/google/auth-url`);
      if (!res.ok) {
        setShowGooglePanel(true);
        return;
      }
      const data = (await res.json()) as { url?: string };
      if (data.url) {
        window.open(data.url, "sadaops-google-oauth", "width=540,height=680");
      }
    } catch {
      setShowGooglePanel(true);
    }
  }, [googleStatus?.connected]);

  const handleGoogleDisconnect = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/integrations/google/disconnect`, { method: "POST" });
      await refreshGoogleStatus();
      setShowGooglePanel(false);
    } catch {
      setError("Failed to disconnect Google account");
    }
  }, [refreshGoogleStatus]);

  return (
    <MotionConfig reducedMotion="user">
      <main className="cockpit" dir={locale === "ar" ? "rtl" : "ltr"}>
        <header className="header">
          <div className="brand-lockup">
            <h1>{t.title}</h1>
            <p>{t.subtitle}</p>
          </div>

          <nav className="tab-switcher" role="tablist" aria-label="Workspace views">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "commander"}
              className={`tab-btn ${activeTab === "commander" ? "tab-btn-active" : ""}`}
              data-testid="tab-commander"
              onClick={() => setActiveTab("commander")}
            >
              <Radio size={14} aria-hidden="true" />
              <span>{t.navCommander}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "telemetry"}
              className={`tab-btn ${activeTab === "telemetry" ? "tab-btn-active" : ""}`}
              data-testid="tab-telemetry"
              onClick={() => setActiveTab("telemetry")}
            >
              <BarChart3 size={14} aria-hidden="true" />
              <span>{t.navTelemetry}</span>
              {(waterfalls.length > 0 || toolEvents.length > 0) && (
                <span className="pill pill-mono">{waterfalls.length + toolEvents.length}</span>
              )}
            </button>
          </nav>

          <div className="controls">
            <span className={`pill ${health?.status === "ok" ? "pill-ok" : "pill-warn"}`}>
              <span className="pill-dot" aria-hidden="true" />
              <span>{health?.status === "ok" ? t.statusHealthy : t.statusDegraded}</span>
            </span>

            <span className="pill">
              <Activity size={13} aria-hidden="true" />
              <span>Agent: {agentState}</span>
            </span>

            <a className="btn btn-ghost" href={JAEGER_URL} target="_blank" rel="noreferrer">
              <ExternalLink size={15} aria-hidden="true" />
              <span>{t.jaegerLink}</span>
            </a>

            <button
              type="button"
              className={`btn ${googleStatus?.connected ? "btn-google-connected" : "btn-ghost"}`}
              data-testid="google-connect-btn"
              onClick={handleGoogleConnectClick}
            >
              <Mail size={15} aria-hidden="true" />
              <span>
                {googleStatus?.connected
                  ? `${t.googleConnectedLabel}: ${googleStatus.email ?? "ON"}`
                  : t.googleConnectBtn}
              </span>
            </button>

            <button
              type="button"
              className="btn"
              onClick={() => setLocale((prev) => (prev === "en" ? "ar" : "en"))}
            >
              <Languages size={15} aria-hidden="true" />
              <span>{t.langToggle}</span>
            </button>

            {!session ? (
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleConnect}
                disabled={connecting}
              >
                <Mic size={15} aria-hidden="true" />
                <span>{connecting ? t.connecting : t.connect}</span>
              </button>
            ) : (
              <button type="button" className="btn btn-danger" onClick={handleDisconnect}>
                <PhoneOff size={15} aria-hidden="true" />
                <span>{t.disconnect}</span>
              </button>
            )}
          </div>
        </header>

        {error && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}

        {showGooglePanel && (
          <div className="integration-banner" data-testid="google-setup-panel">
            <span>
              {googleStatus?.connected
                ? `${t.googleConnectedLabel}: ${googleStatus.email ?? "Connected"} (Gmail + Google Calendar active)`
                : t.googleSetupHint}
            </span>
            <div style={{ display: "inline-flex", gap: "0.5rem" }}>
              {googleStatus?.connected && (
                <button
                  type="button"
                  className="btn btn-danger btn-compact"
                  data-testid="google-disconnect-btn"
                  onClick={handleGoogleDisconnect}
                >
                  {t.googleDisconnectBtn}
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-compact"
                onClick={() => setShowGooglePanel(false)}
              >
                {t.cancelEditBtn}
              </button>
            </div>
          </div>
        )}

        {session && (
          <LiveKitRoom
            token={session.token}
            serverUrl={session.livekit_url}
            connect={true}
            audio={connectAudio}
            video={false}
            onDisconnected={handleDisconnect}
            onError={(err) => setError(err.message)}
          >
            <RoomSessionContent
              micMode={micMode}
              micMuted={micMuted}
              pttHolding={pttHolding}
              onTranscript={onTranscript}
              onWaterfall={onWaterfall}
              onAgentState={setAgentState}
              onAudioTrack={setAudioTrack}
              onLocalAudioTrack={setLocalAudioTrack}
              onToolEvent={onToolEvent}
              onRouteInfo={onRouteInfo}
              onVoiceHitlState={setVoiceHitlEnabled}
              onHitlUpdate={() => {
                void refreshPendingActions();
              }}
              onRegisterBridges={handleRegisterBridges}
            />
          </LiveKitRoom>
        )}

        {activeTab === "commander" ? (
          <div className="grid" data-testid="commander-view">
            <div className="column-stack">
              <AcousticStage
                agentState={agentState}
                connected={Boolean(session)}
                locale={locale}
                audioTrack={audioTrack}
                localAudioTrack={localAudioTrack}
                routeInfo={routeInfo}
                micMode={micMode}
                micMuted={micMuted}
                pttHolding={pttHolding}
                onChangeMicMode={handleChangeMicMode}
                onToggleMute={handleToggleMute}
                onPttChange={setPttHolding}
              />
              <TranscriptPanel
                messages={messages}
                locale={locale}
                canSendText={chatReady}
                onSendText={handleSendText}
              />
            </div>

            <div className="column-stack">
              <HITLApprovalPanel
                actions={pendingActions}
                locale={locale}
                busyActionId={busyActionId}
                actionFeedback={actionFeedback}
                voiceHitlEnabled={voiceHitlEnabled}
                onToggleVoiceHitl={handleToggleVoiceHitl}
                onApprove={handleApproveAction}
                onReject={handleRejectAction}
                onEdit={handleEditAction}
              />
              <CompactTelemetryBar
                waterfalls={waterfalls}
                tools={toolEvents}
                locale={locale}
                onOpenTelemetry={() => setActiveTab("telemetry")}
              />
            </div>
          </div>
        ) : (
          <TelemetryView
            waterfalls={waterfalls}
            tools={toolEvents}
            routeHistory={routeHistory}
            locale={locale}
          />
        )}
      </main>
    </MotionConfig>
  );
}
