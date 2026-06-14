"""Playwright-based scraper.

Boucheron sits behind Akamai Bot Manager: plain HTTP clients get HTTP 403, but a
real headless Chromium with a normal browser fingerprint is served HTTP 200. We
therefore drive Chromium, let the collection page lazy-load, and read each
product tile straight from the DOM.

A product tile links to a detail page whose URL embeds the reference
(e.g. .../quatre-classique-xs-ring-jrg03330.html). That reference is the
locale-independent key used to line products up across countries.
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
    collection: str
    country: str
    currency: str
    ref: str
    name: str
    local_price: int | None
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


_TILE_JS = r"""
() => {
    const seen = new Set();
    const out = [];
    for (const a of document.querySelectorAll("a[href*='-j']")) {
        const href = a.getAttribute('href') || '';
        const m = href.match(/(j[a-z]{2}\d{4,6})/i);
        if (!m) continue;
        const ref = m[1].toUpperCase();
        if (seen.has(ref)) continue;
        const text = (a.innerText || '').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        seen.add(ref);
        out.push({ ref, href: a.href, text });
    }
    return out;
}
"""


def scrape_country(page, brand: str, collection: str, country: dict) -> list[Product]:
    url = country["url"]
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)
    # lazy-load: scroll the full page a few times
    for _ in range(8):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(700)

    raw = page.evaluate(_TILE_JS)
    products: list[Product] = []
    for t in raw:
        products.append(
            Product(
                brand=brand,
                collection=collection,
                country=country["code"],
                currency=country["currency"],
                ref=t["ref"],
                name=_clean_name(t["text"]),
                local_price=parse_price(t["text"], country["currency"]),
                url=t["href"],
            )
        )
    return products


def scrape_brand(config: dict) -> list[dict]:
    """Scrape every enabled country for a brand config; returns list of dicts."""
    brand = config["brand"]
    collection = config["collection"]
    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        for country in config["countries"]:
            if country.get("enabled", True) is False:
                print(f"  [{country['code']}] skipped ({country.get('note', 'disabled')})")
                continue
            # Fresh context per country: applies the right language headers and
            # isolates cookies (e.g. Global-e country/currency) between markets.
            ctx = browser.new_context(
                user_agent=UA,
                ignore_https_errors=True,
                viewport={"width": 1366, "height": 900},
                locale=country.get("browser_locale", "en-US"),
                extra_http_headers=(
                    {"Accept-Language": country["accept_language"]}
                    if country.get("accept_language")
                    else {}
                ),
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = ctx.new_page()
            try:
                items = scrape_country(page, brand, collection, country)
                priced = [i for i in items if i.local_price is not None]
                redirected = page.url and "boucheron.cn" not in page.url and country["code"] == "CN"
                flag = " (REDIRECTED off .cn!)" if redirected else ""
                print(f"  [{country['code']}] {len(items)} products, {len(priced)} priced{flag}")
                results.extend(asdict(i) for i in items)
            except Exception as e:  # keep going if one country fails
                print(f"  [{country['code']}] ERROR {type(e).__name__}: {str(e)[:100]}")
            finally:
                ctx.close()
        browser.close()
    return results
