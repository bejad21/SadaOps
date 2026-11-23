"""SQL AST security tests for ops_db_mcp (15+ injection/mutation cases)."""

from __future__ import annotations

import pytest

from mcp_servers.ops_db_mcp.sql_guard import SQLValidationError, validate_and_rewrite_sql


@pytest.mark.parametrize(
    "valid_sql",
    [
        "SELECT incident_code, severity FROM ops_incidents",
        "SELECT service_name, p95_latency_ms FROM service_metrics WHERE error_rate_pct > 1.0",
        (
            "SELECT d.version, i.incident_code FROM deployments d "
            "JOIN ops_incidents i ON d.service_name = i.service_name LIMIT 10"
        ),
    ],
)
def test_valid_select_queries_pass_and_enforce_limit(valid_sql: str) -> None:
    out = validate_and_rewrite_sql(valid_sql)
    assert "SELECT" in out.upper()
    assert "LIMIT" in out.upper()


def test_excessive_limit_is_capped_to_50() -> None:
    out = validate_and_rewrite_sql("SELECT * FROM ops_incidents LIMIT 5000")
    assert "LIMIT 50" in out.upper()


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "",
        "DROP TABLE ops_incidents",
        "DELETE FROM ops_incidents WHERE id = 1",
        "INSERT INTO ops_incidents (incident_code) VALUES ('X')",
        "UPDATE ops_incidents SET status = 'resolved'",
        "ALTER TABLE ops_incidents ADD COLUMN pwn TEXT",
        "SELECT * FROM ops_incidents; DROP TABLE ops_incidents;",
        "SELECT * FROM calendar_events",
        "SELECT * FROM pending_actions",
        "SELECT * FROM pg_shadow",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT pg_sleep(10) FROM ops_incidents",
        "SELECT pg_read_file('/etc/passwd') FROM ops_incidents",
        (
            "SELECT * FROM ops_incidents UNION "
            "SELECT usename, passwd, '', '', '', '', '', NOW(), NOW() FROM pg_shadow"
        ),
        "WITH evil AS (DELETE FROM ops_incidents RETURNING *) SELECT * FROM evil",
        "COPY ops_incidents TO '/tmp/leak.csv'",
        "SELECT query_to_xml_and_xmlschema('SELECT * FROM pg_catalog.pg_roles', true, false, '')",
        "SELECT table_to_xml('pg_catalog.pg_roles', true, false, '')",
        "SELECT repeat('A', 100000000) FROM ops_incidents",
        "SELECT set_config('statement_timeout', '0', false) FROM ops_incidents",
        "SELECT version()",
        "SELECT COUNT(*) FROM (SELECT 1) t",
        "SELECT * FROM ops_incidents OFFSET -5",
        "SELECT " + ("(" * 250) + "1" + (")" * 250) + " FROM ops_incidents",
        "SELECT * FROM ops_incidents WHERE title = '" + ("a" * 5000) + "'",
    ],
)
def test_sql_guard_blocks_all_injection_and_unauthorized_tables(malicious_sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_and_rewrite_sql(malicious_sql)


def test_sql_guard_allows_safe_cte_over_ops_table() -> None:
    rewritten = validate_and_rewrite_sql(
        "WITH open_inc AS (SELECT incident_code FROM ops_incidents WHERE status = 'open') "
        "SELECT COUNT(*) FROM open_inc"
    )
    assert "OPEN_INC" in rewritten.upper()
    assert "LIMIT 50" in rewritten.upper()
