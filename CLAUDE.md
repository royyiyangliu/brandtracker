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
│   └── boucheron.yaml         # 每个品牌一个 YAML（声明国家、品类、URL 拼装规则）
├── src/
│   ├── scraper.py             # Playwright 抓取 + 分页 + 价格/名称解析
│   ├── fx.py                  # 实时汇率 → CNY
│   ├── storage.py             # SQLite 落库（含旧 schema 自动迁移）
│   ├── report.py              # 跨国对比表（Markdown）
│   └── run.py                 # 编排器：scrape → fx → store → report
├── .github/workflows/weekly.yml  # 每周 cron + push 触发，跑完回写数据
├── data/prices.db             # SQLite（被 CI 用 `git add -f` 回写，保留历史）
├── output/latest.md           # 最新对比报告
└── docs/                      # GitHub Pages 静态前端
    ├── index.html             # 比价页（筛选/搜索/价差统计/显示原价）
    └── data.json              # 由 src/export_web.py 从最新 run 导出（前端数据源）
```

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

## 14. 第二品牌：卡地亚 Cartier（探查完成，待实现）

> 2026-06-15 用真实浏览器 POC 探查完毕，**7 国 × 全部珠宝品类方案已确定、零代理**。下面是写爬虫所需的全部事实。要求同宝诗龙：周度、同 7 国（US/SG/HK/JP/KR/FR/CN）、同样解析项，品类取**全部珠宝品类**。

### 14.1 平台与反爬（关键差异）
- `.com` 站是 **Salesforce Commerce Cloud（SFCC/Demandware）**；`cartier.cn` 是**另一套中国特供平台**（cookie `cartier_session`/`XSRF-TOKEN`/`gdp_*`，非 SFCC）。
- 反爬同是 **Akamai，但比宝诗龙严**。**必须用真实 Chrome**：Playwright `channel="chrome"`（**不是自带 Chromium**——自带的会被 JP/SG/HK/FR 店面判为 bot 返回 403；US 店面宽松，自带的也能过，所以早期误判为"geo 封锁"）。`headless=True` 即可。
- 配套伪装：现代 UA（Chrome/145）+ **完整 client-hints 头**（`sec-ch-ua`/`sec-ch-ua-mobile`/`sec-ch-ua-platform`）+ `accept`/`accept-language` + stealth init（`navigator.webdriver=undefined` 等）。
- **每国先 `goto` 该国落地页热身**（建立 Akamai `ak_bmsc`/`_abck`/`bm_sz` 会话），再打数据接口。
- **零代理**：已从 GitHub 美国 runner 验证 US 直连；从美国 IP + 真实 Chrome 验证 7 国均可。美国 IP 访问非美站会弹 geo「跳回美国」窗口，但走网格接口取数不受影响，无需处理弹窗。
- **CI 注意**：runner 需有 Google Chrome —— 加 `python -m playwright install chrome`（或用 ubuntu runner 预装的 Chrome），不能只 `install chromium`。

### 14.2 珠宝品类（canonical → cgid，US 数量）
| canonical | 中文 | SFCC cgid | CN slug | US 数量 |
|---|---|---|---|---|
| rings | 戒指 | `jewelry_rings` | `all-rings` | 343 |
| necklaces | 项链 | `jewelry_necklaces` | `all-necklaces` | 262 |
| bracelets | 手链/手镯 | `jewelry_bracelets` | `all-bracelets` | 281 |
| earrings | 耳环 | `jewelry_earrings` | `all-earrings` | 187 |
| engagement-rings | 订婚戒 | `jewelry_engagementrings` | （CN 无，404）| 39 |
| wedding-bands | 婚戒/对戒 | `jewelry_weddingbands` | （CN 无，404）| 114 |

CN 只有 4 个品类页（rings/necklaces/bracelets/earrings）；engagement/wedding/brooches 在 .cn 均 404。

### 14.3 SFCC 6 国抓取（US/SG/HK/JP/FR/KR）
- **不抓品类落地页**（KR 等无该 URL），统一直打网格接口（热身后在页面内 `fetch`）：
  `https://www.cartier.com/on/demandware.store/Sites-Cartier{SITE}-Site/{LOCALE}/Search-UpdateGrid?cgid={cgid}&prefn1=sapIsVisibleWeb&prefv1=true&start=0&sz=400`
  - `sz=400` 一次取整品类（最大品类 343 < 400；如某品类超 400 再用 `start` 翻页）。
  - 返回 HTML 网格，用 `DOMParser` 解析 tile。
