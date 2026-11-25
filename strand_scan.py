#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Strand Funding Desk (CLI)
-------------------------
Cross-exchange perpetual funding spreads for EEA-accessible venues:
OKX, Kraken Futures, optional Bybit.

Rates are normalized to hourly, then to APR = hourly * 24 * 365.
APR is conditional: only if the snapshot keeps printing. Not a forecast.
Not financial advice. Two margin accounts can liquidate one leg.

Usage:
  python strand_scan.py
  python strand_scan.py --bybit --min-apr 10 --top 5
  python strand_scan.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

OKX = "https://www.okx.com"
KRAKEN = "https://futures.kraken.com"
BYBIT = "https://api.bybit.com"

ALIAS = {"XBT": "BTC", "XDG": "DOGE"}
NOT_A_COIN = {
    "USDT", "USDC", "USD", "EUR", "GBP", "DAI", "USDE", "USDS",
    "FDUSD", "TUSD", "EURC", "PYUSD", "USDG",
}
HOURLY_SANITY = 0.01  # drop |hourly| above 1%/h
KRAKEN_HOURLY_CAP = 0.0025  # Kraken contract cap ~0.25%/h


@dataclass
class Leg:
    venue: str
    symbol: str
    interval_hours: float
    rate_per_interval: float
    hourly: float
    apr: float
    mark_per_coin: float
    volume_usd: float
    oi_usd: float
    next_funding_ms: int | None
    predicted_hourly: float | None


@dataclass
class Opportunity:
    asset: str
    long: Leg
    short: Leg
    spread_apr: float
    predicted_spread_apr: float | None
    unstable: bool
    payback_days: float | None
    basis_bps: float | None
    round_trip_fee: float


def get_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "strand-desk/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value == value:
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def canonical_base(raw: str) -> tuple[str, float] | None:
    upper = re.sub(r"[^A-Z0-9]", "", raw.upper())
    aliased = ALIAS.get(upper, upper)
    if not aliased or aliased in NOT_A_COIN:
        return None
    m = re.match(r"^(1000000|100000|10000|1000)([A-Z][A-Z0-9]{1,14})$", aliased)
    if m:
        asset = ALIAS.get(m.group(2), m.group(2))
        if asset in NOT_A_COIN:
            return None
        return asset, float(m.group(1))
    if not re.match(r"^[A-Z0-9]{2,15}$", aliased) or re.match(r"^[0-9]+$", aliased):
        return None
    return aliased, 1.0


def hourly_from_interval(rate: float, hours: float) -> float | None:
    if hours <= 0:
        return None
    return rate / hours


def apr_from_hourly(hourly: float) -> float:
    return hourly * 24 * 365


def interval_from_gap_ms(from_ms: float, to_ms: float) -> float | None:
    if to_ms <= from_ms:
        return None
    hours = round((to_ms - from_ms) / 3_600_000)
    if hours in (1, 2, 4, 8):
        return float(hours)
    return None


def payback_days(spread_apr: float, round_trip_fee: float) -> float | None:
    if spread_apr <= 0 or round_trip_fee < 0:
        return None
    daily = spread_apr / 365
    if daily <= 0:
        return None
    return round_trip_fee / daily


def basis_bps(long_mark: float, short_mark: float) -> float | None:
    if long_mark <= 0 or short_mark <= 0:
        return None
    mid = (long_mark + short_mark) / 2
    return ((long_mark - short_mark) / mid) * 10_000


def leg_sane(leg: Leg) -> bool:
    if abs(leg.hourly) > HOURLY_SANITY:
        return False
    if leg.mark_per_coin <= 0:
        return False
    if leg.interval_hours not in (1, 2, 4, 8):
        return False
    return True


# --- venue loaders -----------------------------------------------------------

def load_okx() -> dict[str, Leg]:
    instruments = get_json(f"{OKX}/api/
... 
