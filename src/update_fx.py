"""Daily FX-only refresh (no scraping).

抓取是每周一次的，但汇率每天都在变。本脚本只做汇率更新：
取数据库里**最近一次抓取**的观测，用今天的实时汇率重新换算 CNY 价，
写回数据库，然后重新生成报告与前端 JSON。

- 品牌价格数据（local_price / run_ts）保持不变 —— 只有 cny_price 与汇率快照时间更新。
- 因此前端「品牌价格数据更新」时间不变，「汇率更新」时间会刷新为今天。

用法：
    python -m src.update_fx
"""
from __future__ import annotations

from . import export_web, fx, report, storage


def main() -> int:
    conn = storage.connect()
    run_ts = storage.latest_run_ts(conn)
    if not run_ts:
        print("数据库中没有任何抓取记录，先跑一次抓取再刷新汇率。")
        return 1

    print(f"== 汇率刷新，基于最近一次抓取 run {run_ts} (UTC) ==")
    rates = fx.fetch_rates_to_cny()
    fx_updated = rates.get("updated", "?")

    # 用今天的汇率重新换算最近一次抓取的每条观测。
    rows = storage.load_run(conn, run_ts)
    for r in rows:
        cny = fx.to_cny(r["local_price"], r["currency"], rates)
        conn.execute(
            "UPDATE observations SET cny_price = ? "
            "WHERE run_ts = ? AND country = ? AND ref = ?",
            (cny, run_ts, r["country"], r["ref"]),
        )
    conn.commit()
    print(f"已用最新汇率重算 {len(rows)} 条观测的 CNY（汇率更新于 {fx_updated}）。")

    # 重新生成报告与前端数据（run_ts 不变，只换汇率快照）。
    brand = rows[0]["brand"] if rows else "?"
    stored = storage.load_run(conn, run_ts)
    md = report.render_markdown(run_ts, brand, fx_updated, stored)
    path = report.write_report(md, run_ts)
    print(f"报告已重写到 {path}")

    web = export_web.export(conn, run_ts, fx_updated)
    print(f"前端数据已重写到 {web}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
