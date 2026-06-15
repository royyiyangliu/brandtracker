# CLAUDE.md — brandtracker

> 给新会话的单一引导文档（项目知识只记在这里，不用本地 memory）。读完即可理解：
> 项目目标、整体架构、两个品牌各自的爬虫技术处理、前端与自动化、以及**如何接入新品牌**。
> 用中文写注释/对话，代码与现有风格保持一致。

---

## 0. 项目协作约定（先读）

- **直接提交到 `main`**：本项目单人开发，所有改动**直接 commit 并 push 到 `main`，不开特性分支、不建 PR**。git 身份已在本仓库配好：`royyiyangliu` / `yyliu@gyasset.com`。
- **push 不触发抓取**：`weekly.yml` 没有 push 触发器。改代码 push 到 main 不会自动跑全量抓取；要立即跑去 GitHub Actions 页手动 Run（或 `gh workflow run "Weekly price scrape"`）。
- **知识写进本文件**：探查结论、决策、约定都更新到 CLAUDE.md，不依赖本地 memory（新 session 靠读本文件即可）。
- **临时脚本**：探查/调试新站点用 `poc_*.py`，用完即删、不要提交。
- **不编造数据**：抓取口径、平台细节务实记录；推测要标注。

## 1. 项目目标

每周爬取**奢侈品牌各国官网**的商品价格（按当地货币、区分货号/型号），用实时汇率换算成**人民币 (CNY)**，让访问者对比同一款商品在全球哪个市场最便宜。

- 当前品牌：**宝诗龙 Boucheron**（珠宝 7 品类）、**卡地亚 Cartier**（珠宝 6 品类）、**梵克雅宝 Van Cleef & Arpels**（珠宝 6 品类）、**宝格丽 Bvlgari**（珠宝 5 品类：戒指/项链/手链/耳环/婚戒）。
- 覆盖 **7 个市场**：美国 US、新加坡 SG、香港 HK、日本 JP、韩国 KR、法国 FR、中国 CN。
- 跑在 GitHub Actions 上，**零代理、零成本**。
- 产物：`docs/data.json`（前端数据源）+ `data/prices.db`（结构化历史）。前端是 GitHub Pages 静态站。
- **已无人看的 Markdown 报告**（旧 `report.py`/`output/latest.md` 已删除）——只产出前端。

## 2. 仓库结构

```
brandtracker/
├── CLAUDE.md                      # 本文档（项目知识源）
├── README.md                      # 面向用户的简介
├── requirements.txt               # playwright / PyYAML / requests
├── config/
│   ├── boucheron.yaml             # 每品牌一个 YAML（国家/品类/URL或接口规则/adapter）
│   ├── cartier.yaml
│   ├── vca.yaml
│   └── bvlgari.yaml
├── src/
│   ├── brands/                    # ← 每品牌一个抓取 adapter（互相独立）
│   │   ├── __init__.py            #   注册表：按 config 的 adapter 字段分发
│   │   ├── boucheron.py           #   Magento/Hyvä（自带 Chromium）
│   │   ├── cartier.py             #   SFCC + AEM/Algolia + cartier.cn（真实 Chrome）
│   │   ├── vca.py                 #   AEM（7 国统一；CN 在 .cn）（真实 Chrome）
│   │   └── bvlgari.py             #   SFCC Composable/SCAPI（6 国）+ bulgari.cn Magento（自带 Chromium）
│   ├── fx.py                      # 实时汇率 → CNY（共用）
│   ├── storage.py                 # SQLite 落库（共用）
│   ├── export_web.py              # 合并各品牌最新 run → docs/data.json（共用）
│   ├── run.py                     # 编排器：scrape(adapter) → fx → store → export
│   └── update_fx.py               # 每日仅刷新汇率（不抓取）
├── .github/workflows/
│   ├── weekly.yml                 # 每周一 cron + 手动；宝诗龙→卡地亚顺序两步、各自提交
│   └── daily-fx.yml               # 每天（周一除外）只刷新汇率
├── data/prices.db                 # SQLite（CI 用 git add -f 回写，保留历史）
└── docs/                          # GitHub Pages 静态前端
    ├── index.html                 # 比价页（内联 CSS/JS；多品牌筛选）
    └── data.json                  # export_web 产出的前端数据源
```

