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
└── output/latest.md           # 最新对比报告
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
- `.github/workflows/weekly.yml`：`schedule`（每周一 02:00 UTC）+ `workflow_dispatch` + `push`（到 main，`paths-ignore: data/** output/**` 防止回写自触发）。`permissions: contents: write` 让跑完的数据能 `git push` 回 main。`timeout-minutes: 45`。
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

## 11. 当前数据快照（2026-06-14）

7 国 × 7 珠宝品类，**540 个不同货号、约 3700 条观测、每国约 522 件有价**。
规律：**日本几乎全线最便宜，中国本土最贵**，同款价差约 26–33%；少数高端件（部分发饰/胸针）美国最便宜。
每国件数高度一致（戒指≈183、项链≈152、耳环 99、手链 86、胸针 6、发饰 5、脚链 2），反证翻页抓全了。
