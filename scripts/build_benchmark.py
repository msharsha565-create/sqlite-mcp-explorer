"""Generate src/sqlite_explorer/data/benchmark.jsonl against the seeded sample database.

Every reference query is executed here; questions whose answer would depend on tie-breaking are
dropped, so grading is unambiguous.
"""
import json
import sys
import tempfile
from pathlib import Path

from sqlite_explorer.db import SafeDatabase
from sqlite_explorer.sample import CATEGORIES, COUNTRIES, build_sample_db

OUT = Path(__file__).resolve().parents[1] / "src/sqlite_explorer/data/benchmark.jsonl"
db = SafeDatabase(build_sample_db(Path(tempfile.gettempdir()) / "bench_build.db"), max_rows=10_000)
cases: list[dict] = []


def add(category: str, question: str, gold: str, ordered_unique: str | None = None) -> None:
    rows = db.query(gold)["rows"]
    if ordered_unique:  # check the top-N cutoff is not a tie
        values = [r[0] for r in db.query(ordered_unique)["rows"]]
        n = len(rows)
        if len(values) > n and values[n - 1] == values[n]:
            print("skipping tie:", question, file=sys.stderr)
            return
    cases.append({"id": f"q{len(cases) + 1:02d}", "category": category, "question": question,
                  "gold_sql": gold})


for c in COUNTRIES:
    add("simple_count", f"How many customers are from {c}?",
        f"SELECT COUNT(*) FROM customers WHERE country = '{c}'")
for cat in CATEGORIES:
    add("simple_count", f"How many products are in the {cat} category?",
        f"SELECT COUNT(*) FROM products WHERE category = '{cat}'")
for cat in CATEGORIES:
    add("aggregate", f"What is the average price of {cat} products?",
        f"SELECT AVG(price) FROM products WHERE category = '{cat}'")
for status in ("shipped", "pending", "cancelled"):
    add("simple_count", f"How many orders are {status}?",
        f"SELECT COUNT(*) FROM orders WHERE status = '{status}'")
add("ordering", "What is the most expensive product?",
    "SELECT name FROM products ORDER BY price DESC LIMIT 1",
    "SELECT price FROM products ORDER BY price DESC")
add("simple_count", "How many different countries do customers come from?",
    "SELECT COUNT(DISTINCT country) FROM customers")
add("ordering", "List all countries our customers come from in alphabetical order.",
    "SELECT DISTINCT country FROM customers ORDER BY country")
for x in (50, 100, 150):
    add("filter", f"List the names of products priced above {x}.",
        f"SELECT name FROM products WHERE price > {x}")
for c in ("US", "UK", "DE", "IN"):
    add("join", f"How many orders were placed by customers from {c}?",
        f"SELECT COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id "
        f"WHERE c.country = '{c}'")
for s in ("shipped", "pending", "cancelled"):
    add("join", f"How many order items belong to {s} orders?",
        f"SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        f"WHERE o.status = '{s}'")
for cat in CATEGORIES:
    add("join_aggregate", f"What is the total revenue from shipped orders in the {cat} category?",
        f"SELECT ROUND(SUM(oi.quantity * p.price), 2) FROM order_items oi "
        f"JOIN products p ON p.id = oi.product_id JOIN orders o ON o.id = oi.order_id "
        f"WHERE o.status = 'shipped' AND p.category = '{cat}'")
for cat in CATEGORIES:
    add("join_aggregate", f"What is the total quantity sold of {cat} products?",
        f"SELECT SUM(oi.quantity) FROM order_items oi JOIN products p ON p.id = oi.product_id "
        f"WHERE p.category = '{cat}'")
for m in ("01", "03", "06", "09", "12"):
    add("date", f"How many customers signed up in 2023-{m}?",
        f"SELECT COUNT(*) FROM customers WHERE signup_date LIKE '2023-{m}-%'")
add("group_by", "How many customers are there in each country?",
    "SELECT country, COUNT(*) FROM customers GROUP BY country")
add("group_by", "How many orders are there in each status?",
    "SELECT status, COUNT(*) FROM orders GROUP BY status")
add("group_by", "What is the average product price in each category?",
    "SELECT category, ROUND(AVG(price), 2) FROM products GROUP BY category")
add("subquery", "How many customers have never placed an order?",
    "SELECT COUNT(*) FROM customers WHERE id NOT IN (SELECT customer_id FROM orders)")
add("top_n", "Which 3 customers placed the most orders? Give their names.",
    "SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id "
    "GROUP BY c.id ORDER BY COUNT(*) DESC LIMIT 3",
    "SELECT COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id "
    "GROUP BY c.id ORDER BY COUNT(*) DESC")
add("top_n", "Which category has the highest average price?",
    "SELECT category FROM products GROUP BY category ORDER BY AVG(price) DESC LIMIT 1",
    "SELECT AVG(price) FROM products GROUP BY category ORDER BY AVG(price) DESC")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
print(f"wrote {len(cases)} cases to {OUT}")
