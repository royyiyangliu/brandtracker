# brandtracker

每周爬取奢侈品牌**各国官网**的商品价格（按当地货币、区分商品型号/参考号），用实时汇率换算成**人民币 (CNY)**，方便对比同一款商品在全球哪个市场最便宜。

首个品牌：**宝诗龙 Boucheron — Quatre 系列**。

## 它是怎么工作的

```
config/<brand>.yaml   各国 locale / 货币 / 列表页 URL
        │
   src/scraper.py      Playwright 驱动真实 Chromium 抓列表页 → 商品(参考号/名称/当地价)
        │
   src/fx.py           拉取实时汇率，换算成 CNY
        │
   src/storage.py      SQLite 落库（保留每周历史，可看趋势）
        │
   src/report.py       按参考号跨国对比 → 生成 Markdown 表格，标出最便宜市场
        │
   output/latest.md    最新对比结果
```

**跨国匹配键**：每个商品详情页 URL 里都带参考号（如 `JRG03330`），同一参考号 = 全球同款，用它把各国价格对齐。

## 当前覆盖市场

| 国家 | locale | 货币 | 状态 |
|---|---|---|---|
| 美国 US | `us` | USD | ✅ |
| 新加坡 SG | `sg_en` | SGD | ✅ |
| 香港 HK | `hk_en` | HKD | ✅ |
| 日本 JP | `ja_jp` | JPY | ✅ |
| 韩国 KR | `ko` | KRW | ✅ |
| 法国 FR | `fr_fr` | EUR | ✅ |
| 中国 CN | `boucheron.cn` | CNY | ⏳ 待接入（见下） |

最新对比见 [`output/latest.md`](output/latest.md)。

## 本地运行

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
xvfb-run -a python -m src.run config/boucheron.yaml   # 无显示环境用 xvfb-run
```

## 每周自动运行

`.github/workflows/weekly.yml` 每周一 02:00 UTC 触发，跑完把更新后的
`data/prices.db` 与 `output/latest.md` 提交回仓库。也可在 Actions 页手动触发。

## 已知约束 / 待办

- **反爬**：官网由 Akamai Bot Manager 保护，普通 HTTP 请求返回 403；本项目用真实
  Chromium 指纹绕过，目前无需代理。若 GitHub Actions 的数据中心 IP 被拦，需接入
  住宅代理（在 `scraper.py` 的 `launch`/`new_context` 中加 `proxy=` 并用仓库 Secret 注入）。
- **中国大陆**：`boucheron.cn` 对非中国 IP 返回 403，需中国本地住宅代理才能抓，已在
  config 中 `enabled: false` 留作 TODO。
- **价格口径**：抓的是各国官网显示价（部分市场如 SG/HK 经 Global-e 跨境，价格可能含
  跨境加价，未必等于当地门店真实零售价）。
- **品类范围**：目前只抓 Quatre 系列列表页（戒指为主）。扩展到项链/手镯或其他品牌，
  只需新增/扩充 `config/*.yaml`。高级珠宝通常不在线标价，无法抓取。
- **汇率**：使用 open.er-api.com 免费接口，每次运行抓一次并随数据存档。
