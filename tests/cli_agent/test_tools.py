"""Unit tests for cli_agent.tools — TDD."""
import pytest

import duckdb

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
    conn_ro.close()
    # Verify table is still there via a fresh read-write connection
    check = duckdb.connect(str(db_path))
    assert check.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    check.close()


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
