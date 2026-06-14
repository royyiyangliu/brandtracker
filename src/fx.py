"""Foreign-exchange conversion to CNY.

Uses the free open.er-api.com endpoint (no API key). Rates are fetched once per
run and snapshotted alongside the price data so historical conversions stay
reproducible.
"""
from __future__ import annotations

import requests

ENDPOINT = "https://open.er-api.com/v6/latest/CNY"


def fetch_rates_to_cny(timeout: int = 20) -> dict:
    """Return {currency: units_per_1_CNY, ...} plus metadata.

    open.er-api with base=CNY gives 'how many <currency> per 1 CNY'. To convert
    an amount in <currency> back to CNY we divide by that rate.
    """
    resp = requests.get(ENDPOINT, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise RuntimeError(f"FX API error: {data.get('error-type', 'unknown')}")
    return {
        "base": data["base_code"],
        "updated": data.get("time_last_update_utc"),
        "rates": data["rates"],  # currency -> per 1 CNY
    }


def to_cny(amount: float | int | None, currency: str, rates: dict) -> float | None:
    """Convert a local amount to CNY using a fetched rate table."""
    if amount is None:
        return None
    if currency == "CNY":
        return float(amount)
    per_cny = rates["rates"].get(currency)
    if not per_cny:
        return None
    return round(amount / per_cny, 2)