## 3. 架构总览

设计原则（用户要求）：**抓取逻辑按品牌彻底分开，底座共用**。
- 「脆弱、各品牌不同、最容易出 bug」的部分 = 反爬/翻页/解析 → 隔离在各自 adapter，某品牌坏了不影响另一个。
- 「稳定、通用」的部分 = 汇率/数据库/导出 → 共用一套，保证用同一汇率源、产出一个合并 `data.json`。

数据流（每个品牌**单独**跑一次 `python -m src.run config/<brand>.yaml`）：

```
config/<brand>.yaml  ──(adapter 字段)──▶  src/brands/<adapter>.scrape_brand(cfg)
                                              │  每国一个浏览器上下文，按平台抓取
                                              ▼  [{brand,category,country,currency,ref,name,local_price,url}]
                          src/fx.fetch_rates_to_cny()  →  给每条算 cny_price
                                              ▼
                          src/storage.save_observations(run_ts, rows)   # 按 run_ts 批量入库
                                              ▼
                          src/export_web.export()      # 合并所有品牌「各自最新 run」→ docs/data.json
```

- **每个品牌有自己的 `run_ts`**（独立运行，时间戳不同）。数据库表有 `brand` 列。
- `export_web` 不是导出「单个 run」，而是 `storage.latest_run_per_brand()` 取每个品牌各自最新 run，**合并**成一个 `data.json`（产品键 `(brand, ref)`）。
- 因此宝诗龙与卡地亚的成败、时间戳完全解耦：任一品牌跑完都会刷新合并后的 `data.json`。

## 4. 数据模型（`data/prices.db`，表 `observations`）

| 列 | 说明 |
|---|---|
| run_ts | 本次运行 UTC 时间戳（一周一批，保留历史看趋势）|
| brand | 品牌（多品牌共表的关键维度）|
| category | 中文品类标签（**来自配置，非解析**，见 §6）|
| country / currency | 市场与当地币种 |
| ref | 货号，**跨国 join 键**（各品牌格式不同，见 §6）|
| name | 该国官网商品名（双语靠不同 country 行：US 英文、CN 中文）|
| local_price | 当地货币整数价（「洽询」无价则 NULL）|
| cny_price | 换算人民币（每日汇率刷新会就地更新最新 run 的此列）|
| url | 商品详情/列表页 URL |

- 主键 `(run_ts, country, ref)`；索引 `ref`、`run_ts`。
- `storage.connect()` 会检测旧 schema（缺 `category` 列）并重建——早期数据一次性。
- `storage.latest_run_per_brand()` → `{brand: 最新 run_ts}`，供 export / 汇率刷新按品牌取数。

## 5. 共用底座

- **`fx.py`**：`open.er-api.com`（免费、无 key、每日中间价，base=CNY）。`fetch_rates_to_cny()` 拉一次快照；`to_cny(amount, currency, rates)` 换算。要更权威换 ECB/带 key 源即可。
- **`storage.py`**：SQLite 落库 + 上述 helper。品牌无关。
- **`export_web.py`**：合并各品牌最新 run 导出 `docs/data.json`。每条产品含：品牌/品类/品名/尺寸/货号 + 各国当地原价与 CNY 价。
  - **品类跨品牌合并**（`CATEGORY_MERGE`）：`项链与吊坠`(宝诗龙/梵克雅宝)→`项链`、`订婚戒指`(卡地亚)→`婚戒`、`胸针`(宝诗龙/梵克 clips)+`袖扣`(梵克 cufflinks)→`胸针袖扣`，让各品牌同义品类归并。
  - **品名三个字段**：`name_cn`(中国名)、`name_us`(美国名)、`name`(**最优可用品名**，按 `_NAME_PRIORITY` 中文→英文→其他市场回退)。卡地亚很多款不在中/美售卖（`name_cn`/`name_us` 空）但在 SG/HK/FR 有名，故前端默认列用 `name`，避免显示「—」。
  - 列顺序：CNY 按 **中日韩港新美法**，价差列（相对中国）按 **日韩港新美法**。payload 含 `brands_updated`（各品牌抓取时间）、`fx_updated`。
