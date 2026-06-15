"""Cartier scraper adapter.

Cartier .com 是 Salesforce Commerce Cloud（SFCC）；cartier.cn 是另一套中国特供平台。
这里的 Akamai 比宝诗龙严：**必须用真实 Chrome（channel="chrome"）**，不能用 Playwright
自带 Chromium，否则 JP/SG/HK/FR 店面返回 403。零代理。详见 CLAUDE.md §14。

每国：
- SFCC（US/SG/HK/JP/FR/KR）：先 goto 该国落地页热身（建立 Akamai 会话），再在页面内
  fetch 品类网格端点 Sites-<site>-Site/<locale>/Search-UpdateGrid?cgid=<cgid>&...&sz=400，
  解析返回的 tile HTML。
- CN（cartier.cn）：热身首页后，逐个打开 /jewellery/collection/all-* 页，滚动到货号数
  稳定，解析 .works_* 商品卡。

跨国 join 键是货号 CR[A-Z]\\d{7}（每个 .com 详情页 URL 都含）。cartier.cn 的货号是它去掉
"CR" 前缀（B4247600 == CRB4247600），故给 CN 货号补 "CR" 对齐。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# 货号：CR + 一个品类字母 + 7 位数字（如 CRB4084600）。
REF_RE = re.compile(r"CR[A-Z]\d{7}")

# 价格元素文本已与名称隔离，按币种取符号旁的第一段数字、再 strip 成整数。
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
    """价格文本（如 '$30,300' / '1 234 €' / '￥184,000'）→ 整数。"""
    if not text:
        return None
    sym = _PRICE_SYM.get(currency)
    if not sym:
        return None
    m = re.search(sym + r"\s*([\d.,\s]+)", text)        # 符号在数字前（多数币种）
    if not m and currency == "EUR":
        m = re.search(r"([\d.,\s]+)\s*€", text)          # 欧元常在数字后
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


# ── SFCC：在页面内 fetch 网格端点，解析 tile ──────────────────────────────
_SFCC_GRID_JS = r"""
async (url) => {
    let r;
    try { r = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }); }
    catch (e) { return { error: String(e) }; }
    const html = await r.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const out = [], seen = new Set();
    for (const tile of doc.querySelectorAll('.product-tile')) {
        let ref = null, href = '';
        for (const a of tile.querySelectorAll('a[href]')) {
            const h = a.getAttribute('href') || '';
            const m = h.match(/CR[A-Z]\d{7}/);
            if (m) { ref = m[0]; href = h; break; }
        }
        if (!ref || seen.has(ref)) continue;
        seen.add(ref);
        const nameEl = tile.querySelector('.product-tile__name, .pdp-link, .product-name, [class*="name"]');
        const priceEl = tile.querySelector('.price');
        out.push({
            ref, href,
            name: (nameEl ? nameEl.textContent : '').replace(/\s+/g, ' ').trim(),
            price: (priceEl ? priceEl.textContent : '').replace(/\s+/g, ' ').trim(),
        });
    }
    return { status: r.status, items: out };
}
"""


def _scrape_sfcc(page, brand, c, cats, cgids) -> list[dict]:
    code, site, locale, cur = c["code"], c["site"], c["locale"], c["currency"]
    page.goto(c["landing"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    rows: list[dict] = []
    for key, label in cats.items():
        cgid = cgids.get(key)
        if not cgid:
            continue
        url = (
            f"https://www.cartier.com/on/demandware.store/Sites-{site}-Site/{locale}"
            f"/Search-UpdateGrid?cgid={cgid}&prefn1=sapIsVisibleWeb&prefv1=true&start=0&sz=400"
        )
        try:
            res = page.evaluate(_SFCC_GRID_JS, url)
        except Exception as e:
            print(f"  [{code}/{label}] grid 异常 {type(e).__name__}: {str(e)[:70]}")
            continue
        if not res or "items" not in res:
            print(f"  [{code}/{label}] grid 失败: {res}")
            continue
        items = res["items"]
        for it in items:
            href = it["href"]
            if href.startswith("/"):
                href = "https://www.cartier.com" + href
            rows.append(asdict(Product(
                brand, label, code, cur, it["ref"],
                _clean_name(it["name"]), parse_price(it["price"], cur), href,
            )))
        priced = sum(1 for it in items if parse_price(it["price"], cur) is not None)
        print(f"  [{code}/{label}] {len(items)} products, {priced} priced")
    return rows


# ── cartier.cn：滚动加载 + 解析 .works_* 卡片 ─────────────────────────────
_CN_COUNT_JS = r"""
() => new Set([...document.querySelectorAll("a[href^='/creation/']")]
    .map(a => (a.getAttribute('href') || '').match(/\/creation\/([A-Z]\d{6,8})/))
    .filter(Boolean).map(m => m[1])).size
