# CLI Data Agent Implementation Plan

**Goal:** Build a CLI agent that answers ad-hoc natural-language questions about the mini-data-platform warehouse by introspecting DuckDB schema + dbt metadata and generating read-only SQL.

**Architecture:** Single tool-using Claude Sonnet 4.5 agent with 4 tools (`list_tables`, `describe_table`, `sample_rows`, `run_sql`). Read-only DuckDB connection is the sole safety boundary. Session-start bootstrap query anchors relative-date questions to the data's actual end (2024-12-31), not wall-clock. Single-shot + minimal REPL mode.

**Tech Stack:** Python 3.13, `anthropic` SDK, `duckdb`, `pytest`. No `sqlglot`, no `rich` (plain ASCII tables).

**Source spec:** [`docs/design/2026-04-17-cli-data-agent-design.md`](../design/2026-04-17-cli-data-agent-design.md)

---

## Post-implementation notes

This plan is the snapshot I worked from. Two things shipped slightly different from what's written below — documenting here instead of rewriting the tasks:

- **Task 15 (README):** plan creates a separate `README-agent.md`; ended up consolidating into the root `README.md` so a new reviewer has a single entry point.
- **Rendering default:** plan has `render` show prose + SQL + result table by default, with `--verbose` adding the full trace. After live-testing, the default was narrowed to prose-only, and `--verbose` now gates SQL + table + trace together (cleaner for non-technical users; a REPL leak in the SQL extractor was fixed at the same time).

---

## File Structure

**Created files:**

| Path | Responsibility |
|---|---|
| `cli_agent/__init__.py` | Package marker (empty) |
| `cli_agent/__main__.py` | `python -m cli_agent` entry: calls `cli.main()` |
| `cli_agent/cli.py` | argparse, single-shot vs REPL dispatch |
| `cli_agent/agent.py` | `answer()` agent loop, `AgentResult`, `AgentSession` |
| `cli_agent/tools.py` | `Tools` class: 4 tool methods + `dispatch` + `schemas()` |
| `cli_agent/rendering.py` | `render(result, verbose)` — prose + SQL + table, optional trace |
| `cli_agent/prompts.py` | `SYSTEM_PROMPT` template, `build_system_prompt()`, `bootstrap()` |
| `cli_agent/config.py` | Env var parsing (API key, warehouse path, limits) |
| `cli_agent/examples.sh` | Manual smoke script: 5 canned questions |
| `tests/__init__.py` | Package marker (if missing) |
| `tests/cli_agent/__init__.py` | Package marker |
| `tests/cli_agent/conftest.py` | Pytest fixtures: in-memory DuckDB with 3 tables + stub manifest |
| `tests/cli_agent/test_tools.py` | 9 TDD tests driving `tools.py` |
| `tests/cli_agent/test_agent.py` | One smoke test: mocked Anthropic, verify tool dispatch round-trip |
| `README-agent.md` | Deliverable: pitch, quick start, design, examples, next steps, limitations |

**Modified files:**

| Path | Change |
|---|---|
| `pyproject.toml` | Add `anthropic`, `pytest` to dependencies |

---

## Task 1: Scaffold package and dependencies

**Files:**
- Create: `cli_agent/__init__.py`
- Create: `cli_agent/__main__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies via uv**

Run: `cd /Users/yejunkong/mini-data-platform && uv add anthropic pytest`

Expected: `pyproject.toml` gains `anthropic` and `pytest` entries; `uv.lock` regenerated.

- [ ] **Step 2: Create package skeleton**

Create `cli_agent/__init__.py`:

```python
"""CLI data agent — answers ad-hoc questions about the warehouse."""
```

Create `cli_agent/__main__.py`:

```python
from cli_agent.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify import works**