- **`run.py`**：编排单品牌一次运行（scrape→fx→store→export）。
- **`update_fx.py`**：每日仅刷新汇率——对**每个品牌各自最新 run** 用今日汇率重算 `cny_price` 写回，再重新导出 `data.json`。不抓取、不需要浏览器（只用 requests）。`run_ts`/`local_price` 不变，故前端「品牌数据更新」时间不变、「汇率更新」时间刷新。

## 6. 品牌 adapter

**接口**：每个 `src/brands/<name>.py` 实现 `scrape_brand(config) -> list[dict]`，返回的每条含
`brand/category/country/currency/ref/name/local_price/url`。adapter **自管浏览器**（含 `channel`/反爬）、翻页、解析。
`src/brands/__init__.py` 的 `scrape_brand(config)` 按 config 的 `adapter:` 字段（缺省=品牌名小写）import 对应模块。

**共同设计：品类来自配置、不解析**。我们按品类落地页/接口分别抓取，页面上的商品按定义即属该品类——品类标签写在 YAML 里。新增品类/品牌只改配置 + adapter，不靠关键词猜。

### 6.1 宝诗龙 Boucheron（`brands/boucheron.py`）
- 平台：**Magento + Hyvä 主题 + Amasty Shopby**。反爬 **Akamai**（中国站 `boucheron.cn` 是阿里云 ESA）。
- 浏览器：**Playwright 自带 Chromium 即可**（轻量伪装 `--disable-blink-features=AutomationControlled` + 隐藏 `navigator.webdriver`），**零代理**，连 GitHub 美国 runner 也返回 200。
- 货号 join 键：`j[a-z]{2}\d{4,6}`（如 `JRG03330`），嵌在详情页 URL。
- **翻页（关键）**：品类页每页只渲染 8 件，其余靠 `<category_url>?p=N&isAjax=true` 返回 JSON `{"products":"<grid html>"}`。`_PAGINATE_JS` 先 `goto` 建立会话，再在页面内 `fetch` 循环 p=1,2,3…（带 `X-Requested-With`），连续 2 页无新增即停。⚠️ `?product_list_limit=N` 本站被忽略。
- 价格：按币种正则 `_PRICE_PATTERNS`，先去掉 ref token 防数字污染。名称：取 ref 之前的文本。
- 7 品类：戒指/项链与吊坠/手链/脚链/耳环/胸针/发饰。7 国 locale：US=`us`, SG=`sg_en`, HK=`hk_en`, JP=`ja_jp`, KR=`ko`, FR=`fr_fr`(法语 slug), CN=`cn_zh`(在 .cn 域名，深链接直达、价本就是 CNY)。

