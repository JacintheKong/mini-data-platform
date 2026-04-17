"""Read-only tools exposed to the Claude agent."""
import json
from pathlib import Path
from typing import Any

import duckdb

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
