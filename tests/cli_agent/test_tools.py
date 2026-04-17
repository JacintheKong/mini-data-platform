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