### 6.2 卡地亚 Cartier（`brands/cartier.py`）
- 反爬 **Akamai，比宝诗龙严**：**必须用真实 Chrome**（Playwright `channel="chrome"`，不是自带 Chromium——自带的会被新平台店面判 bot 返回 403）。`headless=True` 即可，**零代理**。配套：现代 UA（Chrome/145）+ 完整 client-hints 头（`sec-ch-ua` 等）+ `accept-language` + stealth init。**每国先 `goto` 落地页热身**（建立 Akamai `ak_bmsc`/`_abck`/`bm_sz` 会话）再打数据接口。
- 货号 join 键：`CR[A-Z]\d{7}`（如 `CRB4084600`）。**CN 的货号是 `B…`（= .com 去掉 `CR`），落库时补 `CR` 前缀**对齐到统一键。
- 6 品类（canonical→中文/SFCC cgid/CN slug）：rings 戒指 `jewelry_rings` `all-rings`；necklaces 项链 `jewelry_necklaces` `all-necklaces`；bracelets 手链 `jewelry_bracelets` `all-bracelets`；earrings 耳环 `jewelry_earrings` `all-earrings`；engagement-rings 订婚戒指 `jewelry_engagementrings` （CN 无）；wedding-bands 婚戒 `jewelry_weddingbands` （CN 无）。AEM 国家品类 slug = canonical key。
- **横跨三套平台**（卡地亚在做平台迁移，未来可能再变——抓不到时先确认平台是否换了）：

  | 国家 | 平台 | adapter 路径 | 取数方式 |
  |---|---|---|---|
  | US, KR | SFCC/Demandware（老）| `_scrape_sfcc` | 热身后在页面内 `fetch` 网格接口 `…/Sites-Cartier{US/KR}-Site/{en_US/ko_KR}/Search-UpdateGrid?cgid={cgid}&prefn1=sapIsVisibleWeb&prefv1=true&start=0&sz=400`；解析返回 HTML 的 `.product-tile`（名 `.product-tile__name`、价 `.price`、货号 `a[href]` 取 `CR…`）。KR 无品类落地页，但 cgid 端点直接命中。 |
  | SG, HK, JP, FR | Adobe AEM + Algolia（新；标志 `/libs/granite`、`/libs/cq`）| `_scrape_aem` | `goto` 品类页 `{landing}/jewellery/{slug}/`，**运行时拦截**该页发的 Algolia 列表请求拿到各国索引/集合名（不硬编码），把末段 limit 改 `.1000.json` 取全（`hits[].globalReference`=货号、`description`等=名）；价格自行构造 `{品类页路径}.productinfo.<ref-ref-…>.json`（每 20 个一批），取 `additionals.car.variants[].priceValue` 最小值；**单品款（如耳环）variants 为空，价格在顶层 `formattedPrice`**——必须 fallback 到顶层，否则该品类 0 价。 |
  | CN | cartier.cn 中国特供站（cookie `cartier_session`/`XSRF-TOKEN`/`gdp_*`）| `_scrape_cn` | `goto` `…/jewellery/collection/{cn_slug}`，滚动到货号数稳定，解析卡片（货号 `a[href^="/creation/"]` 取 `B…`、名 `.works_name`/`a[title]`、价 `.works_price`）。 |

- 价格解析 `parse_price`：按币种符号取数字（EUR 兼容符号在后）。AEM 的 `formattedPrice`（如 `￥737,000`）也走它。
- 备注：**AEM 4 国有价比例约 57–60%**（这些市场高级珠宝「洽询」多，正常）；**订婚戒指各国仅约 4 款有价**（多为「裸戒托/钻石另议」，真实情况，非 bug）。AEM 国商品名仅入库不外显（前端只显示 US 英文 + CN 中文名）。

### 6.3 梵克雅宝 Van Cleef & Arpels（`brands/vca.py`）
- 反爬同卡地亚（Akamai 系）：**必须真实 Chrome**（`channel="chrome"`，headless），零代理，每国先 goto 首页热身。
- **7 国统一一套 Adobe AEM**（标志 `/libs/granite`、`_jcr_content`、`.rcq`）——比卡地亚干净，单一抓取路径。中国在 `vancleefarpels.cn`（同一套 AEM）。
- 货号 join 键：`vca[a-z0-9]{7}`（如 `VCARPME300`），**7 国含 CN 格式完全一致，无需归一**。
- 6 品类（slug→中文）：rings 戒指 / necklaces-and-pendants 项链与吊坠 / bracelets 手链 / earrings 耳环 / clips 胸针 / cufflinks 袖扣（不含手表）。clips+cufflinks 经 `CATEGORY_MERGE` 与宝诗龙胸针合并为「胸针袖扣」。
- **取数（每国 base = 区域/语言前缀，如 `/us/en`）**：
  - 列表：分页 `{base}/e-boutique/category/{slug}/_jcr_content/root/searchResultListing/search_result.search.json?page=N&priceCountryCode={cc}` → `all.hits`（ES 结构）：`numberOfPages` 给页数、`hits.hits[]._source` 给每件，其中 `documentTitle`=「VCAR… - 名称」拆出货号+名、`path` 也含货号。
  - 价格：`{base}/home.productinfo.{cc}.REF-<ref-ref-…>.json`，取 `price`（"14100.00"）/`formattedPrice`。**接口每批上限 10 个货号**（>10 会 400/403），故按 10 个一批。