Run: `cd /Users/yejunkong/mini-data-platform && uv run python -c "import cli_agent; print('ok')"`

Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add cli_agent/ pyproject.toml uv.lock
git commit -m "scaffold cli_agent package with anthropic + pytest deps"
```

---

## Task 2: Config module

**Files:**
- Create: `cli_agent/config.py`

- [ ] **Step 1: Write config module**

Create `cli_agent/config.py`:

```python
"""Runtime configuration from environment variables."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("CLI_AGENT_MODEL", "claude-sonnet-4-5")
WAREHOUSE_PATH = Path(
    os.environ.get("CLI_AGENT_WAREHOUSE", REPO_ROOT / "warehouse" / "data.duckdb")
).resolve()
DBT_MANIFEST_PATH = REPO_ROOT / "dbt_project" / "target" / "manifest.json"

MAX_ROWS = int(os.environ.get("CLI_AGENT_MAX_ROWS", "1000"))
MAX_ITERATIONS = int(os.environ.get("CLI_AGENT_MAX_ITERATIONS", "10"))
QUERY_TIMEOUT_SECONDS = int(os.environ.get("CLI_AGENT_QUERY_TIMEOUT", "30"))


def require_api_key() -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it before running: "
            "export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return ANTHROPIC_API_KEY
```

- [ ] **Step 2: Verify module imports**

Run: `uv run python -c "from cli_agent import config; print(config.MODEL, config.WAREHOUSE_PATH)"`

Expected: prints `claude-sonnet-4-5 /Users/yejunkong/mini-data-platform/warehouse/data.duckdb`.

- [ ] **Step 3: Commit**

```bash
git add cli_agent/config.py
git commit -m "add config module with env-var overrides"
```

---

## Task 3: Test fixtures (conftest)

**Files:**
- Create: `tests/__init__.py` (if missing)
- Create: `tests/cli_agent/__init__.py`
- Create: `tests/cli_agent/conftest.py`

- [ ] **Step 1: Create package markers**

Create empty `tests/__init__.py` (skip if already exists) and empty `tests/cli_agent/__init__.py`.

- [ ] **Step 2: Write conftest with DuckDB + manifest fixtures**

Create `tests/cli_agent/conftest.py`:

```python
"""Shared fixtures for cli_agent tests."""
import json
import tempfile
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def duckdb_conn():
    """In-memory DuckDB with a tiny marts-like schema and ~15 rows."""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SCHEMA marts")
    conn.execute("""
        CREATE TABLE marts.fct_orders (
            transaction_id INTEGER,
            transaction_date DATE,
            status VARCHAR,
            subtotal DECIMAL,
            total DECIMAL,
            customer_segment VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO marts.fct_orders VALUES
            (1, '2024-10-01', 'completed', 100.0, 108.0, 'high_value'),
            (1, '2024-10-01', 'completed', 100.0, 108.0, 'high_value'),
            (2, '2024-11-15', 'completed', 50.0, 54.0, 'medium_value'),
            (3, '2024-12-20', 'cancelled', 75.0, 81.0, 'low_value'),
            (4, '2024-12-31', 'completed', 200.0, 216.0, 'high_value')
    """)
    conn.execute("""
        CREATE TABLE marts.dim_products (
            product_id INTEGER, product_name VARCHAR, category VARCHAR
        )
    """)
    conn.execute(
        "INSERT INTO marts.dim_products VALUES "
        "(1, 'Widget A', 'electronics'), (2, 'Gadget B', 'home'), (3, 'Thing C', 'home')"
    )
    conn.execute("CREATE TABLE marts.dim_customers (user_id INTEGER, email VARCHAR)")
    conn.execute(
        "INSERT INTO marts.dim_customers VALUES "
        "(1, 'a@x.com'), (2, 'b@x.com'), (3, 'c@x.com')"
    )
    yield conn
    conn.close()


@pytest.fixture
def manifest_path(tmp_path):
    """Minimal dbt manifest.json stub for describe_table tests."""
    manifest = {
        "nodes": {
            "model.mini_data_platform.fct_orders": {
                "name": "fct_orders",
                "description": "Denormalized order line items with dimensions joined in.",
                "columns": {
                    "transaction_id": {"description": "Order id. NOTE: not unique — line items share it."},
                    "status": {"description": "One of completed|pending|cancelled|refunded."},
                },
            }
        }
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path
```

- [ ] **Step 3: Verify fixture loads**

Run: `uv run pytest tests/cli_agent/ --collect-only -q`

Expected: `no tests ran` (no test files yet) but no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "add pytest fixtures: in-memory DuckDB + stub dbt manifest"
```

---

## Task 4: `list_tables` tool (TDD)

**Files:**
- Create: `cli_agent/tools.py`
- Create: `tests/cli_agent/test_tools.py`

- [ ] **Step 1: Write failing test**

Create `tests/cli_agent/test_tools.py`:

```python
"""Unit tests for cli_agent.tools — TDD."""
import pytest

from cli_agent.tools import Tools


def test_list_tables_returns_marts_tables(duckdb_conn):
    tools = Tools(duckdb_conn)
    out = tools.list_tables()
    assert "fct_orders" in out
    assert "dim_products" in out
    assert "dim_customers" in out
    assert "marts" in out
    # Excludes system schemas
    assert "information_schema" not in out
    assert "pg_catalog" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli_agent/test_tools.py::test_list_tables_returns_marts_tables -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'cli_agent.tools'`.

- [ ] **Step 3: Implement `Tools` class with `list_tables`**

Create `cli_agent/tools.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli_agent/test_tools.py::test_list_tables_returns_marts_tables -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli_agent/tools.py tests/cli_agent/test_tools.py
git commit -m "tools: add list_tables (TDD)"
```

---

## Task 5: `describe_table` tool (TDD)

**Files:**
- Modify: `cli_agent/tools.py`
- Modify: `tests/cli_agent/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/cli_agent/test_tools.py`:

```python
def test_describe_table_returns_columns_and_types(duckdb_conn):
    tools = Tools(duckdb_conn)
    out = tools.describe_table("marts.fct_orders")
    assert "transaction_id" in out
    assert "INTEGER" in out
    assert "status" in out
    assert "VARCHAR" in out


def test_describe_table_includes_dbt_docs_when_available(duckdb_conn, manifest_path):
    tools = Tools(duckdb_conn, manifest_path=manifest_path)
    out = tools.describe_table("marts.fct_orders")
    assert "Denormalized order line items" in out  # from manifest description
    assert "not unique" in out  # from manifest column description


def test_describe_table_handles_missing_table(duckdb_conn):
    tools = Tools(duckdb_conn)
    out = tools.describe_table("marts.does_not_exist")
    assert "not found" in out.lower() or "no columns" in out.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/cli_agent/test_tools.py -k describe_table -v`

Expected: 3 FAILs (AttributeError: no `describe_table`).

- [ ] **Step 3: Implement `describe_table`**

Add to `cli_agent/tools.py` inside `Tools`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/cli_agent/test_tools.py -k describe_table -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add cli_agent/tools.py tests/cli_agent/test_tools.py
git commit -m "tools: add describe_table with dbt manifest merge (TDD)"
```

---

## Task 6: `sample_rows` tool (TDD)

**Files:**
- Modify: `cli_agent/tools.py`
- Modify: `tests/cli_agent/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/cli_agent/test_tools.py`:

```python
def test_sample_rows_returns_requested_number(duckdb_conn):
    tools = Tools(duckdb_conn)
    out = tools.sample_rows("marts.dim_products", n=2)
    # Header (1) + separator (1) + 2 data rows
    assert out.count("\n") >= 3
    assert "Widget A" in out or "Gadget B" in out or "Thing C" in out


def test_sample_rows_caps_n_at_20(duckdb_conn):
    tools = Tools(duckdb_conn)
    # Fixture has only 5 rows in fct_orders, but request 9999 — should cap to 20
    out = tools.sample_rows("marts.fct_orders", n=9999)
    # Count data rows (lines after the markdown separator)
    data_lines = [l for l in out.splitlines() if l.startswith("|") and "---" not in l]
    assert len(data_lines) - 1 <= 20  # -1 for header
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli_agent/test_tools.py -k sample_rows -v`

Expected: 2 FAILs (AttributeError).

- [ ] **Step 3: Implement `sample_rows`**

Add to `cli_agent/tools.py` inside `Tools`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/cli_agent/test_tools.py -k sample_rows -v`

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add cli_agent/tools.py tests/cli_agent/test_tools.py
git commit -m "tools: add sample_rows with 20-row hard cap (TDD)"
```

---

## Task 7: `run_sql` tool — basic execution and read-only enforcement (TDD)

**Files:**
- Modify: `cli_agent/tools.py`
- Modify: `tests/cli_agent/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/cli_agent/test_tools.py`:

```python
def test_run_sql_executes_select(duckdb_conn):
    tools = Tools(duckdb_conn)
    out = tools.run_sql("SELECT COUNT(*) AS n FROM marts.fct_orders")
    assert "5" in out  # 5 rows in fixture
    assert "n" in out  # column name in header


def test_run_sql_rejects_writes_via_readonly(tmp_path):
    # Build a persistent DB so we can reopen read-only
    db_path = tmp_path / "ro.duckdb"
    setup = duckdb.connect(str(db_path))
    setup.execute("CREATE TABLE t (x INTEGER)")
    setup.execute("INSERT INTO t VALUES (1)")
    setup.close()

    conn_ro = duckdb.connect(str(db_path), read_only=True)
    tools = Tools(conn_ro)
    out = tools.run_sql("DROP TABLE t")
    assert "error" in out.lower() or "read-only" in out.lower() or "cannot" in out.lower()
    # Verify table is still there via a fresh read-write connection
    check = duckdb.connect(str(db_path))
    assert check.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    check.close()
```

Note: this test imports `duckdb` at the top of the file — ensure `import duckdb` is at module top (add if missing).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli_agent/test_tools.py -k run_sql -v`

Expected: 2 FAILs (AttributeError: no `run_sql`).

- [ ] **Step 3: Implement `run_sql`**

Add imports at top of `cli_agent/tools.py`:

```python
import threading
from cli_agent import config
```

Add method to `Tools`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/cli_agent/test_tools.py -k run_sql -v`

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add cli_agent/tools.py tests/cli_agent/test_tools.py
git commit -m "tools: add run_sql with read-only enforcement (TDD)"
```

---

## Task 8: `run_sql` row cap and timeout (TDD)

**Files:**
- Modify: `tests/cli_agent/test_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/cli_agent/test_tools.py`:

```python
def test_run_sql_truncates_at_max_rows(duckdb_conn, monkeypatch):
    from cli_agent import config
    monkeypatch.setattr(config, "MAX_ROWS", 2)
    tools = Tools(duckdb_conn)
    out = tools.run_sql("SELECT * FROM marts.fct_orders")  # 5 rows
    assert "truncated" in out.lower()
    assert "MAX_ROWS=2" in out


def test_run_sql_times_out(duckdb_conn, monkeypatch):
    from cli_agent import config
    monkeypatch.setattr(config, "QUERY_TIMEOUT_SECONDS", 1)
    tools = Tools(duckdb_conn)
    # A query that spins long enough to be interrupted
    out = tools.run_sql(
        "SELECT COUNT(*) FROM generate_series(1, 100000000) t(x) "
        "CROSS JOIN generate_series(1, 100) u(y)"
    )
    assert "error" in out.lower() or "interrupt" in out.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli_agent/test_tools.py -k "truncates or times_out" -v`

Expected for `truncates`: should PASS already (impl supports it) — if so, remove/keep the test. Expected for `times_out`: PASS if interrupt works; otherwise FAIL gives data to debug.

If either actually passes on first run, that's fine — keep the test as regression coverage.

- [ ] **Step 3: No new implementation needed** if both already pass. If `times_out` fails, inspect: DuckDB's `interrupt()` may need a connection in a different state. Adjust by ensuring `timer.start()` happens before `execute`. (Already the case.)

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/cli_agent/ -v`

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/cli_agent/test_tools.py
git commit -m "tools: add row-cap and timeout regression tests (TDD)"
```

---

## Task 9: Tool schemas + dispatcher

**Files:**
- Modify: `cli_agent/tools.py`

- [ ] **Step 1: Add tool schemas and dispatch method**

Add to `cli_agent/tools.py` inside `Tools`:

```python
    @staticmethod
    def schemas() -> list[dict]:
        """Anthropic tool schemas."""
        return [
            {
                "name": "list_tables",
                "description": "List all tables across schemas (raw, staging, marts). Call this first when exploring.",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "describe_table",
                "description": "Get columns, types, and dbt documentation for a table. Prefer schema-qualified names like 'marts.fct_orders'.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "sample_rows",
                "description": "Return up to 20 rows from a table to inspect actual values (e.g., enum-like columns).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "n": {"type": "integer", "default": 5},
                    },
                    "required": ["table"],
                },
            },
            {
                "name": "run_sql",
                "description": "Execute a read-only SELECT query. Writes are blocked at the DB level. Results capped at MAX_ROWS.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ]

    def dispatch(self, name: str, args: dict) -> str:
        """Route a tool_use block to the right method. Returns the tool_result string."""
        if name == "list_tables":
            return self.list_tables()
        if name == "describe_table":
            return self.describe_table(args["name"])
        if name == "sample_rows":
            return self.sample_rows(args["table"], args.get("n", 5))
        if name == "run_sql":
            return self.run_sql(args["query"])
        return f"Unknown tool: {name}"
```

- [ ] **Step 2: Verify schemas load**

Run: `uv run python -c "from cli_agent.tools import Tools; import json; print(json.dumps(Tools.schemas(), indent=2)[:200])"`

Expected: first 200 chars of the schema JSON.

- [ ] **Step 3: Run full test suite to confirm no regressions**

Run: `uv run pytest tests/cli_agent/ -v`

Expected: all 9 tests still PASS.

- [ ] **Step 4: Commit**

```bash
git add cli_agent/tools.py
git commit -m "tools: add Anthropic tool schemas and dispatcher"
```

---

## Task 10: Prompts module (system prompt + bootstrap)

**Files:**
- Create: `cli_agent/prompts.py`

- [ ] **Step 1: Write prompts module**

Create `cli_agent/prompts.py`:

```python
"""System prompt template and session-start bootstrap."""
from datetime import date

import duckdb

SYSTEM_PROMPT_TEMPLATE = """You are a data analyst with read-only access to a DuckDB warehouse.

## Warehouse
Three schemas: `raw`, `staging`, `marts`. Prefer `marts` — it is the analytics-ready layer.

Key marts tables:
- `marts.fct_orders` — one row per order line item; dimensions joined in via LEFT JOIN.
- `marts.dim_customers` — one row per customer.
- `marts.dim_products` — one row per product; includes precomputed `margin`.

## Critical data quirks (the hardest things to get right)

1. **`fct_orders` is one row per line item, but `subtotal`/`tax`/`shipping`/`discount`/`total` repeat identically on every line of the same order.** Naive `SUM(subtotal)` inflates revenue by avg line count. Dedupe via `DISTINCT transaction_id` or aggregate by `transaction_id` first.

2. **Order count:** use `COUNT(DISTINCT transaction_id)`, not `COUNT(*)`.

3. **Status filter:** the column is named `status` with values `completed`, `pending`, `cancelled`, `refunded`. Revenue questions should default to `WHERE status = 'completed'` and state this. Cancellations/refunds are ~10% of orders.

4. **Data errors present:** ~5% null `payment_method`, ~2% negative `total`, ~3% duplicate `transaction_id`. Exclude negative totals from revenue; flag them for data-quality questions.

5. **Revenue is ambiguous:** `subtotal` (gross), `subtotal - discount` (net), or `total` (customer charge). Default to `subtotal` and state the choice.

6. **`line_margin` is miscomputed** — `total - cost*qty` uses the *order* total, not the line's revenue. Do not trust it. Compute `(unit_price * quantity) - (product_cost * quantity)` instead.

7. **LEFT JOINs in `fct_orders`** mean dim fields may be NULL (unattributed orders/users). Filters like `WHERE customer_segment = 'VIP'` silently drop NULL rows.

## Tool use

- Call `list_tables` first if you don't know the schema.
- `describe_table('marts.fct_orders')` returns columns + dbt docs when available.
- Use `sample_rows` to check enum-like values (e.g., `status`, `category`).
- `run_sql` is read-only — writes fail at the DB level. Always schema-qualify table names.

## Output contract

Respond with a concise prose summary of the answer. State which revenue definition you used if applicable. Do not repeat the SQL in your prose — the renderer surfaces it separately.

{bootstrap_facts}
"""


def bootstrap(conn: duckdb.DuckDBPyConnection) -> str:
    """Query live warehouse facts; inject into system prompt once per session."""
    try:
        min_d, max_d = conn.execute(
            "SELECT MIN(transaction_date), MAX(transaction_date) FROM marts.fct_orders"
        ).fetchone()
    except Exception:
        min_d, max_d = None, None
    try:
        counts = dict(conn.execute(
            "SELECT 'fct_orders', COUNT(*) FROM marts.fct_orders UNION ALL "
            "SELECT 'dim_customers', COUNT(*) FROM marts.dim_customers UNION ALL "
            "SELECT 'dim_products',  COUNT(*) FROM marts.dim_products"
        ).fetchall())
    except Exception:
        counts = {}

    today = date.today().isoformat()
    parts = [f"## Live warehouse facts (fetched at session start)", f"- Today's wall-clock date: {today}"]
    if min_d and max_d:
        parts.append(
            f"- Data spans {min_d} to {max_d}. "
            f"Prefer MAX(transaction_date) as 'now' for relative-date queries — "
            f"wall-clock may be past the data's end."
        )
    if counts:
        parts.append(f"- Mart row counts: {counts}")
    return "\n".join(parts)


def build_system_prompt(bootstrap_facts: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(bootstrap_facts=bootstrap_facts)
```

- [ ] **Step 2: Smoke-test bootstrap against fixture**

Run:

```bash
uv run python -c "
import duckdb
from cli_agent.prompts import bootstrap, build_system_prompt
conn = duckdb.connect(':memory:')
conn.execute('CREATE SCHEMA marts')
conn.execute('CREATE TABLE marts.fct_orders (transaction_date DATE)')
conn.execute('CREATE TABLE marts.dim_customers (x INT)')
conn.execute('CREATE TABLE marts.dim_products (x INT)')
conn.execute(\"INSERT INTO marts.fct_orders VALUES ('2024-01-01'), ('2024-12-31')\")
print(build_system_prompt(bootstrap(conn))[:500])
"
```

Expected: prints a system prompt ending with "Data spans 2024-01-01 to 2024-12-31."

- [ ] **Step 3: Commit**

```bash
git add cli_agent/prompts.py
git commit -m "add system prompt + bootstrap for session-start facts"
```

---

## Task 11: Agent loop

**Files:**
- Create: `cli_agent/agent.py`
- Create: `tests/cli_agent/test_agent.py`

- [ ] **Step 1: Write smoke test (mocked Anthropic)**

Create `tests/cli_agent/test_agent.py`:

```python
"""Smoke test: agent tool-use round-trip with mocked Anthropic client."""
from unittest.mock import MagicMock

from cli_agent.agent import AgentSession
from cli_agent.tools import Tools


class FakeBlock:
    """Minimal shape matching Anthropic SDK content blocks."""
    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_response(stop_reason, content):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    return resp


def test_agent_dispatches_tool_use_and_returns_final_text(duckdb_conn):
    client = MagicMock()
    # Turn 1: agent requests list_tables
    turn1 = make_response(
        stop_reason="tool_use",
        content=[FakeBlock(type="tool_use", id="t1", name="list_tables", input={})],
    )
    # Turn 2: agent emits final text
    turn2 = make_response(
        stop_reason="end_turn",
        content=[FakeBlock(type="text", text="There are 3 tables in marts.")],
    )
    client.messages.create.side_effect = [turn1, turn2]

    session = AgentSession(
        client=client,
        system_prompt="test prompt",
        tools=Tools(duckdb_conn),
        model="claude-sonnet-4-5",
    )
    result = session.answer("what tables exist?")
    assert "3 tables" in result.text
    assert len(result.trace) == 2
    # Second call should include the tool_result
    second_call = client.messages.create.call_args_list[1].kwargs
    last_msg = second_call["messages"][-1]
    assert last_msg["role"] == "user"
    assert any(
        block.get("type") == "tool_result" for block in last_msg["content"]
    )


def test_agent_stops_after_max_iterations(duckdb_conn):
    client = MagicMock()
    # Always return tool_use — never ends
    loop_resp = make_response(
        stop_reason="tool_use",
        content=[FakeBlock(type="tool_use", id="t", name="list_tables", input={})],
    )
    client.messages.create.return_value = loop_resp

    session = AgentSession(
        client=client,
        system_prompt="test",
        tools=Tools(duckdb_conn),
        model="claude-sonnet-4-5",
        max_iterations=3,
    )
    result = session.answer("loop forever")
    assert "did not converge" in result.text.lower()
    assert len(result.trace) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli_agent/test_agent.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'cli_agent.agent'`.

- [ ] **Step 3: Implement `AgentSession`**

Create `cli_agent/agent.py`:

```python
"""Claude agent loop with tool use."""
from dataclasses import dataclass, field
from typing import Any

from cli_agent import config
from cli_agent.tools import Tools


@dataclass
class AgentResult:
    text: str
    trace: list[Any] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)


class AgentSession:
    def __init__(
        self,
        client,
        system_prompt: str,
        tools: Tools,
        model: str = config.MODEL,
        max_iterations: int = config.MAX_ITERATIONS,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.tools = tools
        self.model = model
        self.max_iterations = max_iterations
        self.history: list[dict] = []

    def answer(self, question: str) -> AgentResult:
        messages = self.history + [{"role": "user", "content": question}]
        trace = []
        for _ in range(self.max_iterations):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                tools=Tools.schemas(),
                messages=messages,
            )
            trace.append(resp)
            if resp.stop_reason == "end_turn":
                text = _final_text(resp)
                # Append assistant turn to history for follow-ups
                messages.append({"role": "assistant", "content": _serialize_content(resp.content)})
                self.history = messages
                return AgentResult(text=text, trace=trace, messages=messages)
            # Dispatch tool_use blocks
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    output = self.tools.dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    })
            messages.append({"role": "assistant", "content": _serialize_content(resp.content)})
            messages.append({"role": "user", "content": tool_results})
        return AgentResult(
            text="(agent did not converge in max_iterations turns)",
            trace=trace,
            messages=messages,
        )


def _final_text(resp) -> str:
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip() or "(no text response)"


def _serialize_content(content) -> list[dict]:
    """Convert Anthropic content blocks into dicts for the next messages.create call."""
    out = []
    for b in content:
        if getattr(b, "type", None) == "text":
            out.append({"type": "text", "text": b.text})
        elif getattr(b, "type", None) == "tool_use":
            out.append({
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": b.input,
            })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli_agent/test_agent.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/cli_agent/ -v`

Expected: 11 PASS (9 tool tests + 2 agent tests).

- [ ] **Step 6: Commit**

```bash
git add cli_agent/agent.py tests/cli_agent/test_agent.py
git commit -m "agent: add AgentSession with tool-use loop and iteration cap (TDD)"
```

---

## Task 12: Rendering module

**Files:**
- Create: `cli_agent/rendering.py`

- [ ] **Step 1: Write rendering module**

Create `cli_agent/rendering.py`:

```python
"""Format AgentResult for terminal output."""
from cli_agent.agent import AgentResult


def render(result: AgentResult, verbose: bool = False) -> str:
    lines = [result.text, ""]

    sql_calls = _extract_sql_calls(result)
    if sql_calls:
        lines.append("---")
        for i, (query, output) in enumerate(sql_calls, 1):
            label = f"SQL" if len(sql_calls) == 1 else f"SQL #{i}"
            lines.append(f"\n{label}:")
            lines.append(_indent(query.strip(), "  "))
            lines.append("\nResult:")
            lines.append(_indent(output.strip(), "  "))

    if verbose:
        lines.append("\n---\nFull trace:")
        for i, resp in enumerate(result.trace, 1):
            lines.append(f"\n[turn {i}] stop_reason={resp.stop_reason}")
            for block in resp.content:
                btype = getattr(block, "type", "?")
                if btype == "text":
                    lines.append(f"  text: {block.text[:200]}")
                elif btype == "tool_use":
                    lines.append(f"  tool_use: {block.name}({block.input})")

    return "\n".join(lines)


def _extract_sql_calls(result: AgentResult) -> list[tuple[str, str]]:
    """Walk the message history, pair run_sql tool_use with its tool_result."""
    pairs = []
    pending: dict[str, str] = {}
    for msg in result.messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "tool_use" and block.get("name") == "run_sql":
                pending[block["id"]] = block["input"].get("query", "")
            elif btype == "tool_result":
                tid = block.get("tool_use_id")
                if tid in pending:
                    pairs.append((pending.pop(tid), str(block.get("content", ""))))
    return pairs


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
```

- [ ] **Step 2: Smoke-test rendering shape**

Run:

```bash
uv run python -c "
from cli_agent.agent import AgentResult
from cli_agent.rendering import render
r = AgentResult(text='Q4 revenue was 500.', trace=[], messages=[
    {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': 'x', 'name': 'run_sql', 'input': {'query': 'SELECT SUM(subtotal) FROM marts.fct_orders'}}
    ]},
    {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'x', 'content': '| sum |\n|---|\n| 500 |'}
    ]},
])
print(render(r))
"
```

Expected: output shows the answer, `SQL:` section with the query, and `Result:` section with the markdown table.

- [ ] **Step 3: Commit**

```bash
git add cli_agent/rendering.py
git commit -m "add output rendering: prose + SQL + table, --verbose trace"
```

---

## Task 13: CLI + REPL

**Files:**
- Create: `cli_agent/cli.py`

- [ ] **Step 1: Write CLI module**

Create `cli_agent/cli.py`:

```python
"""CLI entry: single-shot or minimal REPL."""
import argparse
import sys

import anthropic
import duckdb

from cli_agent import config
from cli_agent.agent import AgentSession
from cli_agent.prompts import bootstrap, build_system_prompt
from cli_agent.rendering import render
from cli_agent.tools import Tools


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cli_agent",
        description="Ask ad-hoc questions about the mini-data-platform warehouse.",
    )
    parser.add_argument("question", nargs="?", help="One-shot question. Omit to enter REPL.")
    parser.add_argument("--verbose", action="store_true", help="Show full tool-call trace.")
    args = parser.parse_args()

    api_key = config.require_api_key()
    if not config.WAREHOUSE_PATH.exists():
        print(f"Warehouse not found at {config.WAREHOUSE_PATH}", file=sys.stderr)
        return 2

    conn = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    tools = Tools(conn, manifest_path=config.DBT_MANIFEST_PATH)
    system_prompt = build_system_prompt(bootstrap(conn))
    client = anthropic.Anthropic(api_key=api_key)
    session = AgentSession(
        client=client, system_prompt=system_prompt, tools=tools, model=config.MODEL
    )

    if args.question:
        result = session.answer(args.question)
        print(render(result, verbose=args.verbose))
        return 0

    # REPL
    print(f"cli_agent — ask questions about the warehouse. Ctrl-D to exit. Model: {config.MODEL}")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        result = session.answer(question)
        print(render(result, verbose=args.verbose))
        print()
```

- [ ] **Step 2: Verify CLI help works**

Run: `uv run python -m cli_agent --help`

Expected: argparse prints usage including `question` and `--verbose`.

- [ ] **Step 3: Verify missing API key errors cleanly**

Run: `unset ANTHROPIC_API_KEY && uv run python -m cli_agent "test"; echo exit=$?`

Expected: error message about `ANTHROPIC_API_KEY`, exit code non-zero.

- [ ] **Step 4: Commit**

```bash
git add cli_agent/cli.py
git commit -m "cli: single-shot and minimal REPL entry point"
```

---

## Task 14: Manual smoke script

**Files:**
- Create: `cli_agent/examples.sh`

- [ ] **Step 1: Write smoke script**

Create `cli_agent/examples.sh`:

```bash
#!/usr/bin/env bash
# Manual smoke test — five canned questions across the brief's categories.
# Requires ANTHROPIC_API_KEY to be exported.
set -u

cd "$(dirname "$0")/.."

run() {
    echo "============================================================"
    echo "Q: $1"
    echo "------------------------------------------------------------"
    uv run python -m cli_agent "$1"
    echo
}

run "What was the total revenue in Q4 2024, and how many completed orders?"
run "Which two product categories are most often purchased together?"
run "Are there any obvious data anomalies I should know about?"
run "What's the average order value by customer segment?"
run "Show the monthly revenue trend for 2024."
```

- [ ] **Step 2: Make executable**

Run: `chmod +x cli_agent/examples.sh`

- [ ] **Step 3: (Optional) Run smoke against real API**

Run: `export ANTHROPIC_API_KEY=... && ./cli_agent/examples.sh 2>&1 | tee /tmp/agent-smoke.log`

Expected: five questions answered. Inspect output for: correct status filtering, subtotal used for revenue, relative dates resolved to 2024 not 2026, SQL printed under each answer.

**If any answer looks wrong**, create a task to tighten the system prompt and re-run. Do not mark Task 14 complete until the smoke passes.

- [ ] **Step 4: Commit**

```bash
git add cli_agent/examples.sh
git commit -m "add manual smoke script covering five brief categories"
```

---

## Task 15: README-agent.md

**Files:**
- Create: `README-agent.md`

- [ ] **Step 1: Write README**

Create `README-agent.md`:

````markdown
# CLI Data Agent

A terminal agent that answers ad-hoc questions about the mini-data-platform warehouse. Ask it anything — sales, product pairs, anomalies, customer metrics — and it explores the schema, writes SQL, and explains the result.

```
$ uv run python -m cli_agent "Total revenue in Q4 2024?"
Q4 2024 completed-order revenue was $512,340 across 1,842 orders.
(Revenue = subtotal, status='completed', deduped by transaction_id.)

SQL:
  SELECT SUM(subtotal) AS revenue, COUNT(DISTINCT transaction_id) AS orders
  FROM (SELECT DISTINCT transaction_id, subtotal, transaction_date, status
        FROM marts.fct_orders)
  WHERE status='completed' AND transaction_date BETWEEN '2024-10-01' AND '2024-12-31';

Result:
  | revenue | orders |
  |---|---|
  | 512340  | 1842   |
```

## Quick start

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
uv run python -m cli_agent "your question"          # single-shot
uv run python -m cli_agent                          # REPL
uv run python -m cli_agent "question" --verbose     # show tool-call trace
```

## How it works

One tool-using Claude Sonnet 4.5 agent with four tools:

| Tool | What it does |
|---|---|
| `list_tables` | Enumerate schemas + tables |
| `describe_table` | Columns, types, and dbt documentation |
| `sample_rows` | Up to 20 example rows |
| `run_sql` | Read-only SELECT against DuckDB |

At session start, the agent runs a **bootstrap query** that fetches the data's date range and mart row counts. These facts go into the system prompt so relative-date questions ("last month") resolve against `MAX(transaction_date)`, not wall-clock — critical because the synthetic data ends 2024-12-31.

**Safety:** the DuckDB connection is opened `read_only=True` at startup. `INSERT`/`UPDATE`/`DELETE`/`DROP` fail at the engine level regardless of what the LLM generates. Additional caps: 1000-row result fetch, 30s query timeout, 10-turn agent loop.

## Design decisions

**Tool-using agent, not one-shot text-to-SQL.** The brief says "keep it generic, infer from code/metadata" and asks for diverse question types. A tool-using agent can introspect schema, recover from SQL errors, and chain queries — a one-shot pipeline can't.

**Claude Sonnet 4.5 over alternatives.** Best-in-class tool use, matches the repo's MCP configuration. Model is configurable via `CLI_AGENT_MODEL`.

**Single agent, not multi-agent.** Ad-hoc questions decompose sequentially (schema → SQL → inspect). Splitting into planner+executor adds latency without capability gain. Multi-agent makes sense for parallel investigations (listed in next steps).

**Four tools, not more.** I folded dbt-docs access into `describe_table` (one tool call instead of two) and dropped an AST-based SQL guard (read-only DuckDB is already bulletproof). Smaller tool surface → less confusion in the agent loop.

## Handling the data quirks

The system prompt encodes the landmines the generator intentionally plants:

| Question type | What the agent does |
|---|---|
| Revenue totals | Filters `status='completed'`, dedupes by `transaction_id`, uses `subtotal`, states the definition |
| Order counts | `COUNT(DISTINCT transaction_id)` |
| Data anomalies | Surfaces negative totals, null payment methods, duplicate transaction IDs |
| Margin analysis | Computes `(unit_price - product_cost) * quantity` manually — ignores the misnamed `line_margin` column |
| Relative dates | Anchors on `MAX(transaction_date)` (2024-12-31), not wall-clock (2026-04-17) |
| Segment analysis | Uses LEFT-JOIN-aware filters so NULL-segment rows aren't silently dropped |

## Examples

See [`cli_agent/examples.sh`](cli_agent/examples.sh) for five canned questions across the brief's categories.

## Next steps if I had more time

- **Automated eval harness.** Pair ~20 questions with ground-truth SQL and assert the agent's answer matches within tolerance. Would turn "does it work?" into a regression-testable metric.
- **Multi-agent for scheduled investigations.** A watcher finds anomalies nightly and dispatches a diagnostic sub-agent per anomaly. Fits the task's anomaly category as a productized workflow.
- **Result caching across REPL session.** Cache tool results (especially `list_tables`, `describe_table`) within a session — saves turns on follow-ups like "now break that down by segment."
- **Chart generation.** Add a `make_chart` tool (matplotlib → PNG → terminal image via iTerm2 / Kitty protocols) for trend questions.
- **Vector-search over dbt docs.** Scales schema discovery for warehouses with thousands of tables. Overkill for 13 tables; justified at 1,000+.
- **Streaming output.** Use the Anthropic SDK's streaming API so long answers feel responsive.
- **Richer REPL.** Arrow-key history, tab-completion on table names, session transcript save (`:save session.md`).

## Known limitations

- **Prompt injection in the question text** can steer the agent toward odd SQL. Read-only enforcement means the blast radius is confusing results, not data loss — but a determined user could cost themselves API tokens.
- **Single-warehouse assumption.** Path is pinned by `CLI_AGENT_WAREHOUSE`; no multi-project mode.
- **No streaming.** Long answers print in one chunk after the whole turn completes.
- **No auth / multi-user.** Local CLI only.
- **No test coverage on the CLI and rendering layers.** Unit tests cover the tools (9 tests, strict TDD) and the agent dispatch loop (smoke test with mocked client). Manual smoke script exercises the full end-to-end.

## Testing

```bash
uv run pytest tests/cli_agent/ -v
```

Nine TDD tests on `tools.py` (list/describe/sample/run_sql, read-only enforcement, row cap, timeout) plus two smoke tests on the agent loop. No network required.
````

- [ ] **Step 2: Verify README renders**

Open `README-agent.md` in the IDE or run `head -50 README-agent.md`. Check markdown structure.

- [ ] **Step 3: Commit**

```bash
git add README-agent.md
git commit -m "add README-agent.md: approach, usage, design decisions, next steps"
```

---

## Final Verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/cli_agent/ -v`

Expected: all tests PASS.

- [ ] **Step 2: Run manual smoke (if API key available)**

Run: `./cli_agent/examples.sh`

Expected: five answers that handle the data quirks correctly (see Task 14 step 3).

- [ ] **Step 3: Check git log tells a clean story**

Run: `git log --oneline`

Expected: 15 commits, each with a focused message. No "WIP" / "fix typo" / "oops" noise.

- [ ] **Step 4: Verify line-count is in target range**

Run: `find cli_agent -name '*.py' | xargs wc -l | tail -1`

Expected: ~400–500 lines of Python in `cli_agent/` (plus ~250 in tests).

