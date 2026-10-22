from __future__ import annotations

import random
import sqlite3
import tempfile
from pathlib import Path

CATEGORIES = ["Books", "Electronics", "Garden", "Toys", "Kitchen"]
FIRST = ["Ava", "Liam", "Noah", "Mia", "Zoe", "Ethan", "Ivy", "Leo", "Nina", "Omar", "Sara", "Tom"]
LAST = ["Brown", "Chen", "Garcia", "Khan", "Lopez", "Miller", "Novak", "Patel", "Smith", "Wong"]
COUNTRIES = ["US", "UK", "DE", "IN", "BR", "JP"]

SCHEMA = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL,
                        signup_date TEXT NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                       price REAL NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                     order_date TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                          product_id INTEGER NOT NULL REFERENCES products(id),
                          quantity INTEGER NOT NULL);
"""


def build_sample_db(path: str | Path, seed: int = 7) -> Path:
    """Deterministic synthetic shop data. Not real customer data."""
    rng = random.Random(seed)
    path = Path(path)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for i in range(1, 61):
        conn.execute("INSERT INTO customers VALUES (?,?,?,?)", (
            i, f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(COUNTRIES),
            f"2023-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"))
    for i in range(1, 41):
        cat = rng.choice(CATEGORIES)
        conn.execute("INSERT INTO products VALUES (?,?,?,?)",
                     (i, f"{cat} item {i}", cat, round(rng.uniform(4, 180), 2)))
    item_id = 1
    for i in range(1, 301):
        conn.execute("INSERT INTO orders VALUES (?,?,?,?)", (
            i, rng.randint(1, 60), f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            rng.choices(["shipped", "pending", "cancelled"], weights=[6, 2, 1])[0]))
        for _ in range(rng.randint(1, 4)):
            conn.execute("INSERT INTO order_items VALUES (?,?,?,?)",
                         (item_id, i, rng.randint(1, 40), rng.randint(1, 5)))
            item_id += 1
    conn.commit()
    conn.close()
    return path


def default_db_path() -> Path:
    import os

    configured = os.environ.get("SQLITE_DB")
    if configured:
        return Path(configured)
    # Build next to the target and rename into place: concurrent processes never see (or read)
    # a half-written file, and one that already has the old inode open is unaffected.
    target = Path(tempfile.gettempdir()) / "sqlite_explorer_sample.db"
    staging = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    build_sample_db(staging)
    os.replace(staging, target)
    return target