- **中国 / `.cn` DNS 坑**：CN 用 `.com/cn/zh` 入口（302 跳转到 `vancleefarpels.cn`），adapter 热身后按落地 `location.origin` 重算 base。**从中国境外 IP 访问 `.cn` 域名 DNS 偶发解析失败**（本机开发时 cartier.cn/vancleefarpels.cn 都时好时坏）——失败时该国本次优雅跳过，不影响其余 6 国；GitHub runner 上 .cn 通常可解析（卡地亚 CN 在 CI 成功过）。

### 6.4 宝格丽 Bvlgari（`brands/bvlgari.py`）
- 反爬最宽松：**自带 Chromium 即可、零代理**（不必真实 Chrome）。
- **横跨两套平台**：
  - US/SG/HK/JP/KR/FR：`.com` 是 **SFCC Composable / PWA Kit（SCAPI）**。商品+价格走 Salesforce Commerce API `…/mobify/proxy/api/search/shopper-search/v1/organizations/f_ecom_bcsg_prd/product-search?siteId=&refine=cgid%3D{cgid}&currency=&locale=&limit=&offset=`，**带 Bearer 鉴权 + correlation-id 头**。首页不触发该接口——必须先开本地化「戒指」品类页热身（`warm_slugs`，slug 各国本地化：US `jewelry/rings`、SG/HK `jewellery/rings`、JP `ジュエリー/リング`、FR `joaillerie/bagues`、KR `주얼리/반지`），**捕获其真实 product-search URL 模板 + 头，之后只替换 cgid + offset 重放分页**（不能从零拼 URL，否则 503）。响应 `hits[]`：`productId`=货号（`AN######`）、`productName`、`price`、`currency`、`total`。
  - CN：`bulgari.cn` 是 **Magento**。商品走 `…/rest/zh_cn/V4/catalog/layer?identifier=jewelry/{slug}&page=N&pageSize=100` → `data.productItems[]`（`sku`=货号、`priceNum`=价、`name`、`url`；`totalPages` 翻页）。无需鉴权。
- 货号 join 键 `AN[0-9]{6}` 级别（如 `AN860830`），**.com 与 CN 的 sku 格式一致，无需归一**。
- 5 品类（canonical→中文）：rings 戒指 / necklaces 项链 / bracelets 手链 / earrings 耳环 / engagement-rings + wedding-bands（都标「婚戒」，自然并入卡地亚的婚戒）。cgid 来自 categories 目录树 API（跨 locale 共享）：rings 241473 / necklaces 241474 / bracelets 241480 / earrings 241475 / engagement-rings 241498 / wedding-bands 245590。CN 只抓 4 主品类（婚戒的 .cn 路径未确认；婚戒跨国对比由 .com 6 国提供）。
- **坑**：本机对 bulgari.com 部分 locale 频繁 `ERR_HTTP2_PROTOCOL_ERROR` / 503（疑似多次探查被限速）——adapter 对每国按 `warm_slugs` 候选重试、捕获不到模板则优雅跳过；SCAPI token 有时效（guest token），单次 run 内有效。
- **KR 是 SCAPI（siteId=KR），HK 才是 SSR**（实测 6/7 国可抓，HK 暂不抓）：
  - **KR 之前误判**：失败是 Playwright 连 `ko-kr` 的 `ERR_HTTP2_PROTOCOL_ERROR`（连接层）所致，不是没店面。`launch` 加 **`--disable-http2`** 后稳定；warm_slug 用韩文 **`주얼리/네크리스`**（반지=戒指、네크리스=项链）。→ US/SG/JP/KR/FR + CN 共 6 国走 SCAPI/Magento 正常。
  - **HK 是「展示型」SSR 站**：`/en-hk/jewellery/rings` 显示价 HK$、HTML 里也有 AN 货号，但**不发 product-search 接口、商品不在 `/product/` 链接里、无内嵌 JSON**——SCAPI 路径取不到。要补 HK 需另写「解析 SSR HTML」的第三条路径（markup 不透明、偏脆）；**用户决定先不爬 HK**。
