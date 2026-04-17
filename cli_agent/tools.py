"""Read-only tools exposed to the Claude agent."""
import json
import threading
from pathlib import Path
from typing import Any

import duckdb

from cli_agent import config

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "main"}


class Tools:
    def __init__(self, conn: duckdb.DuckDBPyConnection, manifest_path: Path | None = None):
        self.conn = conn
        self._manifest = self._load_manifest(manifest_path)

    @staticmethod
    def _load_manifest(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def list_tables(self) -> str:
        rows = self.conn.execute(
            "SELECT table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name"
        ).fetchall()
        if not rows:
            return "(no tables found)"
        lines = ["| schema | table |", "|---|---|"]
        lines.extend(f"| {s} | {t} |" for s, t in rows)
        return "\n".join(lines)

    def describe_table(self, name: str) -> str:
        """Columns + types; merges dbt model description/column docs if available."""
        if "." in name:
            schema, table = name.split(".", 1)
        else:
            schema, table = "main", name
        cols = self.conn.execute(
            "SELECT column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [schema, table],
        ).fetchall()
        if not cols:
            return f"Table {name} not found."

        lines = [f"# {name}"]
        dbt_node = self._find_dbt_node(table)
        if dbt_node and dbt_node.get("description"):
            lines.append(f"\n{dbt_node['description']}\n")

        lines.append("| column | type | description |")
        lines.append("|---|---|---|")
        col_docs = (dbt_node or {}).get("columns", {}) if dbt_node else {}
        for col, dtype in cols:
            desc = col_docs.get(col, {}).get("description", "")
            lines.append(f"| {col} | {dtype} | {desc} |")
        return "\n".join(lines)

    def _find_dbt_node(self, table: str) -> dict | None:
        for node in self._manifest.get("nodes", {}).values():
            if node.get("name") == table:
                return node
        return None

    def sample_rows(self, table: str, n: int = 5) -> str:
        n = max(1, min(n, 20))  # hard cap
        # Validate table exists to avoid cryptic SQL error
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "main", table
        exists = self.conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, tbl],
        ).fetchone()
        if not exists:
            return f"Table {table} not found."
        result = self.conn.execute(f"SELECT * FROM {table} LIMIT {n}")
        rows = result.fetchall()
        cols = [d[0] for d in result.description]
        return _markdown_table(cols, rows)

    def run_sql(self, query: str) -> str:
        max_rows = config.MAX_ROWS
        timeout = config.QUERY_TIMEOUT_SECONDS
        timer = threading.Timer(timeout, self.conn.interrupt)
        timer.start()
        try:
            result = self.conn.execute(query)
            rows = result.fetchmany(max_rows + 1)
            cols = [d[0] for d in result.description] if result.description else []
        except Exception as e:
            return f"SQL error: {e}"
        finally:
            timer.cancel()
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        table = _markdown_table(cols, rows)
        summary = f"\n\n({len(rows)} row{'s' if len(rows) != 1 else ''}"
        if truncated:
            summary += f", truncated at MAX_ROWS={max_rows}"
        summary += ")"
        return table + summary


def _markdown_table(cols: list[str], rows: list[tuple]) -> str:
    if not rows:
        return f"(no rows)\nColumns: {', '.join(cols)}"
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _fmt(v: Any) -> str:
    if v is None:
        return "NULL"
    return str(v)
