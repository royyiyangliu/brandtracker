"""SQLite persistence for scraped observations.

Each weekly run appends a batch of observations stamped with a run timestamp, so
price history is preserved and trends can be charted later.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "prices.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    run_ts       TEXT    NOT NULL,
    brand        TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    country      TEXT    NOT NULL,
    currency     TEXT    NOT NULL,
    ref          TEXT    NOT NULL,
    name         TEXT,
    local_price  INTEGER,
    cny_price    REAL,
    url          TEXT,
    PRIMARY KEY (run_ts, country, ref)
);
CREATE INDEX IF NOT EXISTS idx_obs_ref ON observations(ref);
CREATE INDEX IF NOT EXISTS idx_obs_run ON observations(run_ts);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # If an older schema (pre-category) exists, reset it — early data is throwaway.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
    if cols and "category" not in cols:
        conn.execute("DROP TABLE observations")
    conn.executescript(_SCHEMA)
    return conn


def save_observations(conn: sqlite3.Connection, run_ts: str, rows: list[dict]) -> int:
    payload = [
        (
            run_ts,
            r["brand"],
            r["category"],
            r["country"],
            r["currency"],
            r["ref"],
            r.get("name"),
            r.get("local_price"),
            r.get("cny_price"),
            r.get("url"),
        )
        for r in rows
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO observations
           (run_ts, brand, category, country, currency, ref, name,
            local_price, cny_price, url)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        payload,
    )
    conn.commit()
    return len(payload)


def latest_run_ts(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(run_ts) AS ts FROM observations").fetchone()
    return row["ts"] if row else None


def latest_run_per_brand(conn: sqlite3.Connection) -> dict[str, str]:
    """每个品牌各自最新一次 run 的时间戳：{brand: run_ts}.

    品牌各自独立运行（时间戳不同），前端/汇率刷新都按「每品牌最新 run」取数。
    """
    rows = conn.execute(
        "SELECT brand, MAX(run_ts) AS ts FROM observations GROUP BY brand"
    ).fetchall()
    return {r["brand"]: r["ts"] for r in rows}


def load_run(conn: sqlite3.Connection, run_ts: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM observations WHERE run_ts = ?", (run_ts,)
    ).fetchall()
