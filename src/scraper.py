"""Playwright-based scraper.

Boucheron sits behind Akamai Bot Manager (and boucheron.cn behind Alibaba ESA):
plain HTTP clients get blocked, but a real headless Chromium is served HTTP 200.
We drive Chromium, let each category landing page lazy-load, and read product
tiles from the DOM.

Category is NOT parsed from the page — it is declared in config. We scrape one
category landing page at a time, so every product found inherits that page's
category label. The cross-country join key is the product reference (e.g.
JRG03330), embedded in every market's product-detail URL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Reference like JRG03330 / JNE00123 (jewellery category letters + digits).
REF_RE = re.compile(r"j[a-z]{2}\d{4,6}", re.IGNORECASE)

# Per-currency price patterns. The currency is known from config, so we only
# pull out the numeric part; separators are stripped to an integer afterwards.
# innerText flattens nbsp/narrow-nbsp to ASCII spaces, hence EUR allows spaces.
_PRICE_PATTERNS = {
    "USD": r"\$\s*([\d,]+)",
    "SGD": r"\$\s*([\d,]+)",          # S$6,700
    "HKD": r"\$\s*([\d,]+)",          # HK$36,400
    "JPY": r"[¥￥]\s*([\d,]+)",
    "KRW": r"₩\s*([\d,]+)",
    "EUR": r"([\d ,.]+)\s*€",    # 4 820 €
    "CNY": r"[¥￥]\s*([\d,]+)",
}


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


@dataclass
class Target:
    country: str
    currency: str
    category: str   # Chinese display label
    url: str


def parse_price(text: str, currency: str) -> int | None:
    """Extract the integer local price from a tile's text for a known currency."""
    pat = _PRICE_PATTERNS.get(currency)
    if not pat:
        return None
    # Drop the reference token first so its digits can't contaminate the price.
    cleaned = REF_RE.sub(" ", text)
    m = re.search(pat, cleaned)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def _clean_name(text: str) -> str:
    """Tile text looks like '<name> ref ◌ JRG03330 $4,990 + 8 colors'.
    Keep the part before the reference marker and drop trailing noise."""
    cut = REF_RE.search(text)
    name = text[: cut.start()] if cut else text
    name = re.sub(r"[◌·•]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # drop a trailing reference label ('ref' / 'réf' / 'reference') if present
    name = re.sub(r"\s*\b(ref|r[ée]f|reference)\b\.?$", "", name, flags=re.IGNORECASE)
    return name.strip()


def build_targets(config: dict) -> list[Target]:
    """Expand the brand config into one Target per (country, category)."""
    cats = config["categories"]  # canonical_key -> zh label
    targets: list[Target] = []
    for c in config["countries"]:
        base = c["base"].rstrip("/")
        path = c["category_path"].strip("/")
        slugs = c.get("slugs", {})
        for key, label in cats.items():
            slug = slugs.get(key, key)
            url = f"{base}/{path}/{slug}.html"
            targets.append(Target(c["code"], c["currency"], label, url))
    return targets


# The category grid (Magento + Hyvä + Amasty Shopby) shows only 8 items per
# page and loads the rest via a "see more" button that fetches
# `?p=N&isAjax=true` — returning JSON {"products": "<grid html>"}. We replicate
# that inside the established session: page through every p until exhausted,
# parsing each partial. This captures the full category (e.g. 183 rings) in one
# pass instead of the first 8.
_PAGINATE_JS = r"""
async (base) => {
    const origin = location.origin;
    const seen = new Map();
    const collect = (html) => {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        let added = 0;
        for (const a of doc.querySelectorAll("a[href*='-j']")) {
            let href = a.getAttribute('href') || '';
            const m = href.match(/j[a-z]{2}\d{4,6}/i);
            if (!m) continue;
            const ref = m[0].toUpperCase();
            if (seen.has(ref)) continue;
            if (href.startsWith('/')) href = origin + href;
            const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
            if (!text) continue;
            seen.set(ref, { ref, href, text });
            added++;
        }
        return added;
    };
    let page = 1, empty = 0;
    while (page <= 80) {
        const url = base + (base.includes('?') ? '&' : '?') + 'p=' + page + '&isAjax=true';
        let html = '';
        try {
            const r = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const ct = r.headers.get('content-type') || '';
            const body = await r.text();
            html = ct.includes('json') ? (JSON.parse(body).products || '') : body;
        } catch (e) { break; }
        if (collect(html) === 0) { if (++empty >= 2) break; } else empty = 0;
        page++;
    }
    return [...seen.values()];
}
"""


def scrape_page(page, brand: str, target: Target) -> list[Product]:
    # Navigate once to establish the session/cookies, then paginate via fetch.
    page.goto(target.url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2000)
    raw = page.evaluate(_PAGINATE_JS, target.url)
    return [
        Product(
            brand=brand,
            category=target.category,
            country=target.country,
            currency=target.currency,
            ref=t["ref"],
            name=_clean_name(t["text"]),
            local_price=parse_price(t["text"], target.currency),
            url=t["href"],
        )
        for t in raw
    ]


def scrape_brand(config: dict) -> list[dict]:
    """Scrape every (country, category) target; returns list of dicts."""
    brand = config["brand"]
    by_country: dict[str, dict] = {c["code"]: c for c in config["countries"]}
    targets = build_targets(config)
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        # One context per country (right language headers, isolated cookies).
        for code, cfg in by_country.items():
            ctx = browser.new_context(
                user_agent=UA,
                ignore_https_errors=True,
                viewport={"width": 1366, "height": 900},
                locale=cfg.get("browser_locale", "en-US"),
                extra_http_headers=(
                    {"Accept-Language": cfg["accept_language"]}
                    if cfg.get("accept_language")
                    else {}
                ),
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = ctx.new_page()
            for t in (t for t in targets if t.country == code):
                try:
                    items = scrape_page(page, brand, t)
                    priced = sum(1 for i in items if i.local_price is not None)
                    print(f"  [{code}/{t.category}] {len(items)} products, {priced} priced")
                    results.extend(asdict(i) for i in items)
                except Exception as e:
                    print(f"  [{code}/{t.category}] ERROR {type(e).__name__}: {str(e)[:90]}")
            ctx.close()
        browser.close()
    return results
