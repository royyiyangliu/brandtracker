# CLAUDE.md — brandtracker

> 给新会话的引导文档。读完即可理解项目结构、数据流、爬虫技术方案，并知道**如何扩展到新品牌**。
> 用中文写注释/对话，代码与现有风格保持一致。

## 1. 项目目标

每周爬取**奢侈品牌各国官网**的商品价格（按当地货币、区分商品型号/货号），用实时汇率换算成**人民币 (CNY)**，让访问者对比同一款商品在全球哪个市场最便宜。

- 首个品牌：**宝诗龙 Boucheron**，珠宝全 7 个品类，7 个市场。
- 跑在 GitHub Actions 上，每周一自动执行，零代理、零成本（详见 §7）。
- 当前产物：`output/latest.md`（人看的对比表）+ `data/prices.db`（结构化历史数据，前端数据源）。

## 2. 仓库结构

```
brandtracker/
├── CLAUDE.md                  # 本文档
├── README.md                  # 面向用户的简介
├── requirements.txt           # playwright / PyYAML / requests
├── config/
│   ├── boucheron.yaml         # 每个品牌一个 YAML（声明国家、品类、URL/接口规则、adapter）
│   └── cartier.yaml
├── src/
│   ├── brands/                # 每品牌一个抓取 adapter（互相独立，bug 锁定在单品牌）
│   │   ├── __init__.py        #   注册表：按 config 的 adapter 字段分发
│   │   ├── boucheron.py       #   Magento/Hyvä：分页 fetch + DOM 解析（自带 Chromium）
│   │   └── cartier.py         #   SFCC + AEM/Algolia + cartier.cn 三路径（真实 Chrome）
│   ├── fx.py                  # 实时汇率 → CNY（共用）
│   ├── storage.py             # SQLite 落库（共用；含 latest_run_per_brand）
│   ├── export_web.py          # 导出 docs/data.json（按品牌各取最新 run 合并）
│   └── run.py                 # 编排器：scrape(adapter) → fx → store → export
├── .github/workflows/
│   ├── weekly.yml             # 每周一 cron + 手动；宝诗龙→卡地亚顺序两步、各自提交
│   └── daily-fx.yml           # 每天（周一除外）只刷新汇率
├── data/prices.db             # SQLite（被 CI 用 `git add -f` 回写，保留历史）
└── docs/                      # GitHub Pages 静态前端
    ├── index.html             # 比价页（筛选/搜索/价差统计/显示原价；支持多品牌）
    └── data.json              # 由 src/export_web.py 合并各品牌最新 run 导出
```
> 注：**已移除人看的 Markdown 报告**（旧 `src/report.py` / `output/latest.md`）——只产出前端 `data.json`。新增品牌 = 加 `src/brands/<name>.py` + `config/<name>.yaml`（见 §15）。

## 3. 数据流（`python -m src.run config/<brand>.yaml`）

```
config/<brand>.yaml
   │  build_targets(): 展开成 (国家 × 品类) 的目标 URL 列表
   ▼
src/scraper.scrape_brand()      每国一个浏览器上下文；逐个品类页分页抓取
   │  → [{brand, category, country, currency, ref, name, local_price, url}]
   ▼
src/fx.fetch_rates_to_cny()     拉一次汇率快照；to_cny() 给每条算 cny_price
   ▼
src/storage.save_observations() 按 run_ts 批量入库（PK: run_ts+country+ref）
   ▼
src/report.render_markdown()    按 ref 跨国对齐，按品类分组，标最便宜市场
   ▼
output/latest.md (+ comparison_<ts>.md)
```

## 4. 爬虫技术方案（核心，务必理解）

### 4.1 反爬：用真实浏览器指纹，不需要代理
- 宝诗龙 `.com` 站在 **Akamai Bot Manager** 后面；裸 HTTP 请求（curl/requests）一律 **403 Access Denied**（响应体含 `errors.edgesuite.net`）。
- **关键发现**：真实 headless **Chromium（Playwright）即使从数据中心 IP（含 GitHub Actions 的美国 runner）也返回 200**。所以**当前阶段无需住宅代理**。
- 轻量伪装：`--disable-blink-features=AutomationControlled` + 隐藏 `navigator.webdriver`。
- 若未来 Akamai 收紧、CI 开始 403：在 `scraper.py` 的 `launch`/`new_context` 加 `proxy=`，用仓库 Secret 注入住宅代理。

