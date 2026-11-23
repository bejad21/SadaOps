"""FastMCP server exposing read-only Ops DB tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_servers.ops_db_mcp.service import OpsDBService

mcp = MCPServer("sadaops-ops-db")
_service = OpsDBService()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Recent Operational Incidents",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def get_recent_incidents(
    service: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Fetch recent operational incidents filtered optionally by service, severity, or status."""
    return _service.get_recent_incidents(
        service=service, severity=severity, status=status, limit=limit
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Service Metrics Snapshot",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def get_service_metrics(service: str | None = None) -> list[dict[str, Any]]:
    """Fetch latest latency (p50/p95), error rate, and RPM metrics for services."""
    return _service.get_service_metrics(service=service)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read-Only Ops SQL Query",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def run_readonly_sql(sql: str) -> dict[str, Any]:
    """Run a validated read-only SELECT on ops_incidents, service_metrics, or deployments.

    Tables: ops_incidents(incident_code, service_name, severity, status, title, summary,
    assigned_to, created_at), service_metrics(service_name, p50_latency_ms, p95_latency_ms,
    error_rate_pct, requests_per_min, recorded_at), deployments(service_name, version,
    commit_sha, status, deployed_by, deployed_at).
    """
    return _service.run_readonly_sql(sql)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run()
