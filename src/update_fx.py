"""Daily FX-only refresh (no scraping).

抓取是每周一次的，但汇率每天都在变。本脚本只做汇率更新：对**每个品牌各自最近一次
抓取**的观测，用今天的实时汇率重新换算 CNY 价，写回数据库，然后重新导出前端 JSON。

- 品牌价格数据（local_price / run_ts）保持不变 —— 只有 cny_price 与汇率快照时间更新。
- 因此前端「品牌价格数据更新」时间不变，「汇率更新」时间会刷新为今天。

用法：
    python -m src.update_fx
"""
from __future__ import annotations

from . import export_web, fx, storage


def main() -> int:
    conn = storage.connect()
    latest = storage.latest_run_per_brand(conn)   # {brand: run_ts}
    if not latest:
        print("数据库中没有任何抓取记录，先跑一次抓取再刷新汇率。")
        return 1

    print(f"== 汇率刷新，覆盖品牌：{', '.join(latest)} ==")
    rates = fx.fetch_rates_to_cny()
    fx_updated = rates.get("updated", "?")

    total = 0
    for brand, run_ts in latest.items():
        rows = storage.load_run(conn, run_ts)
        rows = [r for r in rows if r["brand"] == brand]
        for r in rows:
            cny = fx.to_cny(r["local_price"], r["currency"], rates)
            conn.execute(
                "UPDATE observations SET cny_price = ? "
                "WHERE run_ts = ? AND country = ? AND ref = ?",
                (cny, run_ts, r["country"], r["ref"]),
            )
        total += len(rows)
        print(f"  [{brand}] 重算 {len(rows)} 条（run {run_ts}）")
    conn.commit()
    print(f"共重算 {total} 条 CNY（汇率更新于 {fx_updated}）。")

    # 重新导出前端数据（合并所有品牌最新 run；run_ts 不变，只换汇率快照）。
    web = export_web.export(conn, fx_updated)
    print(f"前端数据已重写到 {web}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