### 4.2 中国大陆：boucheron.cn（阿里云 ESA，另一套体系）
- `boucheron.cn` 根路径会把**非中国 IP geo 跳转**到 `boucheron.com`，但**本地化深链接 `…/cn_zh/…` 可直接访问**，价格本就是 CNY。
- 实测从 GitHub 美国 runner 也能直接抓到，**无需中国代理**。隐患：从被识别为中国的 IP 可能仍跳转——`scraper` 未来可加「检测到跳出 .cn 即告警」。

### 4.3 分页：每页只有 8 件，必须翻页（曾漏 95% 数据）
- 品类页是 **Magento + Hyvä 主题 + Amasty Shopby**。网格**每页只渲染 8 件**，其余由「see more creations」按钮异步加载。**单纯滚动不会加载更多。**
- 加载更多的接口：`<category_url>?p=<N>&isAjax=true`，返回 **JSON** `{"products": "<grid html>"}`，每页 8 个新商品。
- 方案（`_PAGINATE_JS`）：先 `goto` 品类页建立会话/cookie，然后**在页面内用 `fetch()` 循环拉 p=1,2,3…**（带 `X-Requested-With: XMLHttpRequest` 头），`JSON.parse(...).products` 取 HTML 再用 `DOMParser` 解析，连续 2 页无新增即停。
- 速度：`.com` 约 7–8s/品类，`.cn` 约 35s/品类。全量 7 国×7 品类约 10–12 分钟。
- ⚠️ `?product_list_limit=N`（Magento 常规改页大小的参数）在本站**被忽略**，不要依赖它。

### 4.4 跨国匹配键：货号 ref
- 每个商品详情页 URL 都含货号，正则 `j[a-z]{2}\d{4,6}`（如 `JRG03330`=戒指, `JBT…`=手链, `JCO…`=耳环, `JPN/JCL…`=项链, `JAL…`=婚戒, `JCP…`=胸针, `JDI/JHB…`=发饰）。
- **同一 ref = 全球同款**，用它做跨国 join。ref 与品类字母前缀相关但**不要用前缀推品类**（见 4.5）。

### 4.5 品类：来自配置，不解析（重要设计决策）
- 我们**按品类落地页抓取**（如 `…/jewelry-category/rings.html`），页面上的每件商品**按定义就属于该品类**——品类标签写在配置里，零解析、零关键词猜测、跨品牌通用。
- 曾经从 URL slug 猜品类（找 `ring`/`bracelet`），已废弃，因为不可泛化。**新增品类/品牌只改 YAML。**
- 商品页**没有 JSON-LD / schema.org 结构化数据**，面包屑也是 JS 渲染的——所以品类只能靠「抓哪个页面」来确定。

### 4.6 价格与名称解析
- 价格：货币已知（来自配置），按 `_PRICE_PATTERNS` 每币种正则提取数字部分，**先去掉 ref token**（防止货号数字污染），再 strip 非数字成整数。欧元用窄空格/普通空格做千分位，故 EUR 正则允许空格。
- 名称：`_clean_name()` 取 ref 之前的文本（各国官网原文，US 英文 / CN 中文等），去掉 `◌` 和尾部 "ref/réf"。**各国名称分别入库**，前端可同时展示中美双语名。

## 5. 配置 schema（`config/<brand>.yaml`）

