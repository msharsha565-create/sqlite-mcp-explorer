# sqlite-mcp-explorer

A sandboxed text-to-SQL stack for AI agents: a read-only SQL server over
[MCP](https://modelcontextprotocol.io), a natural-language interface, and a **benchmark that
scores any text-to-SQL model** on syntax validity, schema adherence and execution accuracy.
Giving an agent a database is risky; the sandbox is built so a bad, hallucinated or hostile query
cannot change data or run away.

![demo](docs/demo.png)

## Safety model

Several independent layers, so one failure does not open the door:

1. **Read-only connection**: the file is opened with `mode=ro`; SQLite itself refuses writes.
2. **Authorizer allow-list**: only `SELECT`, column reads, functions and recursive CTEs are
   permitted. `DROP`, `DELETE`, `UPDATE`, `INSERT`, `CREATE`, `ALTER`, `PRAGMA`, `ATTACH`,
   `VACUUM` and `load_extension` are denied.
3. **One statement per call**, so `SELECT 1; DELETE ...` is rejected.
4. **Bounded cost**: results are capped at 200 rows (the response says if it was `truncated`),
   and a query is aborted after 2 seconds.

The tests exercise each layer, including attack queries, and check that data is unchanged
afterwards.

## Text-to-SQL and benchmark

Ask in plain English; a generator writes SQL, and that SQL runs through the same sandbox as
everything else. Plug in any OpenAI-compatible model (OpenAI, Ollama, vLLM, LM Studio):

```bash
TEXT2SQL_MODEL="openai:llama3@http://localhost:11434/v1" sqlite-explorer-web
```

Gemini works through Google's OpenAI-compatible endpoint; keep the key under its own name:

```bash
GEMINI_API_KEY=... TEXT2SQL_API_KEY_ENV=GEMINI_API_KEY \
TEXT2SQL_MODEL="openai:gemini-3.1-flash-lite@https://generativelanguage.googleapis.com/v1beta/openai" \
sqlite-explorer-web
GEMINI_API_KEY=... sqlite-explorer-bench --api-key-env GEMINI_API_KEY \
  --generator "openai:gemini-3.1-flash-lite@https://generativelanguage.googleapis.com/v1beta/openai"
```

Use a current model name from the provider's docs (Gemini 2.5 models are already retired for new
users). The generator retries 429/5xx responses, but gives up at once on a daily quota.

**Live result** (`gemini-3.1-flash-lite`, free tier, run 2026-10-22, one run): 53 questions,
100% produced SQL, 100% syntax validity, 100% schema adherence, **100% execution accuracy**;
latency p50 13.1 s, p95 52.2 s. A first run scored 98% (52/53): `q50` ("average product price
in each category") failed because the reference rounds to 2 decimals and the question did not say
so. The question now says "rounded to 2 decimals". This is a small, easy benchmark, so 100% shows
the pipeline works end to end rather than ranking models.

Benchmark any generator on 53 questions across 10 categories (counts, aggregates, joins,
group-by, subqueries, top-N, dates). Reference answers are executed SQL, not text:

```bash
sqlite-explorer-bench --generator template                          # baseline, no LLM
sqlite-explorer-bench --generator "openai:llama3@http://localhost:11434/v1" --out run.json
sqlite-explorer-bench --generator ... --min-accuracy 0.8            # exit 1 below 80%, for CI
```

| Metric | Meaning |
| --- | --- |
| Syntax validity | The SQL parses |
| Schema adherence | Every table and column exists (hallucinations fail here) |
| Execution accuracy | The result set matches the reference query's (order ignored unless the reference has `ORDER BY`) |

Unsafe SQL (for example a generated `DROP TABLE`) is flagged and never executed.

The bundled **template generator is a regex baseline, not an LLM**. It scores about 45%
(simple counts and filters pass; every join, group-by and subquery fails), which gives a real
model something to beat. Its `produced SQL` rate shows how often it declined to answer.

## Demo (no API key)

```bash
pip install -e ".[dev]"
sqlite-explorer-web                # http://127.0.0.1:8000
```

It opens a **synthetic** shop database (customers, products, orders, order items) generated
from a fixed seed; it contains no real data. The page has buttons that attempt `DROP TABLE`, a
two-statement query and an infinite recursive query, so you can watch each get rejected.

Use your own database:

```bash
SQLITE_DB=/path/to/app.db sqlite-explorer-web
```

## MCP tools

```json
{ "mcpServers": { "sqlite": { "command": "sqlite-explorer-mcp",
                              "env": { "SQLITE_DB": "/path/to/app.db" } } } }
```

| Tool | Does |
| --- | --- |
| `list_tables()` | Tables and views |
| `describe_table(table)` | Columns, types, keys, foreign keys, row count |
| `get_schema_ddl()` | `CREATE TABLE` statements; read first, then write SQL against them |
| `run_query(sql)` | One read-only SELECT; errors if rejected or timed out |

## Tests

```bash
python -m pytest
ruff check src tests
```

## Verified vs not verified

- **Verified**: the sandbox and benchmark logic (51 tests, including attack queries and a perfect
  "oracle" generator that scores 100%); the OpenAI-compatible generator against a mocked transport;
  and the benchmark CLI end to end against a local fake OpenAI-compatible server.
- **Verified live**: the benchmark against Gemini `gemini-3.1-flash-lite` (result above).
- **Not verified**: runs against a live Ollama/vLLM/OpenAI model, and the web UI with a live model.

## Limitations

- The benchmark's reference answers are tied to the seeded sample database; use `--cases` with your
  own questions for another database.
- Vector / hybrid search is not included.

- SQLite only.
- A read-only query can still read anything in the file. Point it at a database (or a copy)
  that contains only data the agent may see.
- The 2-second limit stops runaway queries, but it is a per-query guard, not a rate limiter.
- No authentication on the web console; keep it on localhost.
