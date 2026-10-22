from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from sqlite_explorer.db import QueryRejected, QueryTimeout
from sqlite_explorer.server import get_db
from sqlite_explorer.text2sql import resolve_generator

INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


def get_generator():
    return resolve_generator(os.environ.get("TEXT2SQL_MODEL", "template"),
                             api_key=os.environ.get("OPENAI_API_KEY"))


async def api_schema(request: Request) -> JSONResponse:
    db = get_db()
    return JSONResponse({"tables": [db.describe(t) for t in db.tables()],
                         "generator": os.environ.get("TEXT2SQL_MODEL", "template")})


async def api_ask(request: Request) -> JSONResponse:
    """Natural language -> SQL -> the same sandboxed execution path as /api/query."""
    question = str((await request.json()).get("question", "")).strip()
    if not question:
        return JSONResponse({"error": "Ask a question first."}, status_code=400)
    db = get_db()
    try:
        sql = get_generator()(question, db.schema_ddl())
    except Exception as exc:  # noqa: BLE001 - surface any generator failure to the user
        return JSONResponse({"error": f"Could not generate SQL: {exc}", "kind": "generation"},
                            status_code=422)
    try:
        return JSONResponse({"sql": sql, **db.query(sql)})
    except QueryRejected as exc:
        return JSONResponse({"sql": sql, "error": str(exc), "kind": "rejected"}, status_code=400)
    except QueryTimeout as exc:
        return JSONResponse({"sql": sql, "error": str(exc), "kind": "timeout"}, status_code=408)


async def api_query(request: Request) -> JSONResponse:
    sql = str((await request.json()).get("sql", ""))
    try:
        return JSONResponse(get_db().query(sql))
    except QueryRejected as exc:
        return JSONResponse({"error": str(exc), "kind": "rejected"}, status_code=400)
    except QueryTimeout as exc:
        return JSONResponse({"error": str(exc), "kind": "timeout"}, status_code=408)


app = Starlette(routes=[
    Route("/", index),
    Route("/api/schema", api_schema),
    Route("/api/query", api_query, methods=["POST"]),
    Route("/api/ask", api_ask, methods=["POST"]),
])


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
