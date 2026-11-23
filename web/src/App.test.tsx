import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import App from "./App";
import { HITLApprovalPanel } from "./components/HITLApprovalPanel";
import { LatencyWaterfallPanel } from "./components/LatencyWaterfallPanel";
import { TelemetryView } from "./components/TelemetryView";
import { ToolExecutionPanel } from "./components/ToolExecutionPanel";
import { TranscriptPanel } from "./components/TranscriptPanel";
import { detectTextDirection } from "./i18n/strings";

describe("SadaOps Bilingual Cockpit", () => {
  it("renders cockpit and toggles EN/AR RTL direction", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/hitl/pending")) {
          return { ok: true, json: async () => ({ actions: [] }) };
        }
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            checks: { postgres: "ok", qdrant: "ok", livekit: "ok", mailpit: "ok" },
          }),
        };
      })
    );

    const { container } = render(<App />);
    expect(screen.getByText("SadaOps Cockpit")).toBeInTheDocument();
    expect(await screen.findByText("Infra Healthy")).toBeInTheDocument();

    const mainEl = container.querySelector("main");
    expect(mainEl).toHaveAttribute("dir", "ltr");

    fireEvent.click(screen.getByRole("button", { name: "العربية" }));
    expect(mainEl).toHaveAttribute("dir", "rtl");
    expect(screen.getByText("لوحة تحكم صدى أوبس")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("auto-detects Arabic vs English text direction and renders interrupted badge", () => {
    expect(detectTextDirection("شوفلي الـ incidents على auth-gateway")).toBe("rtl");
    expect(detectTextDirection("Check auth-gateway incidents")).toBe("ltr");

    render(
      <TranscriptPanel
        locale="en"
        messages={[
          {
            id: "1",
            role: "assistant",
            text: "شوفلي المشاكل الحالية",
            interrupted: true,
            timestamp: "12:00:00",
          },
        ]}
      />
    );
    expect(screen.getByText("interrupted")).toBeInTheDocument();
    expect(screen.getByText("شوفلي المشاكل الحالية").closest("article")).toHaveAttribute(
      "dir",
      "rtl"
    );
  });

  it("renders latency waterfall stages and mouth-to-ear ms", () => {
    render(
      <LatencyWaterfallPanel
        locale="en"
        waterfalls={[
          {
            type: "latency_waterfall",
            turn_id: "turn-42",
            session_id: "sess-1",
            stt_ms: 160,
            llm_ttft_ms: 140,
            mcp_ms: 0,
            tts_ttfb_ms: 130,
            mouth_to_ear_ms: 430,
            interrupted: false,
            providers: { stt: "speechmatics", llm: "openai/gpt-oss-20b", tts: "cartesia" },
          },
        ]}
      />
    );
    expect(screen.getByText("Mouth-to-Ear: 430 ms")).toBeInTheDocument();
    expect(screen.getByText("STT: 160 ms")).toBeInTheDocument();
    expect(screen.getByText("LLM TTFT: 140 ms")).toBeInTheDocument();
  });

  it("renders MCP tool execution cards", () => {
    render(
      <ToolExecutionPanel
        locale="en"
        tools={[
          {
            type: "tool_event",
            call_id: "mcp-1",
            tool: "get_recent_incidents",
            server: "ops_db",
            args: { severity: "sev1" },
            status: "completed",
            duration_ms: 42,
            preview: '[{"incident_code":"INC-1041"}]',
          },
        ]}
      />
    );
    expect(screen.getByText("get_recent_incidents")).toBeInTheDocument();
    expect(screen.getByText("completed (42 ms)")).toBeInTheDocument();
  });

  it("renders HITLApprovalPanel with locked payload and fires approve/reject", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    const action = {
      action_id: "act-abc123",
      session_id: "sadaops-main",
      tool_name: "send_email",
      payload: { to: "ops-leads@example.com", subject: "Incident", body: "Summary" },
      payload_hash: "0123456789abcdef0123456789abcdef",
      status: "pending" as const,
      expires_at: "2030-01-01T00:00:00Z",
    };

    render(
      <HITLApprovalPanel
        actions={[action]}
        locale="en"
        busyActionId={null}
        actionFeedback={null}
        onApprove={onApprove}
        onReject={onReject}
      />
    );

    expect(screen.getByText("send_email")).toBeInTheDocument();
    expect(screen.getByText(/0123456789ab/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("hitl-approve-act-abc123"));
    fireEvent.click(screen.getByTestId("hitl-reject-act-abc123"));
    expect(onApprove).toHaveBeenCalledWith(action);
    expect(onReject).toHaveBeenCalledWith(action);
  });

  it("safely renders 1,000 rapid messages and escapes XSS payloads", () => {
    const flood = Array.from({ length: 1000 }, (_, i) => ({
      id: `m-${i}`,
      role: (i % 2 === 0 ? "user" : "assistant") as "user" | "assistant",
      text:
        i === 999
          ? '<script>window.__xss=1</script><img src=x onerror="alert(1)">'
          : `Turn message #${i}`,
      timestamp: "12:00:00",
    }));

    const { container } = render(<TranscriptPanel locale="en" messages={flood} />);
    expect(screen.getByText(/<script>window.__xss=1<\/script>/)).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll("article")).toHaveLength(1000);
  });

  it("keeps JSON pre blocks LTR inside Arabic RTL panels", () => {
    const action = {
      action_id: "act-rtl1",
      session_id: "s1",
      tool_name: "create_calendar_event",
      payload: { title: "مراجعة النظام", duration_minutes: 30 },
      payload_hash: "abcdef123456abcdef123456",
      status: "pending" as const,
      expires_at: "2030-01-01T00:00:00Z",
    };

    const { container } = render(
      <div dir="rtl">
        <HITLApprovalPanel
          actions={[action]}
          locale="ar"
          busyActionId={null}
          actionFeedback={null}
          onApprove={vi.fn()}
          onReject={vi.fn()}
        />
        <ToolExecutionPanel
          locale="ar"
          tools={[
            {
              type: "tool_event",
              call_id: "call-rtl1",
              tool: "create_calendar_event",
              server: "workspace",
              args: { title: "مراجعة النظام" },
              status: "pending_approval",
              duration_ms: 42,
              preview: "Locked",
            },
          ]}
        />
      </div>
    );

    const preBlocks = container.querySelectorAll("pre");
    expect(preBlocks).toHaveLength(2);
    preBlocks.forEach((pre) => {
      expect(pre).toHaveAttribute("dir", "ltr");
    });
  });

  it("submits typed text commands via TranscriptPanel and edits HITL payload inline", async () => {
    const onSendText = vi.fn().mockResolvedValue(undefined);
    render(
      <TranscriptPanel
        locale="en"
        canSendText={true}
        onSendText={onSendText}
        messages={[
          {
            id: "u-1",
            role: "user",
            text: "Check auth-gateway metrics",
            source: "text",
            timestamp: "12:00:00",
          },
        ]}
      />
    );
    expect(screen.getByText("typed")).toBeInTheDocument();
    const input = screen.getByTestId("chat-text-input");
    fireEvent.change(input, { target: { value: "  send it to ops-leads@example.com  " } });
    fireEvent.click(screen.getByTestId("chat-send-btn"));
    await waitFor(() => expect(onSendText).toHaveBeenCalledWith("send it to ops-leads@example.com"));
    await waitFor(() => expect(input).toHaveValue(""));

    const onEdit = vi.fn().mockResolvedValue(true);
    const action = {
      action_id: "act-edit1",
      session_id: "s1",
      tool_name: "send_email",
      payload: { to: "wrong@example.com", subject: "Old", body: "Old body" },
      payload_hash: "11112222333344445555",
      status: "pending" as const,
      expires_at: "2030-01-01T00:00:00Z",
    };

    render(
      <HITLApprovalPanel
        actions={[action]}
        locale="en"
        busyActionId={null}
        actionFeedback={null}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onEdit={onEdit}
      />
    );

    fireEvent.click(screen.getByTestId("hitl-edit-act-edit1"));
    fireEvent.change(screen.getByTestId("hitl-input-act-edit1-to"), {
      target: { value: "ops-leads@example.com" },
    });
    fireEvent.change(screen.getByTestId("hitl-input-act-edit1-subject"), {
      target: { value: "Fixed Subject" },
    });
    fireEvent.click(screen.getByTestId("hitl-save-act-edit1"));
    await waitFor(() =>
      expect(onEdit).toHaveBeenCalledWith(action, {
        to: "ops-leads@example.com",
        subject: "Fixed Subject",
        body: "Old body",
      })
    );
  });

  it("switches between Commander and Telemetry views and toggles Mic/PTT and Voice HITL controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/hitl/pending")) {
          return { ok: true, json: async () => ({ actions: [] }) };
        }
        return {
          ok: true,
          json: async () => ({ status: "ok" }),
        };
      })
    );

    render(<App />);
    expect(await screen.findByText("Infra Healthy")).toBeInTheDocument();
    expect(screen.getByTestId("commander-view")).toBeInTheDocument();

    // Mic controls: VAD + Mute -> Push-to-Talk segmented switch -> back to VAD
    const muteBtn = screen.getByTestId("mic-mute-btn");
    expect(muteBtn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(muteBtn);
    expect(muteBtn).toHaveAttribute("aria-pressed", "true");

    const vadBtn = screen.getByTestId("mic-mode-vad");
    const pttModeBtn = screen.getByTestId("mic-mode-ptt");
    expect(vadBtn).toHaveAttribute("aria-checked", "true");
    expect(pttModeBtn).toHaveAttribute("aria-checked", "false");
    fireEvent.click(pttModeBtn);
    expect(vadBtn).toHaveAttribute("aria-checked", "false");
    expect(pttModeBtn).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("ptt-hold-btn")).toBeDisabled();
    fireEvent.click(vadBtn);
    expect(vadBtn).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("mic-mute-btn")).toHaveAttribute("aria-pressed", "true");

    // Voice HITL safety toggle
    const voiceHitlBtn = screen.getByTestId("voice-hitl-toggle");
    expect(voiceHitlBtn).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(voiceHitlBtn);
    expect(voiceHitlBtn).toHaveAttribute("aria-pressed", "false");

    // Switch to Telemetry & Logs via header tab and back via Commander tab
    fireEvent.click(screen.getByTestId("tab-telemetry"));
    expect(screen.getByTestId("telemetry-view")).toBeInTheDocument();
    expect(screen.queryByTestId("commander-view")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("tab-commander"));
    expect(screen.getByTestId("commander-view")).toBeInTheDocument();

    // Compact summary CTA also navigates cleanly to Telemetry
    fireEvent.click(screen.getByTestId("open-telemetry-btn"));
    expect(screen.getByTestId("telemetry-view")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("shows Google Workspace status, setup guidance when unconfigured, and disconnect when connected", async () => {
    let connected = true;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/hitl/pending")) {
          return { ok: true, json: async () => ({ actions: [] }) };
        }
        if (url.includes("/api/integrations/google/status")) {
          return {
            ok: true,
            json: async () => ({
              configured: true,
              connected,
              email: connected ? "operator@gmail.com" : null,
            }),
          };
        }
        if (url.includes("/api/integrations/google/disconnect")) {
          connected = false;
          return { ok: true, json: async () => ({ disconnected: true }) };
        }
        return { ok: true, json: async () => ({ status: "ok" }) };
      })
    );

    render(<App />);
    const googleBtn = await screen.findByTestId("google-connect-btn");
    await waitFor(() => expect(googleBtn).toHaveTextContent("Google: operator@gmail.com"));

    fireEvent.click(googleBtn);
    expect(screen.getByTestId("google-setup-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("google-disconnect-btn"));
    await waitFor(() => expect(googleBtn).toHaveTextContent("Connect Google"));

    vi.unstubAllGlobals();
  });

  it("computes P50/P95 KPIs and filters MCP logs by server, status, and search query in TelemetryView", () => {
    render(
      <TelemetryView
        locale="en"
        waterfalls={[
          {
            type: "latency_waterfall",
            turn_id: "turn-2",
            session_id: "s1",
            stt_ms: 150,
            llm_ttft_ms: 210,
            mcp_ms: 180,
            tts_ttfb_ms: 140,
            mouth_to_ear_ms: 680,
            interrupted: false,
            providers: { stt: "speechmatics", llm: "openai/gpt-oss-120b", tts: "cartesia" },
          },
          {
            type: "latency_waterfall",
            turn_id: "turn-1",
            session_id: "s1",
            stt_ms: 120,
            llm_ttft_ms: 110,
            mcp_ms: 40,
            tts_ttfb_ms: 110,
            mouth_to_ear_ms: 380,
            interrupted: false,
            providers: { stt: "speechmatics", llm: "openai/gpt-oss-20b", tts: "cartesia" },
          },
        ]}
        routeHistory={[
          { domains: ["ops_db"], supervisor: false, llm_model: "openai/gpt-oss-20b", tool_count: 3, reason: "single" },
          { domains: ["ops_db", "github"], supervisor: true, llm_model: "openai/gpt-oss-120b", tool_count: 7, reason: "multi" },
        ]}
        tools={[
          {
            type: "tool_event",
            call_id: "c1",
            tool: "get_recent_incidents",
            server: "ops_db",
            args: { severity: "sev1" },
            status: "completed",
            duration_ms: 38,
            preview: "INC-1041",
          },
          {
            type: "tool_event",
            call_id: "c2",
            tool: "list_open_prs",
            server: "github",
            args: { repo: "bejad21/sadaops-demo" },
            status: "error",
            duration_ms: 95,
            preview: "rate limit",
          },
        ]}
      />
    );

    expect(screen.getByTestId("kpi-p50")).toHaveTextContent("380 ms");
    expect(screen.getByTestId("kpi-p95")).toHaveTextContent("680 ms");
    expect(screen.getByTestId("kpi-routing-split")).toHaveTextContent("1 / 1 (50% 120B)");
    expect(screen.getByTestId("kpi-mcp-count")).toHaveTextContent("2");

    expect(screen.getByText("get_recent_incidents")).toBeInTheDocument();
    expect(screen.getByText("list_open_prs")).toBeInTheDocument();

    // Filter by github server
    fireEvent.click(screen.getByTestId("filter-server-github"));
    expect(screen.queryByText("get_recent_incidents")).not.toBeInTheDocument();
    expect(screen.getByText("list_open_prs")).toBeInTheDocument();

    // Search query narrows further
    fireEvent.click(screen.getByTestId("filter-server-all"));
    fireEvent.change(screen.getByTestId("mcp-search-input"), { target: { value: "INC-1041" } });
    expect(screen.getByText("get_recent_incidents")).toBeInTheDocument();
    expect(screen.queryByText("list_open_prs")).not.toBeInTheDocument();
  });
});
