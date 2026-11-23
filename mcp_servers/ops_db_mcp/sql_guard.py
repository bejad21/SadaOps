"""Strict sqlglot AST validator for read-only Ops DB queries."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES: frozenset[str] = frozenset(
    {"ops_incidents", "service_metrics", "deployments"}
)
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "abs",
        "avg",
        "btrim",
        "cast",
        "ceil",
        "ceiling",
        "char_length",
        "coalesce",
        "concat",
        "count",
        "date_trunc",
        "extract",
        "floor",
        "greatest",
        "least",
        "length",
        "lower",
        "ltrim",
        "max",
        "min",
        "now",
        "nullif",
        "round",
        "rtrim",
        "substr",
        "substring",
        "sum",
        "to_char",
        "trim",
        "upper",
    }
)
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Merge,
    exp.Into,
    exp.Lock,
)
MAX_ROW_LIMIT = 50
MAX_SQL_LENGTH = 4000


class SQLValidationError(ValueError):
    """Raised when a SQL query violates the read-only AST policy."""


def validate_and_rewrite_sql(sql: str, max_limit: int = MAX_ROW_LIMIT) -> str:
    """Validate that `sql` is a single, safe SELECT query against allowed Ops tables.

    Enforces a row cap <= `max_limit` and returns normalized Postgres SQL.
    """
    cleaned = (sql or "").strip()
    if not cleaned:
        raise SQLValidationError("SQL query must not be empty.")
    if len(cleaned) > MAX_SQL_LENGTH:
        raise SQLValidationError(
            f"SQL query exceeds maximum length of {MAX_SQL_LENGTH} characters."
        )

    try:
        statements = sqlglot.parse(cleaned, read="postgres")
    except (sqlglot.errors.ParseError, RecursionError) as exc:
        raise SQLValidationError(f"Invalid SQL syntax: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")

    root = statements[0]
    if not isinstance(root, exp.Select):
        raise SQLValidationError(f"Only SELECT statements are allowed, got {type(root).__name__}.")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in root.find_all(exp.CTE)
        if getattr(cte, "alias_or_name", None)
    }
    base_tables_seen: set[str] = set()

    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden SQL construct: {type(node).__name__}.")

        if isinstance(node, exp.Table):
            table_name = (node.name or "").lower()
            db_name = (node.db or "").lower()
            if db_name and db_name != "public":
                raise SQLValidationError(f"Schema '{db_name}' is not allowed.")
            if table_name in ALLOWED_TABLES:
                base_tables_seen.add(table_name)
            elif table_name in cte_names and not db_name:
                continue
            else:
                raise SQLValidationError(
                    f"Table '{table_name}' is not in allowed Ops tables: {sorted(ALLOWED_TABLES)}"
                )

        if isinstance(node, exp.Offset):
            offset_expr = node.expression
            if not (
                isinstance(offset_expr, exp.Literal)
                and offset_expr.is_int
                and int(offset_expr.this) >= 0
            ):
                raise SQLValidationError("OFFSET must be a non-negative integer literal.")

        if isinstance(node, (exp.Anonymous, exp.Func)):
            raw_name = getattr(node, "name", None) or getattr(node, "this", None)
            if isinstance(node, exp.Anonymous):
                func_name = str(raw_name or "").lower()
            else:
                func_name = (
                    node.sql_name().lower()
                    if hasattr(node, "sql_name") and node.sql_name()
                    else str(raw_name or "").lower()
                )
            if func_name not in ALLOWED_FUNCTIONS:
                raise SQLValidationError(f"SQL function '{func_name}' is not in allowlist.")

    if not base_tables_seen:
        raise SQLValidationError(
            f"Query must reference at least one allowed Ops table: {sorted(ALLOWED_TABLES)}"
        )

    existing_limit = root.args.get("limit")
    if existing_limit is None:
        root = root.limit(max_limit)
    else:
        limit_expr = existing_limit.expression
        if isinstance(limit_expr, exp.Literal) and limit_expr.is_int:
            val = int(limit_expr.this)
            if val <= 0 or val > max_limit:
                root = root.limit(max_limit)
        else:
            root = root.limit(max_limit)

    return root.sql(dialect="postgres")
