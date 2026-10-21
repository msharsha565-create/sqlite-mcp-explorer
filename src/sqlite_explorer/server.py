from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from sqlite_explorer.db import SafeDatabase
from sqlite_explorer.sample import default_db_path

mcp = FastMCP("sqlite-explorer")
_db: SafeDatabase | None = None


def get_db() -> SafeDatabase:
    global _db
    if _db is None:
        _db = SafeDatabase(default_db_path())
    return _db


@mcp.tool()
def list_tables() -> list[str]:
    """Names of the tables and views in the database."""
    return get_db().tables()


@mcp.tool()
def describe_table(table: str) -> dict[str, Any]:
    """Columns, types, primary keys, foreign keys and row count of a table."""
    return get_db().describe(table)


@mcp.tool()
def run_query(sql: str) -> dict[str, Any]:
    """Run ONE read-only SELECT statement. Writes, PRAGMA, ATTACH and multiple statements are
    rejected. Results are capped at 200 rows (check `truncated`) and queries time out after 2s."""
    return get_db().query(sql)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
