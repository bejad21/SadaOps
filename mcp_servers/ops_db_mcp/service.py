"""Read-only Ops Database service backed by PostgreSQL (ops_readonly role)."""

from __future__ import annotations

import os
from typing import Any

import psycopg
from pydantic import BaseModel, Field, field_validator

from mcp_servers.ops_db_mcp.sql_guard import validate_and_rewrite_sql

DEFAULT_READONLY_DSN = "postgresql://ops_readonly:ops_readonly_pass@127.0.0.1:5432/sadaops"
_SERVICE_MATCH_SQL = (
    "REPLACE(REPLACE(LOWER(service_name), '-', ''), ' ', '') = "
    "REPLACE(REPLACE(LOWER(%s), '-', ''), ' ', '')"
)


class IncidentQuery(BaseModel):
    """Validated filter parameters for incident queries."""

    service: str | None = Field(default=None, max_length=64)
    severity: str | None = Field(default=None, pattern="^(sev1|sev2|sev3|sev4)$")
    status: str | None = Field(default=None, pattern="^(open|investigating|mitigated|resolved)$")
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("service", "severity", "status", mode="before")
    @classmethod
    def _normalize_enum_field(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip().lower()
            return stripped or None
        return value


class OpsDBService:
    """Executes safe read-only queries against SadaOps Ops tables."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("OPS_READONLY_DATABASE_URL", DEFAULT_READONLY_DSN)

    def get_recent_incidents(
        self,
        service: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Query recent incidents filtered by optional service, severity, and status."""
        params = IncidentQuery(service=service, severity=severity, status=status, limit=limit)
        clauses: list[str] = []
        values: list[Any] = []
        if params.service:
            clauses.append(_SERVICE_MATCH_SQL)
            values.append(params.service)
        if params.severity:
            clauses.append("severity = %s")
            values.append(params.severity)
        if params.status:
            clauses.append("status = %s")
            values.append(params.status)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT incident_code, service_name, severity, status, title, summary, "
            "assigned_to, created_at::text "
            f"FROM ops_incidents {where_sql} ORDER BY created_at DESC LIMIT %s"
        )
        values.append(params.limit)
        return self._fetch_dicts(query, tuple(values))

    def get_service_metrics(self, service: str | None = None) -> list[dict[str, Any]]:
        """Return recent latency and error-rate metrics for a service or top offending services."""
        if service and service.strip():
            query = (
                "SELECT service_name, p50_latency_ms::float, p95_latency_ms::float, "
                "error_rate_pct::float, requests_per_min, recorded_at::text "
                f"FROM service_metrics WHERE {_SERVICE_MATCH_SQL} "
                "ORDER BY recorded_at DESC LIMIT 10"
            )
            return self._fetch_dicts(query, (service.strip(),))
        query = (
            "SELECT service_name, p50_latency_ms::float, p95_latency_ms::float, "
            "error_rate_pct::float, requests_per_min, recorded_at::text "
            "FROM service_metrics ORDER BY error_rate_pct DESC LIMIT 20"
        )
        return self._fetch_dicts(query, ())

    def run_readonly_sql(self, sql: str) -> dict[str, Any]:
        """Validate and execute a read-only SQL SELECT query against Ops tables."""
        safe_sql = validate_and_rewrite_sql(sql)
        rows = self._fetch_dicts(safe_sql, ())
        return {"executed_sql": safe_sql, "row_count": len(rows), "rows": rows}

    def _fetch_dicts(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute(query, params)
                cols = [desc.name for desc in cur.description or []]
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
