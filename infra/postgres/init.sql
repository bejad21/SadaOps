-- SadaOps PostgreSQL Schema & Realistic Seed Data
-- Includes a least-privilege read-only role (ops_readonly) for ops_db_mcp.

-- 1. Least-privilege role for Read-Only Ops SQL queries
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ops_readonly') THEN
        CREATE ROLE ops_readonly WITH LOGIN PASSWORD 'ops_readonly_pass';
    END IF;
END
$$;

-- 2. Ops Incidents Table
CREATE TABLE IF NOT EXISTS ops_incidents (
    id SERIAL PRIMARY KEY,
    incident_code VARCHAR(32) UNIQUE NOT NULL,
    service_name VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('sev1', 'sev2', 'sev3', 'sev4')),
    status VARCHAR(24) NOT NULL CHECK (status IN ('open', 'investigating', 'mitigated', 'resolved')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    assigned_to VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- 3. Service Metrics Table
CREATE TABLE IF NOT EXISTS service_metrics (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(64) NOT NULL,
    p50_latency_ms NUMERIC(8, 2) NOT NULL,
    p95_latency_ms NUMERIC(8, 2) NOT NULL,
    error_rate_pct NUMERIC(5, 2) NOT NULL,
    requests_per_min INTEGER NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Deployments Table
CREATE TABLE IF NOT EXISTS deployments (
    id SERIAL PRIMARY KEY,
    service_name VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL,
    commit_sha VARCHAR(12) NOT NULL,
    status VARCHAR(24) NOT NULL CHECK (status IN ('succeeded', 'failed', 'rolled_back')),
    deployed_by VARCHAR(64) NOT NULL,
    deployed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Calendar Events Table (Workspace MCP)
CREATE TABLE IF NOT EXISTS calendar_events (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    organizer VARCHAR(128) NOT NULL,
    attendees TEXT[] NOT NULL DEFAULT '{}',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    location TEXT DEFAULT 'Google Meet',
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. HITL Pending Actions Table (Phase 3 Parameter-Locked Confirmations)
CREATE TABLE IF NOT EXISTS pending_actions (
    action_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    status VARCHAR(24) NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'failed', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    result_json JSONB
);

-- Grant SELECT ONLY on Ops tables to ops_readonly (never calendar_events or pending_actions)
GRANT USAGE ON SCHEMA public TO ops_readonly;
GRANT SELECT ON ops_incidents, service_metrics, deployments TO ops_readonly;

-- Seed Realistic Ops Incidents
INSERT INTO ops_incidents (incident_code, service_name, severity, status, title, summary, assigned_to, created_at, resolved_at)
VALUES
    ('INC-1041', 'auth-gateway', 'sev1', 'investigating', 'Elevated 502 spikes on token refresh', 'Redis connection pool saturation triggered 502 errors for ~4.2% of refresh calls in eu-west.', 'tariq', NOW() - INTERVAL '45 minutes', NULL),
    ('INC-1040', 'billing-worker', 'sev2', 'open', 'Webhook retry queue backlog exceeding 1,500 jobs', 'Downstream payment provider latency jumped to 3.8s causing worker concurrency saturation.', 'sara', NOW() - INTERVAL '3 hours', NULL),
    ('INC-1039', 'search-indexer', 'sev3', 'mitigated', 'Qdrant batch upsert timeout on large PDFs', 'Reduced chunk batch size from 256 to 64; p95 ingestion latency normalized.', 'bejad', NOW() - INTERVAL '1 day', NULL),
    ('INC-1038', 'checkout-api', 'sev2', 'resolved', 'Deadlock on concurrent inventory reservation', 'Added row-level advisory lock ordering; error rate dropped from 2.1% to 0.03%.', 'ahmed', NOW() - INTERVAL '2 days', NOW() - INTERVAL '40 hours'),
    ('INC-1037', 'notification-svc', 'sev4', 'resolved', 'Delayed push delivery in ap-south', 'Transient APNs certificate reload issue resolved after pod rollover.', 'sara', NOW() - INTERVAL '4 days', NOW() - INTERVAL '90 hours')
ON CONFLICT (incident_code) DO NOTHING;

-- Seed Service Metrics
INSERT INTO service_metrics (service_name, p50_latency_ms, p95_latency_ms, error_rate_pct, requests_per_min, recorded_at)
VALUES
    ('auth-gateway', 42.5, 680.0, 4.20, 4120, NOW() - INTERVAL '5 minutes'),
    ('billing-worker', 190.0, 1420.0, 1.85, 620, NOW() - INTERVAL '5 minutes'),
    ('search-indexer', 85.0, 310.0, 0.12, 340, NOW() - INTERVAL '5 minutes'),
    ('checkout-api', 31.2, 118.4, 0.03, 2890, NOW() - INTERVAL '5 minutes'),
    ('notification-svc', 18.0, 64.5, 0.01, 1540, NOW() - INTERVAL '5 minutes');

-- Seed Deployments
INSERT INTO deployments (service_name, version, commit_sha, status, deployed_by, deployed_at)
VALUES
    ('auth-gateway', 'v2.14.3', 'a91f3c2', 'succeeded', 'tariq', NOW() - INTERVAL '70 minutes'),
    ('billing-worker', 'v1.9.0', '7d44e10', 'failed', 'sara', NOW() - INTERVAL '4 hours'),
    ('checkout-api', 'v3.4.1', 'c18b904', 'succeeded', 'ahmed', NOW() - INTERVAL '2 days'),
    ('search-indexer', 'v1.5.2', '55e0a21', 'rolled_back', 'bejad', NOW() - INTERVAL '1 day');

-- Seed Calendar Events (relative to current UTC date so availability checks always stay fresh)
INSERT INTO calendar_events (title, organizer, attendees, start_time, end_time, location, notes)
SELECT
    'Daily Platform Triage',
    'bejad@sadaops.example',
    ARRAY['ahmed@sadaops.example', 'sara@sadaops.example'],
    DATE_TRUNC('day', NOW() + INTERVAL '1 day') + INTERVAL '10 hours',
    DATE_TRUNC('day', NOW() + INTERVAL '1 day') + INTERVAL '10 hours 30 minutes',
    'Google Meet',
    'Review auth-gateway sev1 & billing-worker backlog'
WHERE NOT EXISTS (SELECT 1 FROM calendar_events WHERE title = 'Daily Platform Triage');

INSERT INTO calendar_events (title, organizer, attendees, start_time, end_time, location, notes)
SELECT
    'Architecture Sync: Qdrant & Voice Pipeline',
    'tariq@sadaops.example',
    ARRAY['bejad@sadaops.example'],
    DATE_TRUNC('day', NOW() + INTERVAL '1 day') + INTERVAL '14 hours',
    DATE_TRUNC('day', NOW() + INTERVAL '1 day') + INTERVAL '15 hours',
    'Room B / Meet',
    'Discuss latency budgets'
WHERE NOT EXISTS (SELECT 1 FROM calendar_events WHERE title = 'Architecture Sync: Qdrant & Voice Pipeline');
