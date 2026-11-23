import type { UiLocale } from "../types";

export const UI_STRINGS: Record<
  UiLocale,
  {
    title: string;
    subtitle: string;
    connect: string;
    disconnect: string;
    connecting: string;
    statusHealthy: string;
    statusDegraded: string;
    navCommander: string;
    navTelemetry: string;
    transcriptTitle: string;
    transcriptEmpty: string;
    waterfallTitle: string;
    waterfallEmpty: string;
    toolsTitle: string;
    toolsEmpty: string;
    hitlTitle: string;
    hitlEmpty: string;
    approveBtn: string;
    rejectBtn: string;
    editBtn: string;
    saveRelockBtn: string;
    cancelEditBtn: string;
    relockedLabel: string;
    lockedHashLabel: string;
    approvedLabel: string;
    rejectedLabel: string;
    interruptedBadge: string;
    typedBadge: string;
    chatPlaceholder: string;
    chatDisconnectedPlaceholder: string;
    chatSendBtn: string;
    chatSendingBtn: string;
    mouthToEar: string;
    jaegerLink: string;
    googleConnectBtn: string;
    googleConnectedLabel: string;
    googleDisconnectBtn: string;
    googleSetupHint: string;
    micHint: string;
    micHintPtt: string;
    micHintMuted: string;
    micVadBtn: string;
    micPttBtn: string;
    micModeSwitchLabel: string;
    micMuteBtn: string;
    micUnmuteBtn: string;
    pttHoldBtn: string;
    pttSpeaking: string;
    voiceHitlOn: string;
    voiceHitlOff: string;
    compactSummaryTitle: string;
    openTelemetryBtn: string;
    noTurnsCompact: string;
    turnsUnit: string;
    kpiP50: string;
    kpiP95: string;
    kpiMcpCalls: string;
    kpiRoutingSplit: string;
    filterAll: string;
    filterSearchPlaceholder: string;
    turnTrendTitle: string;
    langToggle: string;
  }
