"""Build the global price-comparison report from a stored run.

Pivots products (rows) against countries (columns) showing the CNY-converted
price, marks the cheapest market per product, and writes a Markdown report.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def build_comparison(rows: list) -> tuple[list[str], dict]:
    """Returns (ordered country codes, {ref: {name, prices:{country: cny}, raw:{country:(local,currency)}}})."""
    countries: list[str] = []
    products: dict = {}
    for r in rows:
        c = r["country"]
        if c not in countries:
            countries.append(c)
        ref = r["ref"]
        p = products.setdefault(ref, {"name": r["name"], "cny": {}, "raw": {}})
        if not p["name"] and r["name"]:
            p["name"] = r["name"]
        if r["cny_price"] is not None:
            p["cny"][c] = r["cny_price"]
            p["raw"][c] = (r["local_price"], r["currency"])
    return countries, products


def _fmt_cny(v: float | None) -> str:
    return f"¥{v:,.0f}" if v is not None else "—"


def render_markdown(run_ts: str, brand: str, collection: str,
                    fx_updated: str, rows: list) -> str:
    countries, products = build_comparison(rows)
    lines = [
        f"# {brand} · {collection} — 全球定价对比",
        "",
        f"- 抓取时间 (UTC): `{run_ts}`",
        f"- 汇率快照: `{fx_updated}` （换算为人民币 CNY）",
        f"- 覆盖市场: {', '.join(countries)}",
        "",
        "> 价格取自各国官网商品列表显示价，按参考号 (ref) 跨国匹配同款。"
        "**加粗**为该商品当前最便宜的市场。",
        "",
    ]
    header = ["参考号", "商品"] + countries + ["最低价 / 市场"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    # sort by min CNY price
    def min_price(item):
        vals = [v for v in item[1]["cny"].values()]
        return min(vals) if vals else float("inf")

    for ref, p in sorted(products.items(), key=min_price):
        cny = p["cny"]
        cheapest = min(cny, key=cny.get) if cny else None
        cells = [ref, (p["name"] or "")[:48]]
        for c in countries:
            v = cny.get(c)
            txt = _fmt_cny(v)
            if v is not None and c == cheapest:
                txt = f"**{txt}**"
            cells.append(txt)
        if cheapest:
            spread = ""
            vals = list(cny.values())
            if len(vals) > 1:
                diff = (max(vals) - min(vals)) / min(vals) * 100
                spread = f" (最高贵 {diff:.0f}%)"
            cells.append(f"{cheapest} {_fmt_cny(cny[cheapest])}{spread}")
        else:
            cells.append("—")
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    return "\n".join(lines)


def write_report(content: str, run_ts: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = run_ts.replace(":", "").replace(" ", "_")
    path = OUTPUT_DIR / f"comparison_{safe}.md"
    path.write_text(content, encoding="utf-8")
    # also keep a stable "latest" pointer
    (OUTPUT_DIR / "latest.md").write_text(content, encoding="utf-8")
    return path