- **站点 id / locale / 落地页 / 币种**（US、KR 已实测确认；SG/HK/JP/FR 站点 id 按 `Cartier{US地区码}` 类推，实现时各打开一次确认）：
  | 国 | SITE | LOCALE | 落地页(热身) | 币种 |
  |---|---|---|---|---|
  | US | CartierUS | en_US | `/en-us` | USD |
  | SG | CartierSG | en_SG | `/en-sg` | SGD |
  | HK | CartierHK | en_HK | `/en-hk` | HKD |
  | JP | CartierJP | ja_JP | `/ja-jp` | JPY |
  | FR | CartierFR | fr_FR | `/fr-fr` | EUR |
  | KR | CartierKR | ko_KR | `/ko-kr` | KRW |
- **tile 解析**：选择器 `.product-tile`；名称 `.product-tile__name`/`.pdp-link`；价格 `.price`（文本如 `$30,300`）；货号从 `a[href]` 正则 `CR[A-Z]\d{7}`。
- 注：直接打品类落地页时各国 slug 不一（US=`/en-us/jewelry/rings/` 美式 jewelry；SG/HK/JP/FR=`/<loc>/jewellery/rings/` 英式 jewellery；KR 无）。**用网格接口可绕开这些差异**，推荐。

### 14.4 中国 cartier.cn 抓取
- 真实 Chrome 先热身 `https://www.cartier.cn/`。
- 品类页：`https://www.cartier.cn/jewellery/collection/{all-rings|all-necklaces|all-bracelets|all-earrings}`。
- **懒加载**：滚动到底直到货号数稳定（连续几轮不增即停；all-rings 约 372，可能含推荐位——解析时限定在 `works_*` 商品网格内）。底层是 POST `/api/search/getList`（JSON），也可逆向直接调用。
- **卡片解析**：商品卡 `.works_introduce_simple`；名称 `h3.works_name`（或 `a[title]`，如「LOVE Unlimited戒指 18K玫瑰金」）；价格 `.works_price`（`￥21,300`，CNY）；货号从 `a[href^="/creation/"]` 取 `B\d{6,8}`。
- **货号归一（跨国 join 关键）**：CN 货号是 `B…` 格式 = `.com` 货号去掉 `CR` 前缀（CN `B4247600` ↔ .com `CRB4247600`）。落库时给 CN 货号**补 `CR` 前缀**，与其余 6 国对齐到统一 join 键 `CR[A-Z]\d{7}`。

### 14.5 实现计划（按 §9 的 per-brand adapter）
1. 抽 `src/brands/boucheron.py`（搬现有逻辑，行为不变）+ `src/brands/cartier.py`（SFCC 6 国 + cartier.cn）；通用编排（浏览器工厂、fx、storage、report、export_web）留共用层，按 config 品牌名/`scrape_strategy` 分发。浏览器需按 adapter 选 `channel`（宝诗龙自带 chromium / 卡地亚真实 chrome）。
2. 写 `config/cartier.yaml`（品类→cgid+CN slug；7 国 SITE/LOCALE/币种/落地页）。
3. 价格按币种解析复用 `_PRICE_PATTERNS`（USD/SGD/HKD/JPY/KRW/EUR/CNY 都已覆盖）。
4. 跑通：一国一品类 → 7 国全量，核对件数/价格合理性。
5. 自动化：weekly workflow 增加 Cartier（含 `playwright install chrome`）。前端已支持多品牌筛选，基本不改。
- **待用户确认（尚未定）**：卡地亚上线后是否继续保留宝诗龙的周抓取（两个品牌都跑 / 仅卡地亚）。
