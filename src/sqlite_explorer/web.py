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

INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


async def api_schema(request: Request) -> JSONResponse:
    db = get_db()
    return JSONResponse({"tables": [db.describe(t) for t in db.tables()]})


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
])


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