- **货号归一 `_norm_ref`（重要，否则跨国对不上）**：宝格丽货号两类——字母前缀+数字（`AN852260`）与纯数字（`361995`）；各国接口会加装饰：US SCAPI 带系列前缀（`B-zero1-1-bands-AN852260`）、CN Magento 带变体后缀（`361995-E`）。归一规则：优先抽「字母(2-4)+数字(5+)」否则取纯数字(5+)，SCAPI/Magento 两路径都用。实测修复后 US∩CN 戒指重合 0→90。

## 7. 反爬通用经验（接新品牌先看）

- 很多奢侈品站在 **Akamai Bot Manager** 后面，裸 HTTP（curl/requests）一律 403（响应体常含 `errors.edgesuite.net`）。用 **Playwright 真实浏览器**通常可绕过，且**数据中心 IP（含 GitHub runner）也行**，当前所有品牌**零代理**。
- **自带 Chromium vs 真实 Chrome**：Akamai 严格度因站而异。宝诗龙自带 Chromium 即可；卡地亚部分店面识破自带 Chromium 的指纹 → 必须 `channel="chrome"` + 完整 client-hints 头。**接新品牌先两者都试**。
- **geo/弹窗**：美国 IP 访问非美站可能弹「跳回美国」窗口或 geo 跳转——若走后端接口取数通常不受影响；中国站常把非中国 IP 跳转，但本地化深链接/接口多可直达。
- **若未来某站开始 403**：在该 adapter 的 `launch`/`new_context` 加 `proxy=`，用仓库 Secret 注入住宅代理。

## 8. 前端（GitHub Pages）

- 纯静态：`docs/index.html`（内联 CSS/JS）`fetch ./data.json`，全部筛选/对比在浏览器端完成。
- 功能：品牌/品类筛选（品类已跨品牌合并，见 §5）、品名/货号搜索、价格段筛选（基于中国价，无则各国最低 CNY）、筛选结果均价差大号卡片、勾选「显示原价」追加 7 国当地货币原价列、「显示美国品名」开关（默认隐藏该列）。默认「品名」列用 export 的最优可用名（`name`，中文优先）。
- 展示细节：金额以**万**为单位（人民币价 1 位小数、原价 2 位）；人民币价单元格无 ¥ 符号；最低价高亮绿色；各国价格全为空的行不显示；更新时间显示为**北京时间**（`fmtBeijing` 把 UTC 转 UTC+8）。
- **上线方式**：仓库 Settings → Pages → Source = Deploy from a branch → `main` / `/docs`。站点：`https://royyiyangliu.github.io/brandtracker/`。（无 Pages 管理 API，需手动开一次。）

## 9. 自动化

- **`weekly.yml`**：`schedule`（每周一 02:00 UTC = 北京时间周一 10:00）+ `workflow_dispatch`（手动）。**无 push 触发**。`permissions: contents: write`。一个 job 内：装依赖（`playwright install --with-deps chromium` **和** `playwright install chrome`，卡地亚/梵克需真实 Chrome）→ 顺序四组步骤：**宝诗龙 → 卡地亚 → 梵克雅宝 → 宝格丽**，每组「抓取+提交」都 `if: always()`、各自 `git pull --rebase --autostash` 后 `git add -f data/prices.db docs/data.json` 并 commit/push——**任一品牌失败不影响其他已提交的数据**。`timeout-minutes: 60`。
- **`daily-fx.yml`**：`cron "0 2 * * 0,2,3,4,5,6"`（北京时间周二~周日 10:00，跳过周一因为周一汇率随抓取一起更新）+ 手动。轻量（只装 `requests`、不跑浏览器），跑 `python -m src.update_fx`，回写 `data/prices.db` + `docs/data.json`。
- 合起来：**价格每周一抓一次**，**汇率每天更新一次**（覆盖所有品牌的最新 run）。
- ⚠️ 全新仓库里光有 `workflow_dispatch` 不会注册工作流，需要一次含工作流文件的 push 才激活（已激活）。

