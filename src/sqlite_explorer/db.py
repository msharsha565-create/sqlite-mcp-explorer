from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
}
DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile"}


class QueryRejected(Exception):
    """The query is not a permitted read-only statement."""


class QueryTimeout(Exception):
    pass


def _authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None,
                source: str | None) -> int:
    if action not in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class SafeDatabase:
    """Read-only SQL access. Layers of defense, each sufficient on its own for writes:

    1. the file is opened with mode=ro, so SQLite itself refuses to write
    2. an authorizer allows only SELECT, column reads and functions (no ATTACH, PRAGMA, DDL, DML)
    3. one statement per call
    4. result size is capped and the query is aborted after a deadline
    """

    def __init__(self, path: str | Path, max_rows: int = 200, timeout_s: float = 2.0) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def query(self, sql: str) -> dict[str, Any]:
        if not sql.strip():
            raise QueryRejected("Empty query.")
        conn = self._connect()
        try:
            conn.set_authorizer(_authorizer)
            deadline = time.monotonic() + self.timeout_s
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            try:
                cursor = conn.execute(sql)
                rows = cursor.fetchmany(self.max_rows + 1)
            except sqlite3.DatabaseError as exc:
                if "interrupted" in str(exc).lower():
                    raise QueryTimeout(f"Query exceeded {self.timeout_s}s.") from exc
                if "not authorized" in str(exc).lower():
                    raise QueryRejected("Only read-only SELECT queries are allowed.") from exc
                raise QueryRejected(str(exc)) from exc
            except sqlite3.Warning as exc:  # e.g. more than one statement
                raise QueryRejected(str(exc)) from exc
            truncated = len(rows) > self.max_rows
            rows = rows[: self.max_rows]
            columns = [d[0] for d in cursor.description or []]
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
            }
        finally:
            conn.close()

    def analyze(self, sql: str) -> dict[str, list[str]]:
        """Compile (EXPLAIN) one statement without running it; return the tables and columns it
        reads. Raises QueryRejected on syntax errors, unknown tables/columns, or unsafe statements."""
        if not sql.strip():
            raise QueryRejected("Empty query.")
        reads: set[tuple[str, str]] = set()

        def authorizer(action, arg1, arg2, db, source):
            if action == sqlite3.SQLITE_READ and arg1:
                reads.add((arg1, arg2 or ""))
            return _authorizer(action, arg1, arg2, db, source)

        conn = self._connect()
        try:
            conn.set_authorizer(authorizer)
            try:
                conn.execute("EXPLAIN " + sql)
            except (sqlite3.DatabaseError, sqlite3.Warning) as exc:
                if "not authorized" in str(exc).lower():
                    raise QueryRejected("Only read-only SELECT queries are allowed.") from exc
                raise QueryRejected(str(exc)) from exc
            return {"tables": sorted({t for t, _ in reads}),
                    "columns": sorted(f"{t}.{c}" for t, c in reads if c)}
        finally:
            conn.close()

    def schema_ddl(self) -> str:
        """CREATE statements for every table, suitable for an LLM prompt."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            return "\n".join(" ".join(r["sql"].split()) + ";" for r in rows)
        finally:
            conn.close()

    def tables(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def describe(self, table: str) -> dict[str, Any]:
        if table not in self.tables():
            raise QueryRejected(f"Unknown table {table!r}. Tables: {self.tables()}")
        conn = self._connect()
        try:
            cols = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
            fks = conn.execute(f"PRAGMA foreign_key_list({_quote(table)})").fetchall()
            count = conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]
            return {
                "table": table,
                "row_count": count,
                "columns": [{"name": c["name"], "type": c["type"], "primary_key": bool(c["pk"]),
                             "not_null": bool(c["notnull"])} for c in cols],
                "foreign_keys": [{"column": f["from"], "references": f"{f['table']}.{f['to']}"}
                                 for f in fks],
            }
        finally:
            conn.close()