> = {
  en: {
    title: "SadaOps Cockpit",
    subtitle: "Full-Duplex Bilingual (AR/EN) Voice Operations Commander",
    connect: "Connect Voice Session",
    disconnect: "Disconnect",
    connecting: "Connecting...",
    statusHealthy: "Infra Healthy",
    statusDegraded: "Infra Degraded",
    navCommander: "Commander",
    navTelemetry: "Telemetry & Logs",
    transcriptTitle: "Live Bilingual Transcript",
    transcriptEmpty: "Connect your microphone and speak in English, Arabic, or mixed EN/AR.",
    waterfallTitle: "Per-Turn Latency Waterfall",
    waterfallEmpty: "Latency stages (STT → LLM TTFT → MCP → TTS TTFB) appear here after each turn.",
    toolsTitle: "MCP Tool Execution Feed",
    toolsEmpty: "Live tool calls across Ops DB, GitHub, Workspace, and Runbooks appear here.",
    hitlTitle: "HITL Parameter-Locked Approvals",
    hitlEmpty: "No write actions awaiting approval.",
    approveBtn: "Approve & Execute",
    rejectBtn: "Reject",
    editBtn: "Edit Parameters",
    saveRelockBtn: "Save & Re-lock Hash",
    cancelEditBtn: "Cancel",
    relockedLabel: "Re-locked",
    lockedHashLabel: "Locked SHA-256",
    approvedLabel: "Executed",
    rejectedLabel: "Rejected",
    interruptedBadge: "interrupted",
    typedBadge: "typed",
    chatPlaceholder: "Type a command or fix (e.g. ops-leads@example.com)...",
    chatDisconnectedPlaceholder: "Connect voice session to type commands into live agent...",
    chatSendBtn: "Send",
    chatSendingBtn: "Sending...",
    mouthToEar: "Mouth-to-Ear",
    jaegerLink: "Open Jaeger Traces",
    googleConnectBtn: "Connect Google",
    googleConnectedLabel: "Google",
    googleDisconnectBtn: "Disconnect Google",
    googleSetupHint:
      "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env (redirect URI: http://127.0.0.1:8000/api/integrations/google/callback), then click Connect Google.",
    micHint: "Interrupt anytime (barge-in enabled)",
    micHintPtt: "Push-to-Talk active — hold Space or button to speak",
    micHintMuted: "Microphone muted — agent audio remains live",
    micVadBtn: "Always-On VAD",
    micPttBtn: "Push-to-Talk",
    micModeSwitchLabel: "Microphone input mode",
    micMuteBtn: "Mute Mic",
    micUnmuteBtn: "Unmute Mic",
    pttHoldBtn: "Hold Space or Press to Speak",
    pttSpeaking: "Mic Live (Speaking)",
    voiceHitlOn: "Voice Confirm: ON",
    voiceHitlOff: "UI-Only Lock",
    compactSummaryTitle: "Live Session Telemetry",
    openTelemetryBtn: "Open Telemetry & Logs",
    noTurnsCompact: "No voice turns completed yet.",
    turnsUnit: "turns",
    kpiP50: "P50 Mouth-to-Ear",
    kpiP95: "P95 Mouth-to-Ear",
    kpiMcpCalls: "Tool Calls",
    kpiRoutingSplit: "20B / 120B Split",
    filterAll: "All",
    filterSearchPlaceholder: "Filter tools, args, or output preview...",
    turnTrendTitle: "Turn-by-Turn Latency Trend",
    langToggle: "العربية",
  },
  ar: {
    title: "لوحة تحكم صدى أوبس",
    subtitle: "مساعد عمليات صوتي ثنائي اللغة (عربي / إنجليزي) عبر WebRTC",
    connect: "بدء جلسة صوتية",
    disconnect: "إنهاء الجلسة",
    connecting: "جاري الاتصال...",
    statusHealthy: "البنية جاهزة",
    statusDegraded: "خلل في البنية",
    navCommander: "غرفة العمليات",
    navTelemetry: "القياسات والسجلات",
    transcriptTitle: "النص المباشر ثنائي اللغة",
    transcriptEmpty: "ابدأ الجلسة وتحدث بالعربية أو الإنجليزية أو مزيج بينهما.",
    waterfallTitle: "تحليل زمن الاستجابة لكل دورة",
    waterfallEmpty: "ستظهر تفاصيل التأخير (STT → LLM → MCP → TTS) بعد كل دورة صوتية.",
    toolsTitle: "سجل تنفيذ أدوات MCP",
    toolsEmpty: "ستظهر هنا استدعاءات قواعد البيانات وGitHub والتقويم والأدلة التشغيلية.",
    hitlTitle: "موافقات الأمان المقفلة (HITL)",
    hitlEmpty: "لا توجد إجراءات كتابة بانتظار الموافقة.",
    approveBtn: "موافقة وتنفيذ",
    rejectBtn: "رفض",
    editBtn: "تعديل المعاملات",
    saveRelockBtn: "حفظ وإعادة القفل",
    cancelEditBtn: "إلغاء",
    relockedLabel: "تم إعادة القفل",
    lockedHashLabel: "تجزئة SHA-256 المقفلة",
    approvedLabel: "تم التنفيذ",
    rejectedLabel: "مرفوض",
    interruptedBadge: "تمت المقاطعة",
    typedBadge: "نص مكتوب",
    chatPlaceholder: "اكتب أمراً أو تصحيحاً (مثل البريد الإلكتروني أو اسم المستودع)...",
    chatDisconnectedPlaceholder: "ابدأ الجلسة الصوتية لتتمكن من إرسال الأوامر النصية...",
    chatSendBtn: "إرسال",
    chatSendingBtn: "جاري الإرسال...",
    mouthToEar: "إجمالي الاستجابة",
    jaegerLink: "فتح تتبع Jaeger",
    googleConnectBtn: "ربط Google",
    googleConnectedLabel: "حساب Google",
    googleDisconnectBtn: "فصل حساب Google",
    googleSetupHint:
      "أضف GOOGLE_CLIENT_ID و GOOGLE_CLIENT_SECRET إلى ملف .env ثم اضغط ربط Google لتفعيل Gmail والتقويم.",
    micHint: "يمكنك مقاطعة المساعد في أي لحظة",
    micHintPtt: "وضع الضغط للتحدث — اضغط مطولاً أو على مفتاح المسافة",
    micHintMuted: "الميكروفون مكتوم — صوت المساعد ما زال متصلاً",
    micVadBtn: "استماع تلقائي (VAD)",
    micPttBtn: "اضغط للتحدث",
    micModeSwitchLabel: "وضع إدخال الميكروفون",
    micMuteBtn: "كتم الميكروفون",
    micUnmuteBtn: "تفعيل الميكروفون",
    pttHoldBtn: "اضغط مطولاً أو مفتاح المسافة للتحدث",
    pttSpeaking: "الميكروفون نشط الآن",
    voiceHitlOn: "التأكيد الصوتي: مفعّل",
    voiceHitlOff: "قفل عبر الواجهة فقط",
    compactSummaryTitle: "ملخص الأداء المباشر",
    openTelemetryBtn: "فتح القياسات والسجلات",
    noTurnsCompact: "لم تكتمل أي دورة صوتية بعد.",
    turnsUnit: "دورات",
    kpiP50: "وسيط الاستجابة P50",
    kpiP95: "ذروة الاستجابة P95",
    kpiMcpCalls: "استدعاءات الأدوات",
    kpiRoutingSplit: "توزيع 20B / 120B",
    filterAll: "الكل",
    filterSearchPlaceholder: "ابحث في الأدوات أو المعاملات أو النتائج...",
    turnTrendTitle: "منحنى زمن الاستجابة عبر الدورات",
    langToggle: "English",
  },
};

export function detectTextDirection(text: string): "rtl" | "ltr" {
  // Follow Unicode Bidi first-strong-character rule so Arabic-matrix code-switching renders RTL
  for (const ch of text) {
    if (/[\u0600-\u06FF]/.test(ch)) return "rtl";
    if (/[A-Za-z]/.test(ch)) return "ltr";
  }
  return "ltr";
}
