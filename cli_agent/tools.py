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
