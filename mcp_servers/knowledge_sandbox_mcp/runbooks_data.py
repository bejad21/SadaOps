"""Seed engineering runbooks (English + Arabic keywords) for Qdrant RAG."""

from __future__ import annotations

RUNBOOKS: list[dict[str, str]] = [
    {
        "id": "rb-001",
        "title": "Auth Gateway Redis 502 Spike Recovery",
        "service": "auth-gateway",
        "content": (
            "Runbook RB-001: Auth Gateway 502 spikes during JWT token refresh. "
            "Symptoms: elevated HTTP 502 above 2%, Redis connection pool exhaustion "
            "when max_connections=20. Mitigation: increase REDIS_MAX_CONNECTIONS to 64, "
            "enable circuit breaker fallback to cached JWKS keys, and monitor p95 latency. "
            "دليل معالجة أخطاء 502 في بوابة المصادقة auth-gateway بسبب امتلاء اتصالات Redis."
        ),
    },
    {
        "id": "rb-002",
        "title": "Billing Worker Webhook Retry Queue Backlog",
        "service": "billing-worker",
        "content": (
            "Runbook RB-002: Billing Worker dead-letter queue exceeding 500 pending jobs. "
            "Root cause is usually downstream payment gateway latency >3.5s. "
            "Mitigation: enable exponential jitter backoff, cap dead-letter queue threshold "
            "at 500 alerts, and pause non-critical invoice batching until backlog drains. "
            "دليل معالجة تراكم مهام الفوترة billing-worker وتأخير بوابة الدفع."
        ),
    },
    {
        "id": "rb-003",
        "title": "Search Indexer Qdrant Batch Upsert Timeouts",
        "service": "search-indexer",
        "content": (
            "Runbook RB-003: Qdrant upsert timeouts on large 100-page PDFs. "
            "Mitigation: reduce upsert batch_size from 256 to 64 vectors and increase "
            "HTTP client timeout to 30 seconds. "
            "دليل حل انتهاء مهلة الإدخال في Qdrant عبر تقليل حجم الدفعة إلى 64."
        ),
    },
    {
        "id": "rb-004",
        "title": "Checkout API Inventory Deadlock Playbook",
        "service": "checkout-api",
        "content": (
            "Runbook RB-004: Concurrent inventory reservation deadlocks in checkout-api. "
            "Mitigation: acquire PostgreSQL advisory locks in sorted SKU ID order before "
            "updating stock rows and keep transaction duration under 50ms."
        ),
    },
    {
        "id": "rb-005",
        "title": "LiveKit WebRTC Audio Drop & ICE Fallback",
        "service": "voice-pipeline",
        "content": (
            "Runbook RB-005: WebRTC audio stalls on corporate VPNs blocking UDP 7882. "
            "Mitigation: verify LiveKit ICE/TCP fallback on port 7881 and check Silero VAD "
            "min_interruption_duration=0.35s to prevent false echo triggers."
        ),
    },
    {
        "id": "rb-006",
        "title": "Groq LLM Rate Limit 429 Failover Procedure",
        "service": "voice-pipeline",
        "content": (
            "Runbook RB-006: Handling HTTP 429 TPM throttling on Groq free tier. "
            "Mitigation: exponential backoff honoring Retry-After, then automatic failover "
            "from openai/gpt-oss-20b to openai/gpt-oss-120b while capping tool-output tokens."
        ),
    },
    {
        "id": "rb-007",
        "title": "Rollback & Canary Deployment Policy",
        "service": "deployments",
        "content": (
            "Runbook RB-007: Mandatory rollback if service error_rate_pct exceeds 2.0% "
            "or p95_latency_ms exceeds 1000ms for 5 consecutive minutes after deployment. "
            "سياسة التراجع التلقائي عند ارتفاع نسبة الأخطاء فوق 2% أو زيادة زمن الاستجابة."
        ),
    },
    {
        "id": "rb-008",
        "title": "On-Call Escalation & Sev1 Communication SOP",
        "service": "operations",
        "content": (
            "Runbook RB-008: Sev1 incidents require an incident ticket, a 15-minute triage "
            "meeting with the owner, and an email summary to ops-leads@example.com. "
            "إجراءات التصعيد لحوادث Sev1 وإرسال ملخص عبر البريد الإلكتروني وحجز اجتماع طارئ."
        ),
    },
]