"""

_CN_PARSE_JS = r"""
() => {
    const out = [], seen = new Set();
    for (const a of document.querySelectorAll("a[href^='/creation/']")) {
        const m = (a.getAttribute('href') || '').match(/\/creation\/([A-Z]\d{6,8})/);
        if (!m || seen.has(m[1])) continue;
        let card = a;
        for (let i = 0; i < 6 && card.parentElement; i++) {
            if (card.querySelector && card.querySelector('.works_price')) break;
            card = card.parentElement;
        }
        const pe = card.querySelector ? card.querySelector('.works_price') : null;
        const ne = card.querySelector ? card.querySelector('.works_name') : null;
        const name = ((ne ? ne.textContent : '') || a.getAttribute('title') || '')
            .replace(/\s+/g, ' ').trim();
        if (!name && !pe) continue;   // 跳过没有信息块的纯图片重复链接
        seen.add(m[1]);
        out.push({
            ref: m[1], name,
            price: (pe ? pe.textContent : '').replace(/\s+/g, ' ').trim(),
            href: a.getAttribute('href'),
        });
    }
    return out;
}
"""


def _scrape_cn(page, brand, c, cats, cn_slugs) -> list[dict]:
    code, cur, base = c["code"], c["currency"], c["base"].rstrip("/")
    page.goto(c["landing"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    rows: list[dict] = []
    for key, label in cats.items():
        slug = cn_slugs.get(key)
        if not slug:
            continue
        try:
            page.goto(f"{base}/{slug}", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  [{code}/{label}] goto 失败 {type(e).__name__}: {str(e)[:60]}")
            continue
        page.wait_for_timeout(3500)
        # 滚动到货号数稳定（连续 3 轮不增即停）。
        prev, stable = -1, 0
        for _ in range(40):
            n = page.evaluate(_CN_COUNT_JS)
            if n == prev:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable, prev = 0, n
            page.mouse.wheel(0, 6000)
            page.wait_for_timeout(900)
        items = page.evaluate(_CN_PARSE_JS)
        for it in items:
            rows.append(asdict(Product(
                brand, label, code, cur,
                "CR" + it["ref"],                       # 补 CR 前缀对齐 .com
                _clean_name(it["name"]), parse_price(it["price"], cur),
                "https://www.cartier.cn" + it["href"],
            )))
        priced = sum(1 for it in items if parse_price(it["price"], cur) is not None)
        print(f"  [{code}/{label}] {len(items)} products, {priced} priced")
    return rows


# ── AEM/Algolia（SG/HK/JP/FR）：这些国家已迁到 Adobe AEM + Algolia ──────────
# 与 US/KR 的 SFCC 不同：商品列表来自 Algolia（getindexProductsInAlgolia.<index>.
# _collections:<collection>.<limit>.json，hit 含 globalReference=CRB… + 名称），
# 价格来自 <品类页路径>.productinfo.<ref-ref-…>.json（variants[].priceValue）。
# 各国索引名 / 集合 id 不同，故运行时拦截品类页自己发的这两个请求 URL（不硬编码）。
_FETCH_JSON_JS = r"""
async (u) => {
    try {
        const r = await fetch(u, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        return await r.json();
    } catch (e) { return null; }
}
"""


def _scrape_aem(page, brand, c, cats, aem_slugs) -> list[dict]:
    code, cur = c["code"], c["currency"]
    base = c["landing"].rstrip("/")
    page.goto(base, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    rows: list[dict] = []
    for key, label in cats.items():
        slug = aem_slugs.get(key, key)
        cat_url = f"{base}/jewellery/{slug}/"
        # productinfo 路径 = 品类页路径 + ".productinfo.<refs>.json"，直接构造（不依赖拦截，
        # 否则价格懒加载的品类页会捕获不到 → 0 价）。只拦截 Algolia 列表（含各国索引/集合名）。
        prodinfo_base = cat_url.rstrip("/")
        cap = {"algolia": None}

        def handler(req):
            u = req.url
            if "getindexProductsInAlgolia" in u and not cap["algolia"]:
                cap["algolia"] = u

        page.on("request", handler)
        try:
            r = page.goto(cat_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
        except Exception as e:
            page.remove_listener("request", handler)
            print(f"  [{code}/{label}] goto 失败 {type(e).__name__}: {str(e)[:60]}")
            continue
        page.remove_listener("request", handler)
        if not cap["algolia"]:
            print(f"  [{code}/{label}] 未捕获 Algolia 接口 (status={r.status if r else '?'})")
            continue

        # 全量列表（把 limit 调到 1000）
        algolia_url = re.sub(r"\.\d+\.json$", ".1000.json", cap["algolia"])
        data = page.evaluate(_FETCH_JSON_JS, algolia_url) or {}
        hits = data.get("hits", []) if isinstance(data, dict) else []
        names: dict[str, str] = {}
        for h in hits:
            ref = h.get("globalReference")
            if not ref or not REF_RE.fullmatch(ref):
                continue
            # shortDescription 往往只是材质（"イエローゴールド"），用更完整的字段。
            name = (
                h.get("description")
                or h.get("shortDescriptionCommunication")
                or " ".join(filter(None, [h.get("collectionProductLine"), h.get("productType")]))
                or h.get("shortDescription")
                or ""
            )
            names.setdefault(ref, name)

        # 价格：productinfo 按批取（variants[].priceValue 取最小，即「起」价）
        prices: dict[str, int] = {}
        if names:
            refs = list(names)
            for i in range(0, len(refs), 20):
                batch = refs[i:i + 20]
                purl = prodinfo_base + ".productinfo." + "-".join(batch) + ".json"
                pdata = page.evaluate(_FETCH_JSON_JS, purl) or {}
                if not isinstance(pdata, dict):
                    continue
                for ref, info in pdata.items():
                    if not isinstance(info, dict):
                        continue
                    car = (info.get("additionals") or {}).get("car") or {}
                    cands: list[int] = []
                    # 多尺寸款：价格在 variants[]
                    for v in (car.get("variants") or []):
                        pv = v.get("priceValue")
                        if isinstance(pv, (int, float)) and pv:
                            cands.append(int(pv))
                        elif v.get("formattedPrice"):
                            pp = parse_price(v["formattedPrice"], cur)
                            if pp:
                                cands.append(pp)
                    # 单品款（如耳环，variants 为空）：价格在顶层 formattedPrice
                    if not cands:
                        pv = info.get("priceValue")
                        if isinstance(pv, (int, float)) and pv:
                            cands.append(int(pv))
                        elif info.get("formattedPrice"):
                            pp = parse_price(info["formattedPrice"], cur)
                            if pp:
                                cands.append(pp)
                    if cands:
                        prices[ref] = min(cands)

        for ref, name in names.items():
            rows.append(asdict(Product(
                brand, label, code, cur, ref,
                _clean_name(name), prices.get(ref), cat_url,
            )))
        print(f"  [{code}/{label}] {len(names)} products, {len(prices)} priced")
    return rows


def scrape_brand(config: dict) -> list[dict]:
    """抓全部国家 × 品类，返回 dict 列表。"""
    brand = config["brand"]
    cats = config["categories"]                 # canonical -> 中文 label
    cgids = config.get("category_cgids", {})
    cn_slugs = config.get("cn_slugs", {})
    aem_slugs = config.get("aem_slugs", {})
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled"],
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
                t = c.get("type", "sfcc")
                if t == "cn":
                    results += _scrape_cn(page, brand, c, cats, cn_slugs)
                elif t == "aem":
                    results += _scrape_aem(page, brand, c, cats, aem_slugs)
                else:
                    results += _scrape_sfcc(page, brand, c, cats, cgids)
            except Exception as e:
                print(f"  [{c['code']}] FATAL {type(e).__name__}: {str(e)[:90]}")
            ctx.close()
        browser.close()
    return results
