import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from sqlite_explorer import server, web
from sqlite_explorer.db import SafeDatabase
from sqlite_explorer.sample import build_sample_db


@pytest.fixture(autouse=True)
def sample_db(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_db", SafeDatabase(build_sample_db(tmp_path / "s.db"), timeout_s=0.5))


async def test_mcp_tools_and_safety():
    async with create_connected_server_and_client_session(server.mcp._mcp_server) as s:
        names = {t.name for t in (await s.list_tools()).tools}
        tables = await s.call_tool("list_tables", {})
        ok = await s.call_tool("run_query", {"sql": "SELECT COUNT(*) AS n FROM customers"})
        bad = await s.call_tool("run_query", {"sql": "DROP TABLE customers"})
    assert names == {"list_tables", "describe_table", "run_query"}
    assert "customers" in tables.content[0].text
    assert ok.structuredContent["rows"] == [[60]]
    assert bad.isError is True


def test_web_endpoints():
    client = TestClient(web.app)
    assert "sqlite-explorer" in client.get("/").text
    assert len(client.get("/api/schema").json()["tables"]) == 4
    good = client.post("/api/query", json={"sql": "SELECT 1 AS one"})
    assert good.json()["rows"] == [[1]]
    rejected = client.post("/api/query", json={"sql": "DROP TABLE customers"})
    assert rejected.status_code == 400 and rejected.json()["kind"] == "rejected"
    timeout = client.post("/api/query", json={"sql": (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r")})
    assert timeout.status_code == 408
