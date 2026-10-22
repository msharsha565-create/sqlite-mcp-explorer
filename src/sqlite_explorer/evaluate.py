from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from importlib import resources
from pathlib import Path
from typing import Any

from sqlite_explorer.db import QueryRejected, QueryTimeout, SafeDatabase
from sqlite_explorer.text2sql import Generator

SCHEMA_ERRORS = ("no such table", "no such column", "ambiguous column", "no such function")
MAX_COMPARE_ROWS = 10_000


def load_benchmark(path: str | Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        text = resources.files("sqlite_explorer").joinpath("data/benchmark.jsonl").read_text()
    else:
        text = Path(path).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _norm_row(row: list[Any]) -> tuple:
    return tuple(round(v, 4) if isinstance(v, float) else v for v in row)


def results_match(gold: list[list[Any]], got: list[list[Any]], ordered: bool) -> bool:
    """Compare result sets by values. Column names are ignored; row order matters only when the
    reference query has an ORDER BY."""
    g, r = [_norm_row(x) for x in gold], [_norm_row(x) for x in got]
    return g == r if ordered else Counter(g) == Counter(r)


def classify_error(message: str) -> str:
    low = message.lower()
    if "only read-only" in low:
        return "unsafe"
    if any(marker in low for marker in SCHEMA_ERRORS):
        return "schema"
    return "syntax"


def evaluate_case(db: SafeDatabase, generator: Generator, schema: str,
                  case: dict[str, Any]) -> dict[str, Any]:
    """Three graded levels: syntax validity, schema adherence, execution accuracy."""
    result: dict[str, Any] = {
        "id": case["id"], "category": case["category"], "question": case["question"],
        "generated_sql": None, "generated": False, "syntax_valid": False, "schema_valid": False, "correct": False,
        "error": None, "latency_ms": None,
    }
    started = time.perf_counter()
    try:
        sql = generator(case["question"], schema)
    except Exception as exc:  # noqa: BLE001 - any generator failure is a failed case
        result["error"] = f"generation failed: {exc}"
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return result
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["generated_sql"] = sql
    result["generated"] = True
    try:
        db.analyze(sql)
    except QueryRejected as exc:
        kind = classify_error(str(exc))
        result["syntax_valid"] = kind == "schema"  # it parsed; the names were wrong
        result["error"] = f"{kind}: {exc}"
        return result
    result["syntax_valid"] = result["schema_valid"] = True
    try:
        got = db.query(sql)
        gold = db.query(case["gold_sql"])
    except (QueryRejected, QueryTimeout) as exc:
        result["error"] = f"execution: {exc}"
        return result
    result["correct"] = (
        not got["truncated"] and not gold["truncated"]
        and results_match(gold["rows"], got["rows"], "ORDER BY" in case["gold_sql"].upper())
    )
    if not result["correct"]:
        result["error"] = "result set differs from reference"
    return result


def run_benchmark(db: SafeDatabase, generator: Generator, cases: list[dict[str, Any]],
                  name: str = "generator") -> dict[str, Any]:
    schema = db.schema_ddl()
    results = [evaluate_case(db, generator, schema, c) for c in cases]
    n = len(results)
    latencies = sorted(r["latency_ms"] for r in results if r["latency_ms"] is not None)
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    def rate(rows: list[dict[str, Any]], key: str) -> float:
        return round(sum(r[key] for r in rows) / len(rows), 4) if rows else 0.0

    return {
        "generator": name,
        "total": n,
        "generation_rate": rate(results, "generated"),
        "syntax_validity": rate(results, "syntax_valid"),
        "schema_adherence": rate(results, "schema_valid"),
        "execution_accuracy": rate(results, "correct"),
        "latency_p50_ms": latencies[max(0, math.ceil(0.5 * len(latencies)) - 1)] if latencies else None,
        "latency_p95_ms": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None,
        "by_category": {c: {"total": len(v), "execution_accuracy": rate(v, "correct")}
                        for c, v in sorted(by_cat.items())},
        "results": results,
    }
