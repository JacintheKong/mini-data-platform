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
