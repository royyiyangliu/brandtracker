"""Per-brand scraper registry.

每个品牌一个独立模块（owning 反爬/翻页/解析），互不影响——某品牌的 bug 锁定在
自己的模块里。通用底座（fx / storage / export_web）共用。品牌 config 用 `adapter:`
字段声明用哪个模块（缺省按 brand 名小写）。

新增品牌：在本目录加 `<name>.py`，实现 `scrape_brand(config) -> list[dict]`
（每条含 brand/category/country/currency/ref/name/local_price/url），并在 config
里写 `adapter: <name>`。
"""
from __future__ import annotations

import random
import time
from importlib import import_module


def jitter(lo: float, hi: float) -> None:
    """随机停顿 lo~hi 秒。在国家/品类等导航点之间调用，降低访问频率、规避反爬
    velocity 规则（高频爆发易被 Akamai 临时拉黑，如卡地亚港站 403、宝格丽 SG 503）。"""
    time.sleep(random.uniform(lo, hi))


def get_adapter(config: dict):
    name = (config.get("adapter") or config["brand"]).lower()
    return import_module(f".{name}", __package__)


def scrape_brand(config: dict) -> list[dict]:
    return get_adapter(config).scrape_brand(config)
