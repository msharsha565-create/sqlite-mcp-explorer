from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlite_explorer.db import SafeDatabase
from sqlite_explorer.evaluate import load_benchmark, run_benchmark
from sqlite_explorer.sample import default_db_path
from sqlite_explorer.text2sql import resolve_generator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sqlite-explorer-bench", description=__doc__)
    parser.add_argument("--generator", default="template",
                        help="'template' (baseline, no LLM) or openai:MODEL@BASE_URL")
    parser.add_argument("--cases", help="Benchmark JSONL (default: built-in, for the sample DB)")
    parser.add_argument("--out", help="Write the full run JSON here")
    parser.add_argument("--min-accuracy", type=float,
                        help="Exit 1 if execution accuracy falls below this (CI gate)")
    args = parser.parse_args(argv)

    db = SafeDatabase(default_db_path(), max_rows=10_000)
    gen = resolve_generator(args.generator, api_key=os.environ.get("OPENAI_API_KEY"))
    run = run_benchmark(db, gen, load_benchmark(args.cases), name=args.generator)
    if args.out:
        Path(args.out).write_text(json.dumps(run, indent=2))
    print(f"{run['generator']}: {run['total']} questions")
    print(f"  produced SQL        {run['generation_rate']:.0%}")
    print(f"  syntax validity     {run['syntax_validity']:.0%}")
    print(f"  schema adherence    {run['schema_adherence']:.0%}")
    print(f"  execution accuracy  {run['execution_accuracy']:.0%}")
    for cat, v in run["by_category"].items():
        print(f"    {cat:<15} {v['execution_accuracy']:.0%} ({v['total']})")
    if args.min_accuracy is not None and run["execution_accuracy"] < args.min_accuracy:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
