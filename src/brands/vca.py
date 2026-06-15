"""Van Cleef & Arpels (梵克雅宝) scraper adapter.

VCA（历峰集团）**7 国统一在 Adobe AEM 上**（中国在 vancleefarpels.cn，同一套），
单一抓取路径，比卡地亚干净。反爬同卡地亚：**必须真实 Chrome**（channel="chrome"），
零代理；每国先 goto 首页热身（建立 Akamai 会话）再打接口。详见 CLAUDE.md §6.3。

每国 × 每品类：
- 列表（含货号+名称）：分页
  `{base}/e-boutique/category/{slug}/_jcr_content/root/searchResultListing/search_result.search.json?page=N&priceCountryCode={cc}`
  → `all.hits`（ES 结构）：`numberOfPages` 给页数，`hits.hits[]._source` 给每个商品，
  其中 `documentTitle`="VCAR… - 名称"（拆出货号与名）、`path` 也含货号。
- 价格：`{base}/home.productinfo.{cc}.REF-<ref-ref-…>.json`（每批 10，接口上限），取 `price`/`formattedPrice`。

货号 join 键 `vca[a-z0-9]{7}`（如 `VCARPME300`），**7 国含 CN 格式一致，无需归一**。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

REF_RE = re.compile(r"vca[a-z0-9]{7}", re.IGNORECASE)

_PRICE_SYM = {
    "USD": r"\$", "SGD": r"\$", "HKD": r"\$",
    "JPY": r"[¥￥]", "KRW": r"₩", "CNY": r"[¥￥]", "EUR": r"€",
}

_HEADERS_BASE = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "sec-ch-ua": '"Chromium";v="145", "Not(A:Brand";v="24", "Google Chrome";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
_STEALTH = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
    "window.chrome={runtime:{}};"
)

_FETCH_JSON_JS = r"""
async (u) => {
    try {
        const r = await fetch(u, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!r.ok) return null;
        return await r.json();
    } catch (e) { return null; }
}
"""


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


def parse_price(text: str, currency: str) -> int | None:
    """formattedPrice（如 '$ 3,450' / '1 234 €'）兜底解析。"""
    if not text:
        return None
    sym = _PRICE_SYM.get(currency)
    if not sym:
        return None
    m = re.search(sym + r"\s*([\d.,\s]+)", text)
    if not m and currency == "EUR":
        m = re.search(r"([\d.,\s]+)\s*€", text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def _search_url(base: str, slug: str, cc: str, page: int) -> str:
    return (f"{base}/e-boutique/category/{slug}/_jcr_content/root/searchResultListing/"
            f"search_result.search.json?page={page}&priceCountryCode={cc}")


def _collect_list(page, base, slug, cc) -> dict[str, str]:
    """翻页拉全品类，返回 {ref(大写): name}。"""
    names: dict[str, str] = {}

    def take(container):
        for it in (container.get("hits") or []):
            src = it.get("_source") or {}
            title = src.get("documentTitle") or ""
            m = REF_RE.search(src.get("path") or "") or REF_RE.search(title)
            if not m:
                continue
            ref = m.group(0).upper()
            if " - " in title:
                name = title.split(" - ", 1)[1]
            else:
                name = src.get("materialInfo") or title
            names.setdefault(ref, _clean_name(name))

    first = page.evaluate(_FETCH_JSON_JS, _search_url(base, slug, cc, 1))
    cont = ((first or {}).get("all") or {}).get("hits") or {}
    take(cont)
    npages = cont.get("numberOfPages") or 1
    for n in range(2, int(npages) + 1):
        j = page.evaluate(_FETCH_JSON_JS, _search_url(base, slug, cc, n))
        c = ((j or {}).get("all") or {}).get("hits") or {}
        if not c.get("hits"):
            break
        take(c)
    return names


def _collect_prices(page, base, cc, refs, currency) -> dict[str, int]:
    prices: dict[str, int] = {}
    for i in range(0, len(refs), 10):     # productinfo 接口每批上限 10 个货号（>10 会 400/403）
        batch = refs[i:i + 10]
        url = f"{base}/home.productinfo.{cc}.REF-" + "-".join(batch) + ".json"
        j = page.evaluate(_FETCH_JSON_JS, url) or {}
        if not isinstance(j, dict):
            continue
        for ref, info in j.items():
            if not isinstance(info, dict):
                continue
            val = None
            pr = info.get("price")
            if pr:
                try:
                    val = int(round(float(pr)))
                except (TypeError, ValueError):
                    val = None
            if not val and info.get("formattedPrice"):
                val = parse_price(info["formattedPrice"], currency)
            if val:
                prices[ref.upper()] = val
    return prices


def scrape_brand(config: dict) -> list[dict]:
    brand = config["brand"]
    cats = config["categories"]            # canonical -> 中文 label
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        for c in config["countries"]:
            code = c["code"]
            cc = c.get("cc", code)
            base = c["base"].rstrip("/")
            currency = c["currency"]
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
            pg = ctx.new_page()
            try:
                pg.goto(f"{base}/home.html", wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(2500)
                # 落地后按实际 origin 重算 base：中国从 .com/cn/zh 跳转到 vancleefarpels.cn，
                # 接口须用跳转后的 .cn 同源地址；其余国家 origin 不变、base 照旧。
                eff_base = pg.evaluate("location.origin") + urlparse(base).path
                for slug, label in cats.items():
                    try:
                        names = _collect_list(pg, eff_base, slug, cc)
                        prices = _collect_prices(pg, eff_base, cc, list(names), currency)
                        cat_url = f"{eff_base}/e-boutique/category/{slug}.html"
                        for ref, name in names.items():
                            results.append(asdict(Product(
                                brand, label, code, currency, ref,
                                name, prices.get(ref), cat_url,
                            )))
                        print(f"  [{code}/{label}] {len(names)} products, {len(prices)} priced")
                    except Exception as e:
                        print(f"  [{code}/{label}] ERROR {type(e).__name__}: {str(e)[:80]}")
            except Exception as e:
                print(f"  [{code}] FATAL {type(e).__name__}: {str(e)[:90]}")
            ctx.close()
        browser.close()
    return results
