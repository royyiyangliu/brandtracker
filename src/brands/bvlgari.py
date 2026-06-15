"""Bvlgari (宝格丽) scraper adapter.

宝格丽属 LVMH。反爬宽松（自带 Chromium 即可、零代理）。横跨两套平台：
- US/SG/HK/JP/KR/FR：`.com` 是 **SFCC Composable / PWA Kit（SCAPI）**。商品+价格走
  Salesforce Commerce API：`…/mobify/proxy/api/search/shopper-search/v1/organizations/
  {ORG}/product-search?siteId={site}&refine=cgid%3D{cgid}&currency=&locale=&limit=&offset=`，
  带 Bearer 鉴权头（PWA 用 guest token；运行时从页面请求里捕获）。响应 `hits[]`：
  `productId`=货号（如 AN860830）、`productName`、`price`、`currency`、`total`。
- CN：`bulgari.cn` 是 **Magento**（见 _scrape_magento）。

品类用数字 cgid（来自 categories 目录树 API，跨 locale 共享）。详见 CLAUDE.md §17。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from urllib.parse import quote

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
ORG = "f_ecom_bcsg_prd"
_SCAPI = (f"https://www.bulgari.com/mobify/proxy/api/search/shopper-search/v1/"
          f"organizations/{ORG}/product-search")

_HEADERS_BASE = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "sec-ch-ua": '"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
_STEALTH = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
)

_FETCH_AUTH_JS = r"""
async (args) => {
    const [u, headers] = args;
    try {
        const r = await fetch(u, { headers });
        if (!r.ok) return { _status: r.status };
        return await r.json();
    } catch (e) { return null; }
}
"""

# 每国热身候选 slug（本地化「戒指」品类页，用于触发并捕获 product-search 模板）。
_DEFAULT_WARM = ["jewellery/rings", "jewelry/rings"]


@dataclass
class Product:
    brand: str
    category: str
    country: str
    currency: str
    ref: str
    name: str
    local_price: int | None
    url: str


def _clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ── SCAPI（US/SG/HK/JP/KR/FR）──────────────────────────────────────────────
def _scrape_scapi(page, brand, c, cats, cgids) -> list[dict]:
    code = c["code"]
    currency = c["currency"]
    base = c["base"].rstrip("/")

    # 在品类页热身：捕获页面真实的 product-search URL 模板 + 头（含 Bearer / correlation-id）。
    # 首页不触发 product-search，必须开本地化「戒指」品类页。
    cap = {"tmpl": None, "headers": None}

    def onreq(r):
        # 只抓品类主列表的 product-search（含 cgid），避开推荐位/einstein 的搜索。
        if "product-search" in r.url and "cgid" in r.url and not cap["tmpl"]:
            cap["tmpl"] = r.url
            cap["headers"] = {k: v for k, v in r.headers.items() if k.lower() in
                              ("authorization", "correlation-id", "sfdc_user_agent",
                               "accept", "accept-language")}

    page.on("request", onreq)
    for slug in (c.get("warm_slugs") or _DEFAULT_WARM):
        if cap["tmpl"]:
            break
        for _ in range(2):                       # goto 偶发 HTTP2 错误，重试一次
            try:
                page.goto(f"{base}/{slug}", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                page.mouse.wheel(0, 3000)        # 滚动触发懒加载的 product-search
                page.wait_for_timeout(3000)
                break
            except Exception:
                page.wait_for_timeout(1500)
    page.remove_listener("request", onreq)

    if not cap["tmpl"]:
        print(f"  [{code}] 未捕获 product-search 模板，跳过")
        return []

    lm = re.search(r"limit=(\d+)", cap["tmpl"])
    limit = int(lm.group(1)) if lm else 24

    rows: list[dict] = []
    for key, label in cats.items():
        cgid = cgids.get(key)
        if not cgid:
            continue
        offset, total, seen = 0, None, set()
        while True:
            u = re.sub(r"cgid%3D\d+", f"cgid%3D{cgid}", cap["tmpl"])
            u = (re.sub(r"offset=\d+", f"offset={offset}", u)
                 if "offset=" in u else u + f"&offset={offset}")
            j = page.evaluate(_FETCH_AUTH_JS, [u, cap["headers"]])
            if not isinstance(j, dict) or "hits" not in j:
                if offset == 0:
                    print(f"  [{code}/{label}] 接口失败: {j}")
                break
            hits = j.get("hits") or []
            total = j.get("total", total)
            for h in hits:
                ref = h.get("productId")
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                price = h.get("price")
                rows.append(asdict(Product(
                    brand, label, code, currency, ref,
                    _clean_name(h.get("productName") or ""),
                    int(round(price)) if isinstance(price, (int, float)) and price else None,
                    f"{base}/product/{ref}",
                )))
            offset += limit
            if not hits or (total is not None and offset >= total):
                break
        priced = sum(1 for r in rows if r["category"] == label and r["country"] == code and r["local_price"])
        print(f"  [{code}/{label}] {len(seen)} products, {priced} priced")
    return rows


# ── CN：bulgari.cn 是 Magento ─────────────────────────────────────────────
_FETCH_JS = r"""
async (u) => {
    try {
        const r = await fetch(u, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!r.ok) return null;
        return await r.json();
    } catch (e) { return null; }
}
"""


def _scrape_magento(page, brand, c, cats) -> list[dict]:
    code = c["code"]
    currency = c["currency"]
    base = c["base"].rstrip("/")
    api = c.get("api_base", f"{base}/rest/zh_cn/V4/catalog/layer")
    cn_paths = c.get("cn_paths", {})

    page.goto(f"{base}/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    rows: list[dict] = []
    for key, label in cats.items():
        path = cn_paths.get(key)
        if not path:
            continue
        page_no, total_pages, seen = 1, None, set()
        while True:
            url = f"{api}?identifier={quote(path, safe='')}&page={page_no}&pageSize=100"
            j = page.evaluate(_FETCH_JS, url)
            data = (j or {}).get("data") or {}
            items = data.get("productItems") or []
            total_pages = data.get("totalPages", total_pages)
            for it in items:
                sku = it.get("sku")
                if not sku or sku in seen:
                    continue
                seen.add(sku)
                pn = it.get("priceNum")
                price = int(round(pn)) if isinstance(pn, (int, float)) and pn else None
                name = it.get("name") or it.get("second_name") or ""
                u2 = it.get("url") or ""
                if u2.startswith("/"):
                    u2 = base + u2
                rows.append(asdict(Product(
                    brand, label, code, currency, sku,
                    _clean_name(name), price, u2 or base,
                )))
            page_no += 1
            if not items or (total_pages and page_no > total_pages):
                break
        priced = sum(1 for r in rows if r["category"] == label and r["local_price"])
        print(f"  [{code}/{label}] {len(seen)} products, {priced} priced")
    return rows


def scrape_brand(config: dict) -> list[dict]:
    brand = config["brand"]
    cats = config["categories"]
    cgids = config.get("category_cgids", {})
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        for c in config["countries"]:
            ctx = browser.new_context(
                user_agent=UA,
                locale=c.get("browser_locale", "en-US"),
                viewport={"width": 1440, "height": 1000},
                extra_http_headers={
                    **_HEADERS_BASE,
                    "accept-language": c.get("accept_language", "en-US,en;q=0.9"),
                },
            )
            ctx.add_init_script(_STEALTH)
            page = ctx.new_page()
            try:
                if c.get("type") == "magento":
                    results += _scrape_magento(page, brand, c, cats)
                else:
                    results += _scrape_scapi(page, brand, c, cats, cgids)
            except Exception as e:
                print(f"  [{c['code']}] FATAL {type(e).__name__}: {str(e)[:90]}")
            ctx.close()
        browser.close()
    return results
