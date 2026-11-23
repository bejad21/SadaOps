import type { TrackReferenceOrPlaceholder } from "@livekit/components-react";
import { Hand, Mic, MicOff, Radio, Waves } from "lucide-react";
import { useEffect, useRef } from "react";
import { UI_STRINGS } from "../i18n/strings";
import type { AgentState, MicMode, TurnRouteInfo, UiLocale } from "../types";

interface Props {
  agentState: AgentState;
  connected: boolean;
  locale: UiLocale;
  audioTrack?: TrackReferenceOrPlaceholder;
  localAudioTrack?: TrackReferenceOrPlaceholder;
  routeInfo: TurnRouteInfo | null;
  micMode: MicMode;
  micMuted: boolean;
  pttHolding: boolean;
  onChangeMicMode: (mode: MicMode) => void;
  onToggleMute: () => void;
  onPttChange: (holding: boolean) => void;
}

const VERTEX_SHADER = `
  attribute vec2 a_position;
  varying vec2 v_uv;
  void main() {
    v_uv = a_position * 0.5 + 0.5;
    gl_Position = vec4(a_position, 0.0, 1.0);
  }
`;

// Procedural 3D Fresnel + harmonic liquid acoustic orb (zero external noise textures)
const FRAGMENT_SHADER = `
  precision highp float;
  varying vec2 v_uv;
  uniform float u_time;
  uniform float u_agent_amp;
  uniform float u_user_amp;
  uniform float u_state;
  uniform float u_connected;

  vec2 hash2(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
  }

  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(dot(hash2(i + vec2(0.0, 0.0)), f - vec2(0.0, 0.0)),
          dot(hash2(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0)), u.x),
      mix(dot(hash2(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0)),
          dot(hash2(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0)), u.x),
      u.y
    );
  }

  float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 4; i++) {
      v += a * noise(p);
      p *= 2.02;
      a *= 0.5;
    }
    return v;
  }

  void main() {
    vec2 p = v_uv * 2.0 - 1.0;
    float r = length(p);
    float angle = atan(p.y, p.x);

    float warp = 0.018 + u_agent_amp * 0.085 + u_user_amp * 0.04;
    float speed = 0.35 + u_state * 0.25 + u_agent_amp * 0.9;
    float wave = sin(angle * 3.0 + u_time * speed * 1.4) * warp * 0.5
               + fbm(p * 2.1 + vec2(u_time * speed * 0.28, -u_time * speed * 0.22)) * warp;

    float baseRadius = 0.58 + 0.012 * sin(u_time * 1.2) + u_agent_amp * 0.055;
    float radius = baseRadius + wave;
    float sphereMask = 1.0 - smoothstep(radius - 0.014, radius + 0.014, r);

    // Pseudo-3D spherical normal & Fresnel specular rim
    vec2 sp = p / max(0.001, radius);
    float z = sqrt(max(0.0, 1.0 - dot(sp, sp)));
    vec3 n = normalize(vec3(sp, z));
    float fresnel = pow(1.0 - max(0.0, n.z), 2.3);
    float specular = pow(max(0.0, dot(n, normalize(vec3(-0.45, 0.55, 0.70)))), 18.0);

    // Internal sculpted caustic ribbons
    float ribbonA = sin(sp.x * 4.2 + sp.y * 2.6 + fbm(sp * 2.5 + u_time * 0.25) * 3.2);
    float ribbonB = cos(sp.y * 3.8 - sp.x * 2.1 - u_time * 0.35 + u_agent_amp * 3.0);
    float caustic = smoothstep(-0.2, 0.85, (ribbonA + ribbonB) * 0.5);

    vec3 obsidianCore = vec3(0.10, 0.075, 0.065);
    vec3 bronzeMid = vec3(0.56, 0.24, 0.14);
    vec3 copperGlow = vec3(0.89, 0.43, 0.23);
    vec3 amberRim = vec3(0.98, 0.78, 0.44);
    vec3 sageThink = vec3(0.45, 0.78, 0.58);
    vec3 crimsonAlert = vec3(0.90, 0.31, 0.27);

    vec3 accent = copperGlow;
    if (u_state > 1.5 && u_state < 2.5) {
      accent = mix(copperGlow, sageThink, 0.65);
    } else if (u_state > 3.5) {
      accent = crimsonAlert;
    }

    vec3 sphereCol = mix(obsidianCore, bronzeMid, 0.35 + caustic * 0.45);
    sphereCol = mix(sphereCol, accent, caustic * (0.45 + u_agent_amp * 0.45));
    sphereCol += amberRim * fresnel * (0.72 + u_agent_amp * 0.55);
    sphereCol += vec3(1.0, 0.93, 0.82) * specular * 0.55;

    // Fine precision user-mic resonance ring
    float ringR = baseRadius + 0.14 + u_user_amp * 0.09;
    float ringD = abs(r - ringR - sin(angle * 4.0 - u_time * 2.0) * (0.004 + u_user_amp * 0.02));
    float ringAlpha = (1.0 - smoothstep(0.0, 0.011 + u_user_amp * 0.008, ringD)) * (0.28 + u_user_amp * 0.72 + u_connected * 0.14);

    vec3 outRgb = sphereCol * sphereMask + mix(copperGlow, amberRim, 0.65) * ringAlpha;
    float outAlpha = clamp(sphereMask + ringAlpha * 0.8, 0.0, 1.0);
    gl_FragColor = vec4(outRgb, outAlpha);
  }
`;