```yaml
brand: Boucheron
categories:                    # canonical key -> 中文展示标签
  rings: 戒指
  necklaces-pendants: 项链与吊坠
  ...
countries:
  - code: US
    currency: USD
    base: https://www.boucheron.com/us       # URL = {base}/{category_path}/{slug}.html
    category_path: jewelry/jewelry-category
    # browser_locale: 默认 en-US；accept_language: 可选额外头
    # slugs: 仅当该市场本地化了品类 slug 时覆盖（见 FR）
  - code: FR
    currency: EUR
    browser_locale: fr-FR
    base: https://www.boucheron.com/fr_fr
    category_path: joaillerie/joaillerie-par-categorie
    slugs:                     # 法国用法语 slug
      rings: bagues
      necklaces-pendants: colliers-et-pendentifs
      ...
  - code: CN                   # 中国在 boucheron.cn / cn_zh
    currency: CNY
    browser_locale: zh-CN
    accept_language: "zh-CN,zh;q=0.9,en;q=0.8"
    base: https://www.boucheron.cn/cn_zh
    category_path: jewelry/jewelry-category
```

URL 拼装在 `scraper.build_targets()`：`{base}/{category_path}/{slug}.html`，`slug` 默认用 canonical key，`slugs` 里可逐市场覆盖。

**宝诗龙 7 国 locale 速查**：US=`us`, SG=`sg_en`, HK=`hk_en`, JP=`ja_jp`, KR=`ko`, FR=`fr_fr`(法语 slug), CN=`cn_zh`(在 .cn 域名)。

## 6. 数据模型（`data/prices.db`，表 `observations`）

| 列 | 说明 |
|---|---|
| run_ts | 本次运行 UTC 时间戳（一周一批，保留历史看趋势）|
| brand / category / country / currency | 维度；category 是中文标签 |
| ref | 货号，跨国 join 键 |
| name | 该国官网商品名（双语靠不同 country 行）|
| local_price | 当地货币整数价（高级珠宝「洽询」则为 NULL）|
| cny_price | 换算人民币 |
| url | 商品详情页（绝对 URL）|

主键 `(run_ts, country, ref)`。`storage.connect()` 会检测旧 schema（缺 `category` 列）并自动重建——早期数据是一次性的。

## 7. 自动化 / 分支

- **默认/工作分支：`main`**（用户明确要求从 main 跑；早期开发分支 `claude/global-price-comparison-scraper-*` 已不再用）。
- `.github/workflows/weekly.yml`：`schedule`（每周一 02:00 UTC）+ `workflow_dispatch`。**已移除 push 触发**（用户要求：频繁改代码 push 到 main 时不再自动跑全量抓取，避免反复爬；要立即跑就去 Actions 页手动 Run workflow）。`permissions: contents: write` 让跑完的数据能 `git push` 回 main。`timeout-minutes: 45`。
- runner 上：`pip install -r requirements.txt` + `playwright install --with-deps chromium`，然后 `xvfb-run -a python -m src.run config/boucheron.yaml`，最后把 `data/prices.db` + `output/latest.md` 提交回仓库（机器人提交 "weekly price snapshot"）。
- ⚠️ 全新仓库里光有 `workflow_dispatch` 不会注册工作流，需要一次含工作流文件的 `push` 才激活（已激活）。

