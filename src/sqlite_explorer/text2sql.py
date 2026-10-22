from __future__ import annotations

import re
from collections.abc import Callable
from typing import ClassVar

import httpx

Generator = Callable[[str, str], str]  # (question, schema_ddl) -> SQL


class CannotGenerate(Exception):
    """The generator has no SQL for this question."""


SYSTEM_PROMPT = (
    "You translate questions into ONE read-only SQLite SELECT statement. Use only the tables and "
    "columns in the schema. Output only the SQL, with no explanation."
)


def extract_sql(text: str) -> str:
    """Pull the SQL out of a model reply (fenced block, or the raw text)."""
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    sql = (fenced.group(1) if fenced else text).strip()
    return sql.rstrip(";").strip()


class OpenAICompatGenerator:
    """Text-to-SQL with any OpenAI-compatible chat endpoint (OpenAI, Ollama, vLLM, LM Studio)."""

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1",
                 api_key: str | None = None, temperature: float = 0.0,
                 client: httpx.Client | None = None, timeout: float = 120.0) -> None:
        self.model, self.temperature = model, temperature
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(timeout=timeout)

    def __call__(self, question: str, schema_ddl: str) -> str:
        resp = self.client.post(self.url, headers=self.headers, json={
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Schema:\n{schema_ddl}\n\nQuestion: {question}"},
            ],
        })
        resp.raise_for_status()
        return extract_sql(resp.json()["choices"][0]["message"]["content"])


class TemplateGenerator:
    """Regex-template baseline. NOT a language model: it handles a handful of simple question
    shapes (single-table counts, averages, filters) and gives up on joins and grouping. It exists
    so the benchmark and the demo run without an API key."""

    PATTERNS: ClassVar[list[tuple[str, Callable[[re.Match[str]], str]]]] = [
        (r"how many customers are from (\w+)",
         lambda m: f"SELECT COUNT(*) FROM customers WHERE country = '{m[1].upper()}'"),
        (r"how many products are in the (\w+) category",
         lambda m: f"SELECT COUNT(*) FROM products WHERE category = '{m[1].capitalize()}'"),
        (r"average price of (\w+) products",
         lambda m: f"SELECT AVG(price) FROM products WHERE category = '{m[1].capitalize()}'"),
        (r"how many orders are (\w+)",
         lambda m: f"SELECT COUNT(*) FROM orders WHERE status = '{m[1].lower()}'"),
        (r"most expensive product",
         lambda m: "SELECT name FROM products ORDER BY price DESC LIMIT 1"),
        (r"how many different countries",
         lambda m: "SELECT COUNT(DISTINCT country) FROM customers"),
        (r"names of products priced above (\d+)",
         lambda m: f"SELECT name FROM products WHERE price > {m[1]}"),
    ]

    def __call__(self, question: str, schema_ddl: str) -> str:
        for pattern, build in self.PATTERNS:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                return build(match)
        raise CannotGenerate("no template matches this question")


def resolve_generator(spec: str, api_key: str | None = None) -> Generator:
    """'template' | 'openai:MODEL@BASE_URL' (BASE_URL optional)."""
    if spec == "template":
        return TemplateGenerator()
    if spec.startswith("openai:"):
        model, _, base = spec[len("openai:"):].partition("@")
        return OpenAICompatGenerator(model, base or "https://api.openai.com/v1", api_key)
    raise ValueError(f"Unknown generator {spec!r}. Use 'template' or 'openai:MODEL@URL'.")
