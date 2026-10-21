import sqlite3

import pytest

from sqlite_explorer.db import QueryRejected, QueryTimeout, SafeDatabase
from sqlite_explorer.sample import build_sample_db


@pytest.fixture
def db(tmp_path):
    return SafeDatabase(build_sample_db(tmp_path / "shop.db"), max_rows=5, timeout_s=0.5)


def row_count(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_sample_db_is_deterministic(tmp_path):
    a = build_sample_db(tmp_path / "a.db", seed=7)
    b = build_sample_db(tmp_path / "b.db", seed=7)
    conn_a, conn_b = sqlite3.connect(a), sqlite3.connect(b)
    q = "SELECT * FROM customers ORDER BY id"
    assert conn_a.execute(q).fetchall() == conn_b.execute(q).fetchall()
    assert row_count(a, "customers") == 60


def test_select_works_and_caps_rows(db):
    result = db.query("SELECT id FROM customers ORDER BY id")
    assert result["columns"] == ["id"]
    assert result["row_count"] == 5 and result["truncated"] is True
    small = db.query("SELECT id FROM customers WHERE id <= 3 ORDER BY id")
    assert small["rows"] == [[1], [2], [3]] and small["truncated"] is False


def test_joins_and_aggregates_allowed(db):
    result = db.query(
        "SELECT p.category, SUM(oi.quantity) FROM order_items oi "
        "JOIN products p ON p.id = oi.product_id GROUP BY p.category")
    assert result["row_count"] > 0


@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "UPDATE customers SET name = 'x'",
    "INSERT INTO customers VALUES (999, 'x', 'US', '2024-01-01')",
    "CREATE TABLE evil (a)",
    "ALTER TABLE customers ADD COLUMN x TEXT",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "SELECT load_extension('/tmp/x.so')",
    "VACUUM",
    "REPLACE INTO customers VALUES (1, 'x', 'US', '2024-01-01')",
])
def test_write_and_dangerous_statements_rejected(db, sql):
    with pytest.raises(QueryRejected):
        db.query(sql)


def test_multiple_statements_rejected_and_nothing_executes(db):
    before = row_count(db.path, "orders")
    with pytest.raises(QueryRejected):
        db.query("SELECT 1; DELETE FROM orders")
    assert row_count(db.path, "orders") == before


def test_data_unchanged_after_attacks(db):
    before = row_count(db.path, "customers")
    for sql in ("DROP TABLE customers", "DELETE FROM customers"):
        with pytest.raises(QueryRejected):
            db.query(sql)
    assert row_count(db.path, "customers") == before


def test_readonly_connection_blocks_writes_even_without_authorizer(db):
    conn = db._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM customers")
    conn.close()


def test_runaway_query_times_out_but_bounded_recursion_works(db):
    with pytest.raises(QueryTimeout):
        db.query("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) "
                 "SELECT COUNT(*) FROM r")
    ok = db.query("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 3) "
                  "SELECT n FROM r")
    assert ok["rows"] == [[1], [2], [3]]


def test_syntax_error_and_empty_query_rejected(db):
    with pytest.raises(QueryRejected):
        db.query("SELEC nope")
    with pytest.raises(QueryRejected):
        db.query("   ")


def test_describe_and_tables(db):
    assert db.tables() == ["customers", "order_items", "orders", "products"]
    info = db.describe("orders")
    assert info["row_count"] == 300
    assert {"column": "customer_id", "references": "customers.id"} in info["foreign_keys"]
    assert any(c["name"] == "id" and c["primary_key"] for c in info["columns"])


def test_describe_rejects_unknown_and_injection(db):
    with pytest.raises(QueryRejected):
        db.describe("nope")
    with pytest.raises(QueryRejected):
        db.describe('customers"); DROP TABLE customers; --')


def test_missing_database_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SafeDatabase(tmp_path / "missing.db")