## 8. 本地开发

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
xvfb-run -a python -m src.run config/boucheron.yaml   # 无显示环境用 xvfb-run
```
- 探查/调试新站点时，写临时 `poc_*.py`（用完删掉，别提交）。
- Playwright 经容器出口代理时 TLS 是自签的，需 `ignore_https_errors=True`（已设）。

## 9. 扩展到新品牌（重要）

**理想情况**：新品牌 = 新建 `config/<brand>.yaml`，代码不动。但要清楚当前 `scraper.py` 有哪些是**宝诗龙特有假设**，新品牌不一定成立：

| 部分 | 是否通用 | 新品牌需注意 |
|---|---|---|
| 「按品类页抓、品类来自配置」 | ✅ 通用思路 | 找到该品牌各国的品类落地页 URL 规律 |
| 每国一个浏览器上下文 + 反爬伪装 | ✅ 通用 | 反爬强度不同，可能需要代理 |
| `REF_RE = j[a-z]{2}\d{4,6}` | ❌ 宝诗龙货号格式 | 新品牌货号/SKU 格式不同，需调整 join 键来源 |
| `_PAGINATE_JS`（`?p=N&isAjax=true` + JSON）| ❌ Magento/Hyvä 特有 | 其他站可能是 SFCC `?start=&sz=`、GraphQL、滚动加载、JSON-LD 等 |
| `_PRICE_PATTERNS` 按币种 | ⚠️ 半通用 | 新币种/新价格格式要加 pattern |
| `_clean_name`（ref 标记切分）| ❌ 依赖宝诗龙文案 | 名称提取逻辑可能要重写 |

**推荐的扩展架构**（做第二个品牌时再落地，避免过早抽象）：
1. 把每个品牌的「抓取策略」抽成 per-brand adapter（如 `src/brands/<brand>.py`），实现统一接口 `scrape_target(page, target) -> list[Product]`；通用编排（上下文管理、fx、storage、report）留在共用层。
2. config 里加 `scrape_strategy:` 字段或按品牌名分发到对应 adapter。
3. 货号/价格/名称解析下放到 adapter；DB schema 与 report 保持品牌无关。

**接新品牌的标准探查流程**（用临时 `poc_*.py`）：
1. 验证反爬：Playwright 能否拿到 200？数据中心 IP 行不行？中国站是否 geo 跳转？
2. 找品类落地页 URL 规律（各国 locale、是否本地化 slug）。
3. 看是否分页/懒加载/「加载更多」，抓出其接口（看 network 请求），实现完整翻页。
4. 找跨国 join 键（货号/SKU，通常在详情页 URL 或 data 属性里）。
5. 确认价格在 DOM 里且各币种格式，补 `_PRICE_PATTERNS`。
6. 写 `config/<brand>.yaml`，跑通一国一品类 → 全量 → 推 main 让 CI 验证。

## 10. 已知约束 / 坑

- **价格口径**：抓的是各国官网**显示价**。部分市场经 **Global-e** 跨境（URL 带 `glCountry/glCurrency`，US 也有 `globale/onpageload` 调用），显示价可能含跨境加价，未必等于当地门店真实零售价。当前用户接受「先抓官网显示价」。
- **高级珠宝**多为「洽询」无价 → `local_price` 为 NULL，不参与跨国对比。
- **汇率**：`open.er-api.com`（免费、无 key、每日更新、中间价）。要更权威换 ECB 或带 key 的源即可（只改 `fx.py`）。
- **报告体量**：`latest.md` 现已 540 行，作为人看的报告偏大——这是该上前端的信号（前端直接读 SQLite，支持按品类/货号筛选、中美双语名）。
- **手表**未纳入（站点有 `watches/watches-by-category/*`，需要可加进 config）。

## 12. 前端（GitHub Pages）

- 纯静态：`docs/index.html`（内联 CSS/JS）fetch `docs/data.json`，全部筛选/对比在浏览器端完成。
- `src/export_web.py` 从最新 run 导出 `docs/data.json`（每个 ref 一条：品牌/品类/双语名/尺寸/货号 + 各国当地原价与 CNY 价）；已接进 `run.py`，每周自动刷新；CI 回写时 `git add docs/data.json`。
- 列顺序：人民币价按 **中日韩港新美法**；价差列（相对中国）按 **日韩港新美法**；勾选「显示原价」追加 7 国当地货币原价列。
- 价格段筛选基于**中国价**（无中国价则用各国最低 CNY）。尺寸是从 slug/名称**尽力解析**（`如有`），与品类的「来自配置」不同，仅作展示。
- **上线方式**：仓库 Settings → Pages → Source = Deploy from a branch → `main` / `/docs`。站点：`https://royyiyangliu.github.io/brandtracker/`。（无 Pages 管理 API/工具，需手动开一次。）

## 11. 当前数据快照（2026-06-14）

7 国 × 7 珠宝品类，**540 个不同货号、约 3700 条观测、每国约 522 件有价**。
规律：**日本几乎全线最便宜，中国本土最贵**，同款价差约 26–33%；少数高端件（部分发饰/胸针）美国最便宜。
每国件数高度一致（戒指≈183、项链≈152、耳环 99、手链 86、胸针 6、发饰 5、脚链 2），反证翻页抓全了。

## 13. 项目协作约定（重要，新会话先读）

- **提交流程**：本项目改动**直接 commit 并 push 到 `main`，不开特性分支、不建 PR**（用户单人开发，明确要求）。git 身份已在本仓库配好：`royyiyangliu` / `yyliu@gyasset.com`。
- **push 不触发抓取**：`weekly.yml` 已移除 push 触发（见 §7）。改代码 push 到 main 不会自动跑全量抓取；要立即跑去 Actions 页手动 Run。
- **项目知识全部记录在本 CLAUDE.md**，不使用本地 memory（用户要求：新 session 靠读本文件即可，本地不留 memory）。任何探查结论/决策/约定都写进这里。
- **临时探查脚本**用 `poc_*.py`，用完即删、不提交（§8）。

## 14. 第二品牌：卡地亚 Cartier（已实现）

> 实现于 `src/brands/cartier.py` + `config/cartier.yaml`。同宝诗龙：周度、7 国
> （US/SG/HK/JP/KR/FR/CN）、6 个珠宝品类、零代理。**关键：卡地亚横跨三套平台**
> （卡地亚在做平台迁移，未来可能再变——抓不到时先确认平台是否换了）。

### 14.1 三套平台（实测）
| 国家 | 平台 | adapter 路径 |
|---|---|---|
| US, KR | SFCC / Demandware（`.com`，老平台）| `_scrape_sfcc` |
| SG, HK, JP, FR | Adobe AEM + Algolia（`.com`，新平台，标志 `/libs/granite`、`/libs/cq`）| `_scrape_aem` |
| CN | cartier.cn 中国特供站（cookie `cartier_session`/`XSRF-TOKEN`/`gdp_*`）| `_scrape_cn` |

### 14.2 反爬（三套通用）
- 同是 **Akamai，但比宝诗龙严**。**必须用真实 Chrome**：Playwright `channel="chrome"`（**不是自带 Chromium**——自带的会被新平台店面判 bot 返回 403；早期误判为"geo 封锁"其实是这个原因）。`headless=True` 即可，**零代理**。
- 配套：现代 UA（Chrome/145）+ **完整 client-hints 头**（`sec-ch-ua` 等）+ `accept-language` + stealth init。每国先 `goto` 落地页热身（建立 Akamai 会话）再打数据接口。
- **CI 注意**：runner 需 `python -m playwright install chrome`，不能只 `install chromium`。

### 14.3 珠宝品类（canonical → 中文 / SFCC cgid / CN slug）
| canonical | 中文 | SFCC cgid（US/KR）| CN slug | AEM slug（SG/HK/JP/FR）|
|---|---|---|---|---|
| rings | 戒指 | `jewelry_rings` | `all-rings` | `rings` |
| necklaces | 项链 | `jewelry_necklaces` | `all-necklaces` | `necklaces` |
| bracelets | 手链 | `jewelry_bracelets` | `all-bracelets` | `bracelets` |
| earrings | 耳环 | `jewelry_earrings` | `all-earrings` | `earrings` |
| engagement-rings | 订婚戒指 | `jewelry_engagementrings` | （CN 无）| `engagement-rings` |
| wedding-bands | 婚戒 | `jewelry_weddingbands` | （CN 无）| `wedding-bands` |

CN 只有 4 个品类页（戒指/项链/手链/耳环）；engagement/wedding 在 .cn 均 404，跳过。

### 14.4 三条抓取路径
- **SFCC（US/KR）**：热身落地页后，在页面内 `fetch` 网格接口
  `…/on/demandware.store/Sites-{site}-Site/{locale}/Search-UpdateGrid?cgid={cgid}&prefn1=sapIsVisibleWeb&prefv1=true&start=0&sz=400`，
  返回 HTML 网格用 `DOMParser` 解析 `.product-tile`（名称 `.product-tile__name`、价格 `.price`、货号 `a[href]` 取 `CR[A-Z]\d{7}`）。站点 id：**仅 US=`CartierUS`/`en_US`、KR=`CartierKR`/`ko_KR` 实测可用**（KR 无品类落地页 URL，但 cgid 端点直接命中）。
- **AEM/Algolia（SG/HK/JP/FR）**：`goto` 品类页 `{landing}/jewellery/{slug}/`，**运行时拦截**该页自己发的两个请求（不硬编码各国索引名/集合 id）：① Algolia 列表 `…/bin/car/getindexProductsInAlgolia.<index>._collections:<collection>.<n>.json`（把末段 limit 改成 `.1000.json` 取全；`hits[].globalReference`=CR 货号、`shortDescription`/`description`=名称、`nbHits`=总数）；② 价格 `<品类页路径>.productinfo.<ref-ref-…>.json`（按 20 个货号一批构造，`additionals.car.variants[].priceValue` 取最小即「起」价）。
- **CN（cartier.cn）**：`goto` `…/jewellery/collection/{cn_slug}`，滚动到货号数稳定，解析卡片（货号 `a[href^="/creation/"]` 取 `B\d{6,8}`、名称 `.works_name`/`a[title]`、价格 `.works_price`）。
- **货号归一（跨国 join 关键）**：统一用 `CR[A-Z]\d{7}`。SFCC/AEM 的 `globalReference` 本就是该格式；**CN 是 `B…`（= .com 去掉 `CR`），落库时补 `CR` 前缀对齐**。

### 14.5 已实现结果与备注
- 实测各国戒指件数：US 343 / SG 229 / HK 236 / JP 204 / KR 333 / FR 234 / CN 372；跨国 join 生效（同一 `CRB…` 在各国出现）。
- **AEM 4 国有价比例偏低（约 57–60%）**：这些市场高级珠宝「洽询」多，属正常（比价只取多国都有价的款）。
- AEM 国家的商品名取 `description` 等字段（`shortDescription` 常只是材质）；但前端只展示 **US 英文名 + CN 中文名**两列，AEM 名仅入库不外显。
- 架构：`src/brands/` per-brand adapter（见 §15）；每品牌独立 config + 独立 run，互不影响。

## 15. 多品牌架构（per-brand adapter）

按用户要求：**抓取逻辑按品牌彻底分开**（脆弱、易出 bug、各品牌不同的部分隔离），
**底座共用**（汇率/数据库/导出，因为要共用一个汇率源、产出一个合并 `data.json`）。

- **adapter**：`src/brands/<name>.py`，实现 `scrape_brand(config) -> list[dict]`，每条含
  `brand/category/country/currency/ref/name/local_price/url`。adapter 自管浏览器（宝诗龙
  自带 Chromium、卡地亚真实 Chrome）、反爬、翻页、解析。某品牌 bug 只影响自己。
- **注册/分发**：`src/brands/__init__.py` 的 `scrape_brand(config)` 按 config 的 `adapter:`
  字段（缺省=品牌名小写）import 对应模块。config 必须写 `adapter: <name>`。
- **共用底座**：`fx.py`（汇率）、`storage.py`（同一 `data/prices.db`，表有 `brand` 列）、
  `export_web.py`（按品牌各取最新 run 合并到一个 `data.json`，键 `(brand, ref)`）。
- **独立运行 / CI**：每品牌单独 `python -m src.run config/<brand>.yaml`，各自 run_ts、各自
  提交。`weekly.yml` 一个 job 内顺序两步（宝诗龙→卡地亚），都用 `if: always()`，任一品牌
  失败不影响另一个已提交的数据。每日汇率 `update_fx.py` 对**每品牌最新 run** 各自重算 CNY。
- **新增品牌**：① 探查（§9 流程，临时 `poc_*.py`）；② 加 `src/brands/<name>.py`；
  ③ 加 `config/<name>.yaml`（含 `adapter: <name>`）；④ 本地 `python -m src.run` 跑通；
  ⑤ `weekly.yml` 加一步抓取 + 一步提交。前端 `data.json` 已多品牌，基本不动。
