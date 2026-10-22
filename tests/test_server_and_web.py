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
    assert names == {"list_tables", "describe_table", "run_query", "get_schema_ddl"}
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


def test_ask_endpoint_generates_and_executes_sql():
    client = TestClient(web.app)
    good = client.post("/api/ask", json={"question": "How many customers are from US?"}).json()
    assert good["sql"].startswith("SELECT COUNT(*)") and good["row_count"] == 1
    unknown = client.post("/api/ask", json={"question": "Who is the best customer?"})
    assert unknown.status_code == 422 and unknown.json()["kind"] == "generation"
    assert client.post("/api/ask", json={"question": "  "}).status_code == 400


def test_ask_endpoint_cannot_be_used_to_write(monkeypatch):
    monkeypatch.setattr(web, "get_generator", lambda: (lambda q, schema: "DELETE FROM customers"))
    resp = TestClient(web.app).post("/api/ask", json={"question": "wipe it"})
    assert resp.status_code == 400 and resp.json()["kind"] == "rejected"
    assert TestClient(web.app).post("/api/query", json={"sql": "SELECT COUNT(*) FROM customers"}
                                    ).json()["rows"] == [[60]]