## 10. 本地开发

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium   # 宝诗龙
python -m playwright install chrome                  # 卡地亚（真实 Chrome）
python -m src.run config/boucheron.yaml
python -m src.run config/cartier.yaml
# Windows 上加 -X utf8 避免 GBK 编码问题：python -X utf8 -m src.run ...
# 无显示环境（Linux CI）用 xvfb-run -a python -m src.run ...
```
- 调试新站点写临时 `poc_*.py`（用完删）。Playwright 经容器出口代理时 TLS 自签，需 `ignore_https_errors=True`。

## 11. 扩展到新品牌（重要）

新品牌 = 新建 `src/brands/<name>.py` + `config/<name>.yaml`（含 `adapter: <name>`），通用底座不动。

**adapter 要实现的**：`scrape_brand(config) -> list[dict]`，每条含
`brand/category/country/currency/ref/name/local_price/url`；自管浏览器（按需选 `channel`、反爬、代理）、翻页、解析、货号归一。**品类来自配置**。

**标准探查流程**（用临时 `poc_*.py`）：
1. **反爬**：Playwright 自带 Chromium 能否 200？不行就试 `channel="chrome"` + 完整 client-hints 头 + 落地页热身。数据中心 IP 行不行？中国站是否 geo 跳转？
2. **平台识别**：看 cookie / 静态资源 URL / 接口（SFCC=`demandware`、AEM=`/libs/granite`、Magento=`isAjax`、或自有 API）。同品牌不同国家**可能跨多套平台**（卡地亚就是 3 套）——逐国确认。
3. **品类落地页/接口规律**（各国 locale、是否本地化 slug）。
4. **翻页/懒加载机制**（看 network：SFCC `Search-UpdateGrid?start=&sz=`、Algolia、滚动加载、`?p=N` 等），实现完整翻页。
5. **跨国 join 键**（货号/SKU，通常在详情页 URL 或接口字段；注意各国/各平台格式差异，做归一）。
6. **价格在哪、各币种格式**（注意「单品 vs 多变体」价格位置可能不同——卡地亚 AEM 耳环的坑），补 `parse_price`。
7. 写 config + adapter，**先一国一品类 → 全量** 核对件数/价格合理性 → 接进 `weekly.yml`（加抓取+提交两步，需要真实 Chrome 就确认 CI 装了 `playwright install chrome`）→ push main 让 CI 验证。

**注意事项 / 已知坑**：
- 价格是各国官网**显示价**，部分市场经 Global-e/跨境，可能含跨境加价，未必等于当地门店零售价。
- 高级珠宝/订婚戒多为「洽询」无价 → `local_price` NULL，不参与对比（正常，不是 bug）。
- 平台会迁移（卡地亚正在迁），抓不到时**先确认平台/接口是否变了**，再改对应 adapter；其他品牌不受影响。
- 各品牌独立运行/提交，互不拖累——这是架构的核心收益，保持它。

## 12. 当前数据快照（2026-06-15）

- **宝诗龙 540 款 + 卡地亚 1565 款 = 2105 款**（前端 `data.json`）。
- 卡地亚各国戒指件数：US 343 / SG 229 / HK 236 / JP 204 / KR 333 / FR 234 / CN 372；486 款在 ≥5 国有价。
- 规律（宝诗龙）：日本几乎全线最便宜、中国本土最贵，同款价差约 26–33%。