function stateToCode(state: AgentState): number {
  switch (state) {
    case "listening":
      return 1;
    case "thinking":
      return 2;
    case "speaking":
      return 3;
    case "interrupted":
      return 4;
    default:
      return 0;
  }
}

function stateHeadline(state: AgentState, connected: boolean, locale: UiLocale): string {
  if (locale === "ar") {
    if (!connected) return "الاستوديو الصوتي جاهز للأوامر";
    switch (state) {
      case "listening":
        return "يستمع الآن — يمكنك المقاطعة في أي لحظة";
      case "thinking":
        return "يعالج التوجيه والأدوات المتصلة";
      case "speaking":
        return "استجابة صوتية مباشرة عبر WebRTC";
      case "interrupted":
        return "تمت المقاطعة — إعادة ضبط المسار";
      default:
        return "الجلسة الصوتية نشطة";
    }
  }
  if (!connected) return "Acoustic Channel Standing By";
  switch (state) {
    case "listening":
      return "Listening — Full-Duplex Barge-In Active";
    case "thinking":
      return "Routing Intent & MCP Telemetry";
    case "speaking":
      return "Synthesizing Live Voice Response";
    case "interrupted":
      return "Barge-In Detected — Re-aligning Turn";
    default:
      return "Voice Session Live";
  }
}

function compileShader(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const s = gl.createShader(type);
  if (!s) return null;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    gl.deleteShader(s);
    return null;
  }
  return s;
}

