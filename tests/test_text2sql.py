import json

import httpx
import pytest

from sqlite_explorer.db import SafeDatabase
from sqlite_explorer.evaluate import (
    classify_error,
    evaluate_case,
    load_benchmark,
    results_match,
    run_benchmark,
)
from sqlite_explorer.sample import build_sample_db
from sqlite_explorer.text2sql import (
    CannotGenerate,
    OpenAICompatGenerator,
    TemplateGenerator,
    extract_sql,
    resolve_generator,
)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = build_sample_db(tmp_path_factory.mktemp("bench") / "shop.db")
    return SafeDatabase(path, max_rows=10_000)


def case(question="q", gold="SELECT COUNT(*) FROM customers", category="c"):
    return {"id": "t", "category": category, "question": question, "gold_sql": gold}


def fixed(sql):
    return lambda question, schema: sql


def test_benchmark_has_50_plus_unique_questions_in_several_categories():
    cases = load_benchmark()
    assert len(cases) >= 50
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["category"] for c in cases}) >= 8


def test_every_reference_query_runs_and_is_safe(db):
    for c in load_benchmark():
        result = db.query(c["gold_sql"])
        assert result["row_count"] > 0 and not result["truncated"], c["id"]
        assert c["gold_sql"].lstrip().upper().startswith("SELECT")


def test_perfect_generator_scores_100_percent(db):
    gold = {c["question"]: c["gold_sql"] for c in load_benchmark()}
    run = run_benchmark(db, lambda q, s: gold[q], load_benchmark(), "oracle")
    assert run["execution_accuracy"] == run["syntax_validity"] == run["schema_adherence"] == 1.0
    assert run["generation_rate"] == 1.0


def test_template_baseline_is_partial_and_fails_joins(db):
    run = run_benchmark(db, TemplateGenerator(), load_benchmark(), "template")
    assert 0.2 < run["execution_accuracy"] < 0.8
    assert run["by_category"]["join_aggregate"]["execution_accuracy"] == 0.0
    assert run["by_category"]["simple_count"]["execution_accuracy"] == 1.0


def test_correct_sql_written_differently_still_counts(db):
    r = evaluate_case(db, fixed("SELECT COUNT(id) AS n FROM customers"), db.schema_ddl(), case())
    assert r["correct"] and r["syntax_valid"] and r["schema_valid"]


def test_wrong_answer_is_valid_but_incorrect(db):
    r = evaluate_case(db, fixed("SELECT COUNT(*) FROM products"), db.schema_ddl(), case())
    assert r["syntax_valid"] and r["schema_valid"] and not r["correct"]
    assert "differs" in r["error"]


def test_hallucinated_column_fails_schema_adherence_but_not_syntax(db):
    r = evaluate_case(db, fixed("SELECT email FROM customers"), db.schema_ddl(), case())
    assert r["syntax_valid"] is True
    assert r["schema_valid"] is False and not r["correct"]
    assert r["error"].startswith("schema:")


def test_hallucinated_table_fails_schema_adherence(db):
    r = evaluate_case(db, fixed("SELECT COUNT(*) FROM users"), db.schema_ddl(), case())
    assert r["syntax_valid"] and not r["schema_valid"]


def test_syntax_error_fails_syntax_validity(db):
    r = evaluate_case(db, fixed("SELEC COUNT(*) FROM customers"), db.schema_ddl(), case())
    assert not r["syntax_valid"] and not r["schema_valid"]
    assert r["error"].startswith("syntax:")


def test_generated_write_statement_is_flagged_unsafe_and_never_runs(db):
    before = db.query("SELECT COUNT(*) FROM customers")["rows"]
    r = evaluate_case(db, fixed("DROP TABLE customers"), db.schema_ddl(), case())
    assert r["error"].startswith("unsafe:") and not r["correct"]
    assert db.query("SELECT COUNT(*) FROM customers")["rows"] == before


def test_generator_failure_is_a_failed_case_not_a_crash(db):
    def boom(question, schema):
        raise RuntimeError("model down")

    r = evaluate_case(db, boom, db.schema_ddl(), case())
    assert not r["generated"] and "model down" in r["error"]


def test_results_match_ordering_semantics():
    a, b = [[1], [2]], [[2], [1]]
    assert results_match(a, b, ordered=False) is True
    assert results_match(a, b, ordered=True) is False
    assert results_match([[0.30000000004]], [[0.3]], ordered=True) is True


def test_classify_error():
    assert classify_error("no such column: x") == "schema"
    assert classify_error("Only read-only SELECT queries are allowed.") == "unsafe"
    assert classify_error('near "SELEC": syntax error') == "syntax"


def test_analyze_reports_referenced_tables_and_columns(db):
    info = db.analyze("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id")
    assert info["tables"] == ["customers", "orders"]
    assert "customers.name" in info["columns"] and "orders.customer_id" in info["columns"]


def test_schema_ddl_lists_all_tables(db):
    ddl = db.schema_ddl()
    for table in ("customers", "products", "orders", "order_items"):
        assert f"CREATE TABLE {table}" in ddl


def test_extract_sql_from_fenced_and_plain_replies():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("Sure:\n```\nSELECT 2\n```\nDone") == "SELECT 2"
    assert extract_sql("SELECT 3;") == "SELECT 3"


def test_openai_generator_sends_schema_and_question():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "```sql\nSELECT 1\n```"}}]})

    gen = OpenAICompatGenerator("m", "http://localhost:11434/v1",
                                client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert gen("How many?", "CREATE TABLE t (a);") == "SELECT 1"
    prompt = seen["body"]["messages"][1]["content"]
    assert "CREATE TABLE t (a);" in prompt and "How many?" in prompt
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["body"]["temperature"] == 0.0


def test_template_generator_declines_unknown_questions():
    with pytest.raises(CannotGenerate):
        TemplateGenerator()("Which customer spent the most?", "")


def test_resolve_generator():
    assert isinstance(resolve_generator("template"), TemplateGenerator)
    assert isinstance(resolve_generator("openai:llama3@http://x/v1"), OpenAICompatGenerator)
    with pytest.raises(ValueError):
        resolve_generator("nope")


def test_bench_cli_reads_api_key_from_named_env_var(monkeypatch, tmp_path):
    from sqlite_explorer import bench

    seen = []

    def fake_resolve(spec, api_key=None):
        seen.append(api_key)
        return TemplateGenerator()

    monkeypatch.setattr(bench, "resolve_generator", fake_resolve)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("SQLITE_DB", str(build_sample_db(tmp_path / "s.db")))
    bench.main(["--generator", "openai:m@http://x/v1", "--api-key-env", "GEMINI_API_KEY"])
    assert seen == ["gem-key"]


def test_openai_generator_retries_rate_limits_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr("sqlite_explorer.text2sql.time.sleep", sleeps.append)
    replies = iter([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(503),
                    httpx.Response(200, json={"choices": [{"message": {"content": "SELECT 1"}}]})])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(replies)))
    assert OpenAICompatGenerator("m", client=client)("q", "ddl") == "SELECT 1"
    assert sleeps == [7.0, 10]


def test_openai_generator_does_not_retry_a_daily_quota(monkeypatch):
    sleeps = []
    monkeypatch.setattr("sqlite_explorer.text2sql.time.sleep", sleeps.append)
    body = '{"error": {"details": [{"retryDelay": "29919s"}]}}'
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, text=body)))
    with pytest.raises(httpx.HTTPStatusError, match="429"):
        OpenAICompatGenerator("m", client=client)("q", "ddl")
    assert sleeps == []
