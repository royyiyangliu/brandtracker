"""Orchestrator: scrape -> convert to CNY -> persist -> export web data.

Usage:
    python -m src.run config/boucheron.yaml
    python -m src.run config/cartier.yaml

每个品牌单独一次运行（互不影响）：抓取逻辑分发到 src/brands/<adapter>，通用底座
（fx / storage / export_web）共用。报告（latest.md）已取消——只产出前端 data.json。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import export_web, fx, storage


def main(config_path: str) -> int:
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    print(f"== {cfg['brand']} == run {run_ts} (UTC)")

    # 1. scrape (import here so report-only environments don't need playwright)
    from .brands import scrape_brand
    rows = scrape_brand(cfg)
    if not rows:
        print("No products scraped; aborting.")
        return 1

    # 2. FX -> CNY
    print("Fetching FX rates -> CNY ...")
    rates = fx.fetch_rates_to_cny()
    for r in rows:
        r["cny_price"] = fx.to_cny(r.get("local_price"), r["currency"], rates)

    # 3. persist
    conn = storage.connect()
    n = storage.save_observations(conn, run_ts, rows)
    print(f"Saved {n} observations to {storage.DB_PATH}")

    # 4. export combined web data (所有品牌各自最新 run 合并到一个 data.json)
    web = export_web.export(conn, rates.get("updated", ""))
    print(f"Web data written to {web}")
    return 0


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config/boucheron.yaml"
    raise SystemExit(main(cfg))