export function AcousticStage({
  agentState,
  connected,
  locale,
  audioTrack,
  localAudioTrack,
  routeInfo,
  micMode,
  micMuted,
  pttHolding,
  onChangeMicMode,
  onToggleMute,
  onPttChange,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef<AgentState>(agentState);
  const connectedRef = useRef<boolean>(connected);
  const agentAmpRef = useRef<number>(0);
  const userAmpRef = useRef<number>(0);
  const t = UI_STRINGS[locale];

  stateRef.current = agentState;
  connectedRef.current = connected;

  // Bind live Web Audio AnalyserNode to a LiveKit audio track
  const bindAnalyser = (
    trackRef: TrackReferenceOrPlaceholder | undefined,
    targetRef: { current: number }
  ) => {
    const mediaStreamTrack = trackRef?.publication?.track?.mediaStreamTrack;
    if (!mediaStreamTrack || typeof window === "undefined") {
      return undefined;
    }
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return undefined;

    let ctx: AudioContext | null = null;
    let raf = 0;
    try {
      ctx = new AudioCtx();
      const stream = new MediaStream([mediaStreamTrack]);
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.82;
      source.connect(analyser);
      const bins = new Uint8Array(analyser.frequencyBinCount);

      const sample = () => {
        analyser.getByteFrequencyData(bins);
        let sum = 0;
        for (let i = 0; i < bins.length; i++) sum += bins[i];
        targetRef.current = Math.min(1, sum / (bins.length * 140));
        raf = window.requestAnimationFrame(sample);
      };
      raf = window.requestAnimationFrame(sample);
    } catch {
      // Fallback to state-driven amplitude if AudioContext is blocked
    }

    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      if (ctx) void ctx.close().catch(() => undefined);
      targetRef.current = 0;
    };
  };

  useEffect(() => bindAnalyser(audioTrack, agentAmpRef), [audioTrack]);
  useEffect(() => bindAnalyser(localAudioTrack, userAmpRef), [localAudioTrack]);

  // Render WebGL procedural acoustic orb (with graceful 2D canvas fallback and reduced-motion gate)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof window === "undefined") return;
    if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const size = 176;
    canvas.width = size * dpr;
    canvas.height = size * dpr;

    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let gl: WebGLRenderingContext | null = null;
    try {
      gl = canvas.getContext("webgl", { alpha: true, antialias: true });
    } catch {
      gl = null;
    }

    let raf = 0;
    let disposed = false;

    if (gl) {
      const vs = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
      const fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
      const program = vs && fs ? gl.createProgram() : null;

      if (program && vs && fs) {
        gl.attachShader(program, vs);
        gl.attachShader(program, fs);
        gl.linkProgram(program);

        if (gl.getProgramParameter(program, gl.LINK_STATUS)) {
          gl.useProgram(program);
          const buf = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, buf);
          gl.bufferData(
            gl.ARRAY_BUFFER,
            new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
            gl.STATIC_DRAW
          );
          const posLoc = gl.getAttribLocation(program, "a_position");
          gl.enableVertexAttribArray(posLoc);
          gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

          const uTime = gl.getUniformLocation(program, "u_time");
          const uAgent = gl.getUniformLocation(program, "u_agent_amp");
          const uUser = gl.getUniformLocation(program, "u_user_amp");
          const uState = gl.getUniformLocation(program, "u_state");
          const uConn = gl.getUniformLocation(program, "u_connected");

          const start = performance.now();
          let smoothAgent = 0;
          let smoothUser = 0;

          const drawGl = (now: number) => {
            if (disposed || !gl) return;
            const st = stateRef.current;
            const tSec = reducedMotion ? 0 : (now - start) / 1000;
            const targetAgent =
              agentAmpRef.current > 0.02
                ? agentAmpRef.current
                : st === "speaking"
                  ? 0.45 + 0.25 * Math.sin(tSec * 5.2)
                  : st === "thinking"
                    ? 0.24
                    : 0.05;
            const targetUser =
              userAmpRef.current > 0.02
                ? userAmpRef.current
                : st === "listening"
                  ? 0.42 + 0.16 * Math.sin(tSec * 3.8)
                  : 0.04;

            smoothAgent += (targetAgent - smoothAgent) * 0.14;
            smoothUser += (targetUser - smoothUser) * 0.14;

            gl.viewport(0, 0, canvas.width, canvas.height);
            gl.clearColor(0, 0, 0, 0);
            gl.clear(gl.COLOR_BUFFER_BIT);
            gl.uniform1f(uTime, tSec);
            gl.uniform1f(uAgent, smoothAgent);
            gl.uniform1f(uUser, smoothUser);
            gl.uniform1f(uState, stateToCode(st));
            gl.uniform1f(uConn, connectedRef.current ? 1 : 0);
            gl.drawArrays(gl.TRIANGLES, 0, 6);

            if (!reducedMotion) {
              raf = window.requestAnimationFrame(drawGl);
            }
          };

          raf = window.requestAnimationFrame(drawGl);
          return () => {
            disposed = true;
            if (raf) window.cancelAnimationFrame(raf);
          };
        }
      }
    }

    // 2D Canvas Fallback (works cleanly in software-rasterized or non-WebGL browsers)
    let ctx2d: CanvasRenderingContext2D | null = null;
    try {
      ctx2d = canvas.getContext("2d");
    } catch {
      ctx2d = null;
    }
    if (!ctx2d) return;

    const start2d = performance.now();
    const draw2d = (now: number) => {
      if (disposed || !ctx2d) return;
      const tSec = reducedMotion ? 0 : (now - start2d) / 1000;
      const st = stateRef.current;
      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      const baseR = w * 0.28 + (st === "speaking" ? 8 * Math.sin(tSec * 5) : 3 * Math.sin(tSec * 1.5));

      ctx2d.clearRect(0, 0, w, h);

      ctx2d.beginPath();
      ctx2d.arc(cx, cy, baseR + 18 * dpr, 0, Math.PI * 2);
      ctx2d.strokeStyle = st === "listening" ? "rgba(232, 169, 92, 0.72)" : "rgba(214, 102, 56, 0.34)";
      ctx2d.lineWidth = 2 * dpr;
      ctx2d.stroke();

      const grad = ctx2d.createRadialGradient(
        cx - baseR * 0.25,
        cy - baseR * 0.25,
        baseR * 0.1,
        cx,
        cy,
        baseR
      );
      grad.addColorStop(0, "rgba(244, 193, 114, 0.95)");
      grad.addColorStop(0.55, "rgba(205, 92, 48, 0.88)");
      grad.addColorStop(1, "rgba(42, 26, 20, 0.95)");
      ctx2d.beginPath();
      ctx2d.arc(cx, cy, baseR, 0, Math.PI * 2);
      ctx2d.fillStyle = grad;
      ctx2d.fill();

      if (!reducedMotion) {
        raf = window.requestAnimationFrame(draw2d);
      }
    };

    raf = window.requestAnimationFrame(draw2d);
    return () => {
      disposed = true;
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, []);

  // Keyboard Space-bar hold for Push-to-Talk (ignored inside inputs/textareas/buttons)
  useEffect(() => {
    if (!connected || micMode !== "ptt" || typeof window === "undefined") {
      return;
    }
    const isEditable = (target: EventTarget | null): boolean => {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
    };
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.code !== "Space" || ev.repeat || isEditable(ev.target)) return;
      ev.preventDefault();
      onPttChange(true);
    };
    const onKeyUp = (ev: KeyboardEvent) => {
      if (ev.code !== "Space" || isEditable(ev.target)) return;
      ev.preventDefault();
      onPttChange(false);
    };
    const onBlur = () => onPttChange(false);

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      onPttChange(false);
    };
  }, [connected, micMode, onPttChange]);

  const badgeTone =
    agentState === "interrupted"
      ? "pill pill-bad"
      : agentState === "speaking" || agentState === "listening"
        ? "pill pill-ok"
        : agentState === "thinking"
          ? "pill pill-warn"
          : "pill";

  const activeHint =
    micMode === "ptt" ? t.micHintPtt : micMuted ? t.micHintMuted : t.micHint;

  return (
    <section className="panel acoustic-stage" aria-label="Voice Acoustic Stage">
      <div className="orb-frame" aria-hidden="true">
        <canvas ref={canvasRef} className="orb-canvas" />
      </div>

      <div className="stage-readout">
        <div className="stage-meta-row">
          <span className={badgeTone} role="status" aria-atomic="true">
            <Radio size={13} aria-hidden="true" />
            <span>{agentState}</span>
          </span>
          {routeInfo && (
            <span className="pill pill-mono" data-testid="turn-route-pill">
              {routeInfo.supervisor ? "120B Supervisor" : "20B"} · {routeInfo.domains.join("+")} (
              {routeInfo.tool_count})
            </span>
          )}
        </div>

        <h2 className="stage-headline">{stateHeadline(agentState, connected, locale)}</h2>

        <p className="stage-subnote" style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem" }}>
          {micMode === "vad" && micMuted ? (
            <MicOff size={15} aria-hidden="true" />
          ) : connected ? (
            <Waves size={15} aria-hidden="true" />
          ) : (
            <Mic size={15} aria-hidden="true" />
          )}
          <span>{activeHint}</span>
        </p>

        <div className="mic-controls-row">
          <div
            className="mic-mode-switch"
            role="radiogroup"
            aria-label={t.micModeSwitchLabel}
            data-testid="mic-mode-switch"
          >
            <button
              type="button"
              role="radio"
              aria-checked={micMode === "vad"}
              className={`seg-btn ${micMode === "vad" ? "seg-btn-active" : ""}`}
              data-testid="mic-mode-vad"
              onClick={() => onChangeMicMode("vad")}
            >
              <Waves size={14} aria-hidden="true" />
              <span>{t.micVadBtn}</span>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={micMode === "ptt"}
              className={`seg-btn ${micMode === "ptt" ? "seg-btn-active" : ""}`}
              data-testid="mic-mode-ptt"
              onClick={() => onChangeMicMode("ptt")}
            >
              <Hand size={14} aria-hidden="true" />
              <span>{t.micPttBtn}</span>
            </button>
          </div>

          {micMode === "vad" ? (
            <button
              type="button"
              className={`btn btn-compact ${micMuted ? "btn-danger" : ""}`}
              data-testid="mic-mute-btn"
              aria-pressed={micMuted}
              onClick={onToggleMute}
            >
              {micMuted ? <MicOff size={14} aria-hidden="true" /> : <Mic size={14} aria-hidden="true" />}
              <span>{micMuted ? t.micUnmuteBtn : t.micMuteBtn}</span>
            </button>
          ) : (
            <button
              type="button"
              className={`btn btn-compact btn-ptt ${pttHolding ? "btn-primary" : ""}`}
              data-testid="ptt-hold-btn"
              disabled={!connected}
              style={{ touchAction: "none" }}
              onPointerDown={(e) => {
                if (!connected) return;
                e.currentTarget.setPointerCapture?.(e.pointerId);
                onPttChange(true);
              }}
              onPointerUp={() => onPttChange(false)}
              onPointerLeave={() => onPttChange(false)}
              onPointerCancel={() => onPttChange(false)}
            >
              <Mic size={14} aria-hidden="true" />
              <span>{pttHolding ? t.pttSpeaking : t.pttHoldBtn}</span>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
