"""Export the latest run to docs/data.json for the static GitHub Pages frontend.

The frontend (docs/index.html) is fully static; it fetches this JSON and does
all filtering/【comparison】 client-side. We therefore ship one product per ref
with each country's local price (amount + currency) and CNY-converted price.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# Display order requested by the product owner.
CNY_ORDER = ["CN", "JP", "KR", "HK", "SG", "US", "FR"]   # 中 日 韩 港 新 美 法
CMP_ORDER = ["JP", "KR", "HK", "SG", "US", "FR"]          # vs 中国

_SIZE_PATTERNS = [
    (r"\bxs\b|extra[ -]small|超小", "XS"),
    (r"\bsmall\b|小号|小型|\bs\b", "小号"),
    (r"\bmedium\b|中号|中型|\bm\b", "中号"),
    (r"\blarge\b|大号|大型|\bl\b", "大号"),
]


def _size(*texts: str) -> str:
    """Best-effort size from the URL slug / names (column is '如有')."""
    blob = " ".join(t for t in texts if t).lower()
    for pat, label in _SIZE_PATTERNS:
        if re.search(pat, blob):
            return label
    return ""


def build_payload(conn: sqlite3.Connection, fx_updated: str) -> dict:
    """合并「每个品牌各自最新一次 run」到同一份 payload。

    各品牌独立运行、run_ts 不同；前端要的是所有品牌的最新数据。按 (brand, ref) 归一，
    避免不同品牌货号偶然撞键。
    """
    from .storage import latest_run_per_brand

    latest = latest_run_per_brand(conn)   # {brand: run_ts}
    rows = []
    for brand, ts in latest.items():
        rows += conn.execute(
            "SELECT * FROM observations WHERE brand = ? AND run_ts = ?", (brand, ts)
        ).fetchall()

    products: dict[tuple, dict] = {}
    for r in rows:
        key = (r["brand"], r["ref"])
        p = products.setdefault(
            key,
            {
                "brand": r["brand"],
                "category": r["category"],
                "ref": r["ref"],
                "name_us": "",
                "name_cn": "",
                "size": "",
                "cny": {},
                "local": {},   # country -> [amount, currency]
                "_urls": [],
            },
        )
        if r["country"] == "US" and r["name"]:
            p["name_us"] = r["name"]
        if r["country"] == "CN" and r["name"]:
            p["name_cn"] = r["name"]
        if r["url"]:
            p["_urls"].append(r["url"])
        if r["cny_price"] is not None:
            p["cny"][r["country"]] = round(r["cny_price"])
        if r["local_price"] is not None:
            p["local"][r["country"]] = [r["local_price"], r["currency"]]

    out = []
    for p in products.values():
        p["size"] = _size(p["name_us"], p["name_cn"], *p["_urls"])
        p.pop("_urls", None)
        out.append(p)

    # stable order: 品牌, 品类, 然后按中国价（无则各国最低 CNY）
    def keyf(p):
        ref = p["cny"].get("CN")
        anchor = ref if ref is not None else (min(p["cny"].values()) if p["cny"] else 0)
        return (p["brand"], p["category"], anchor)

    out.sort(key=keyf)

    return {
        # 各品牌最新抓取时间；brand_updated_utc 取最新者，供前端「数据更新」展示。
        "brand_updated_utc": max(latest.values()) if latest else "",
        "brands_updated": latest,
        "fx_updated": fx_updated,
        "cny_order": CNY_ORDER,
        "cmp_order": CMP_ORDER,
        "count": len(out),
        "products": out,
    }


def export(conn: sqlite3.Connection, fx_updated: str) -> Path:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload(conn, fx_updated)
    path = DOCS_DIR / "data.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    # Standalone: regenerate docs/data.json from the existing DB.
    from . import fx, storage

    conn = storage.connect()
    try:
        fx_updated = fx.fetch_rates_to_cny().get("updated", "")
    except Exception:
        fx_updated = ""
    print("exported", export(conn, fx_updated))
