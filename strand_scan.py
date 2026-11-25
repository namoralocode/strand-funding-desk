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
    instruments = get_json(f"{OKX}/api/v5/public/instruments?instType=SWAP")
    tickers = get_json(f"{OKX}/api/v5/market/tickers?instType=SWAP")
    oi = get_json(f"{OKX}/api/v5/public/open-interest?instType=SWAP")
    marks = get_json(f"{OKX}/api/v5/public/mark-price?instType=SWAP")
    rates = get_json(f"{OKX}/api/v5/public/funding-rate?instId=ANY")

    inst_list = instruments.get("data") or []
    ticker_map = {t["instId"]: t for t in (tickers.get("data") or []) if "instId" in t}
    oi_map = {o["instId"]: o for o in (oi.get("data") or []) if "instId" in o}
    mark_map = {m["instId"]: m for m in (marks.get("data") or []) if "instId" in m}
    rate_map = {r["instId"]: r for r in (rates.get("data") or []) if "instId" in r}

    out: dict[str, Leg] = {}
    for inst in inst_list:
        if inst.get("state") != "live":
            continue
        if inst.get("settleCcy") not in ("USDT", "USDC"):
            continue
        if inst.get("ctType") != "linear":
            continue
        inst_id = inst.get("instId") or ""
        base = inst.get("ctValCcy") or inst.get("baseCcy") or ""
        canon = canonical_base(base)
        if not canon:
            # try instId prefix e.g. BTC-USDT-SWAP
            part = inst_id.split("-")[0] if inst_id else ""
            canon = canonical_base(part)
        if not canon:
            continue
        asset, mult = canon

        rate_info = rate_map.get(inst_id) or {}
        rate = num(rate_info.get("fundingRate"))
        if rate is None:
            continue
        # OKX default 8h; prefer fundingTime gap when available
        interval = 8.0
        ft = num(rate_info.get("fundingTime"))
        nft = num(rate_info.get("nextFundingTime"))
        if ft is not None and nft is not None:
            gap = interval_from_gap_ms(ft, nft)
            if gap:
                interval = gap
        hourly = hourly_from_interval(rate, interval)
        if hourly is None:
            continue

        mark = num((mark_map.get(inst_id) or {}).get("markPx")) or num((ticker_map.get(inst_id) or {}).get("last"))
        if not mark or mark <= 0:
            continue
        mark_per_coin = mark * mult

        vol = num((ticker_map.get(inst_id) or {}).get("volCcy24h")) or 0.0
        # volCcy24h is often quote; if tiny relative to price treat as base*mark
        oi_row = oi_map.get(inst_id) or {}
        oi_contracts = num(oi_row.get("oi")) or 0.0
        ct_val = num(inst.get("ctVal")) or 1.0
        oi_usd = oi_contracts * ct_val * mark

        leg = Leg(
            venue="okx",
            symbol=inst_id,
            interval_hours=interval,
            rate_per_interval=rate,
            hourly=hourly,
            apr=apr_from_hourly(hourly),
            mark_per_coin=mark_per_coin,
            volume_usd=max(vol, 0.0),
            oi_usd=max(oi_usd, 0.0),
            next_funding_ms=int(nft) if nft else None,
            predicted_hourly=None,
        )
        if not leg_sane(leg):
            continue
        prev = out.get(asset)
        if prev is None or leg.oi_usd > prev.oi_usd:
            out[asset] = leg
    return out


def load_kraken() -> dict[str, Leg]:
    raw = get_json(f"{KRAKEN}/derivatives/api/v3/tickers")
    tickers = raw.get("tickers") or []
    out: dict[str, Leg] = {}
    for t in tickers:
        tag = (t.get("tag") or "").lower()
        if tag != "perpetual":
            continue
        symbol = t.get("symbol") or ""
        pair = t.get("pair") or ""
        # pair like BTC:USD
        base = pair.split(":")[0] if ":" in pair else symbol.replace("PF_", "").replace("USD", "")
        canon = canonical_base(base)
        if not canon:
            continue
        asset, mult = canon

        # relativeFundingRate is already hourly on Kraken; absolute is payment units
        rel = num(t.get("relativeFundingRate") if "relativeFundingRate" in t else None)
        if rel is None:
            # fallback: fundingRate in info-style fields
            abs_rate = num(t.get("fundingRate"))
            mark = num(t.get("markPrice")) or num(t.get("last"))
            if abs_rate is not None and mark and mark > 0:
                # convert absolute hourly-ish payment to relative /h then keep as hourly
                rel = abs_rate / mark
            else:
                continue
        # clamp to exchange cap
        if abs(rel) > KRAKEN_HOURLY_CAP * 4:  # hard reject absurd values
            continue
        hourly = max(-KRAKEN_HOURLY_CAP, min(KRAKEN_HOURLY_CAP, rel))

        pred = num(t.get("relativeFundingRatePrediction") if "relativeFundingRatePrediction" in t else None)
        if pred is not None:
            pred = max(-KRAKEN_HOURLY_CAP, min(KRAKEN_HOURLY_CAP, pred))

        mark = num(t.get("markPrice")) or num(t.get("last"))
        if not mark or mark <= 0:
            continue
        mark_per_coin = mark * mult
        vol = num(t.get("volumeQuote")) or 0.0
        oi = num(t.get("openInterest")) or 0.0
        oi_usd = oi * mark  # contracts * mark (approx)

        leg = Leg(
            venue="kraken",
            symbol=symbol,
            interval_hours=1.0,
            rate_per_interval=hourly,
            hourly=hourly,
            apr=apr_from_hourly(hourly),
            mark_per_coin=mark_per_coin,
            volume_usd=max(vol, 0.0),
            oi_usd=max(oi_usd, 0.0),
            next_funding_ms=None,
            predicted_hourly=pred,
        )
        if not leg_sane(leg):
            continue
        prev = out.get(asset)
        if prev is None or leg.oi_usd > prev.oi_usd:
            out[asset] = leg
    return out


def load_bybit() -> dict[str, Leg]:
    instruments = get_json(f"{BYBIT}/v5/market/instruments-info?category=linear&limit=1000")
    tickers = get_json(f"{BYBIT}/v5/market/tickers?category=linear")
    inst_list = (instruments.get("result") or {}).get("list") or []
    ticker_list = (tickers.get("result") or {}).get("list") or []
    ticker_map = {t["symbol"]: t for t in ticker_list if "symbol" in t}

    out: dict[str, Leg] = {}
    for inst in inst_list:
        if inst.get("status") != "Trading":
            continue
        if inst.get("quoteCoin") not in ("USDT", "USDC"):
            continue
        if inst.get("contractType") not in ("LinearPerpetual", "LinearFutures"):
            # prefer perpetual
            if "Perpetual" not in (inst.get("contractType") or ""):
                continue
        symbol = inst.get("symbol") or ""
        base = inst.get("baseCoin") or ""
        canon = canonical_base(base)
        if not canon:
            continue
        asset, mult = canon
        t = ticker_map.get(symbol) or {}
        rate = num(t.get("fundingRate"))
        if rate is None:
            continue
        # Bybit interval often 8h; fundingInterval in ms on instrument
        interval = 8.0
        fi = num(inst.get("fundingInterval"))
        if fi and fi > 0:
            hours = fi / 60.0  # fundingInterval is minutes on Bybit
            if hours in (1, 2, 4, 8):
                interval = hours
        hourly = hourly_from_interval(rate, interval)
        if hourly is None:
            continue
        mark = num(t.get("markPrice")) or num(t.get("lastPrice"))
        if not mark or mark <= 0:
            continue
        mark_per_coin = mark * mult
        vol = num(t.get("turnover24h")) or 0.0
        oi = num(t.get("openInterestValue")) or 0.0
        nft = num(t.get("nextFundingTime"))

        leg = Leg(
            venue="bybit",
            symbol=symbol,
            interval_hours=interval,
            rate_per_interval=rate,
            hourly=hourly,
            apr=apr_from_hourly(hourly),
            mark_per_coin=mark_per_coin,
            volume_usd=max(vol, 0.0),
            oi_usd=max(oi, 0.0),
            next_funding_ms=int(nft) if nft else None,
            predicted_hourly=None,
        )
        if not leg_sane(leg):
            continue
        prev = out.get(asset)
        if prev is None or leg.oi_usd > prev.oi_usd:
            out[asset] = leg
    return out


# --- scan --------------------------------------------------------------------

def score_pair(
    asset: str,
    a: Leg,
    b: Leg,
    min_apr: float,
    min_oi: float,
    min_vol: float,
    fee_a: float,
    fee_b: float,
    hide_unstable: bool,
) -> Opportunity | None:
    if a.oi_usd < min_oi or b.oi_usd < min_oi:
        return None
    if a.volume_usd < min_vol or b.volume_usd < min_vol:
        return None
    long, short = (a, b) if a.hourly <= b.hourly else (b, a)
    spread = short.apr - long.apr
    if spread <= 0 or spread < min_apr:
        return None
    rtf = 2 * (fee_a + fee_b)  # open+close both legs; fees are decimals
    # fee args already decimal per fill for the two venues of this pair
    fl = fee_a if long.venue == a.venue else fee_b
    fs = fee_b if short.venue == b.venue else fee_a
    # recompute with correct venue fees
    fee_map = {a.venue: fee_a, b.venue: fee_b}
    rtf = 2 * (fee_map[long.venue] + fee_map[short.venue])

    pred_long = long.predicted_hourly if long.predicted_hourly is not None else long.hourly
    pred_short = short.predicted_hourly if short.predicted_hourly is not None else short.hourly
    pred_spread = apr_from_hourly(pred_short) - apr_from_hourly(pred_long)
    unstable = pred_spread <= 0
    if hide_unstable and unstable:
        return None
    return Opportunity(
        asset=asset,
        long=long,
        short=short,
        spread_apr=spread,
        predicted_spread_apr=pred_spread,
        unstable=unstable,
        payback_days=payback_days(spread, rtf),
        basis_bps=basis_bps(long.mark_per_coin, short.mark_per_coin),
        round_trip_fee=rtf,
    )


def scan(
    use_bybit: bool,
    min_apr_pct: float,
    min_oi: float,
    min_vol: float,
    hide_unstable: bool,
    fee_bps: dict[str, float],
) -> list[Opportunity]:
    min_apr = min_apr_pct / 100.0
    fees = {k: v / 10_000.0 for k, v in fee_bps.items()}

    books: dict[str, dict[str, Leg]] = {}
    errors: list[str] = []

    for name, loader in (("okx", load_okx), ("kraken", load_kraken)):
        try:
            books[name] = loader()
            print(f"[ok] {name}: {len(books[name])} assets", file=sys.stderr)
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"[fail] {name}: {e}", file=sys.stderr)

    if use_bybit:
        try:
            books["bybit"] = load_bybit()
            print(f"[ok] bybit: {len(books['bybit'])} assets", file=sys.stderr)
        except Exception as e:
            errors.append(f"bybit: {e}")
            print(f"[fail] bybit: {e}", file=sys.stderr)

    if len(books) < 2:
        raise SystemExit("Need at least two venues with data. " + "; ".join(errors))

    assets: set[str] = set()
    for b in books.values():
        assets |= set(b.keys())

    rows: list[Opportunity] = []
    for asset in assets:
        legs = [books[v][asset] for v in books if asset in books[v]]
        if len(legs) < 2:
            continue
        best: Opportunity | None = None
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                a, b = legs[i], legs[j]
                scored = score_pair(
                    asset,
                    a,
                    b,
                    min_apr,
                    min_oi,
                    min_vol,
                    fees.get(a.venue, 0.0005),
                    fees.get(b.venue, 0.0005),
                    hide_unstable,
                )
                if scored and (best is None or scored.spread_apr > best.spread_apr):
                    best = scored
        if best:
            rows.append(best)

    rows.sort(key=lambda r: r.spread_apr, reverse=True)
    return rows


def pct(decimal: float, digits: int = 2) -> str:
    sign = "+" if decimal > 0 else ""
    return f"{sign}{decimal * 100:.{digits}f}%"


def format_x_post(row: Opportunity) -> str:
    lines = [
        f"{row.asset} funding spread",
        f"Long {row.long.venue.upper()} {pct(row.long.hourly, 4)}/h ({row.long.interval_hours:g}h settle)",
        f"Short {row.short.venue.upper()} {pct(row.short.hourly, 4)}/h ({row.short.interval_hours:g}h settle)",
        f"Spread APR if this holds: {pct(row.spread_apr, 1)}",
    ]
    if row.payback_days is not None:
        lines.append(f"Fee payback ~{row.payback_days:.1f} days (4 taker fills)")
    if row.basis_bps is not None:
        lines.append(f"Mark basis {row.basis_bps:+.1f} bps (long vs short)")
    if row.unstable:
        lines.append("Next Kraken hour may flip this spread.")
    lines.append("Not advice. Snapshot only.")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Strand funding spread desk (OKX / Kraken / optional Bybit)")
    p.add_argument("--bybit", action="store_true", help="Include Bybit (off by default for EEA)")
    p.add_argument("--min-apr", type=float, default=8.0, help="Min spread APR in percent (default 8)")
    p.add_argument("--min-oi", type=float, default=50_000.0, help="Min open interest USD per leg")
    p.add_argument("--min-vol", type=float, default=100_000.0, help="Min 24h volume USD per leg")
    p.add_argument("--top", type=int, default=10, help="How many rows to print")
    p.add_argument("--show-unstable", action="store_true", help="Keep pairs that flip on next Kraken hour")
    p.add_argument("--json", action="store_true", help="Print JSON instead of text")
    p.add_argument("--x-post", type=int, default=0, help="Also print X post for rank N (1-based)")
    args = p.parse_args()

    fee_bps = {
        "okx": float(os.environ.get("FEE_BPS_OKX", "5")),
        "kraken": float(os.environ.get("FEE_BPS_KRAKEN", "5")),
        "bybit": float(os.environ.get("FEE_BPS_BYBIT", "5.5")),
    }

    rows = scan(
        use_bybit=args.bybit,
        min_apr_pct=args.min_apr,
        min_oi=args.min_oi,
        min_vol=args.min_vol,
        hide_unstable=not args.show_unstable,
        fee_bps=fee_bps,
    )

    if args.json:
        payload = []
        for r in rows[: args.top]:
            payload.append(
                {
                    "asset": r.asset,
                    "spread_apr_pct": r.spread_apr * 100,
                    "long": asdict(r.long),
                    "short": asdict(r.short),
                    "payback_days": r.payback_days,
                    "basis_bps": r.basis_bps,
                    "unstable": r.unstable,
                    "x_post": format_x_post(r),
                }
            )
        print(json.dumps(payload, indent=2))
        return

    if not rows:
        print("No pair cleared the floor. Lower --min-apr or check venue feeds.")
        return

    print(f"{'ASSET':<10} {'SPREAD APR':>10}  LONG → SHORT")
    print("-" * 72)
    for r in rows[: args.top]:
        print(
            f"{r.asset:<10} {pct(r.spread_apr, 1):>10}  "
            f"{r.long.venue.upper()} {pct(r.long.hourly, 4)}/h → "
            f"{r.short.venue.upper()} {pct(r.short.hourly, 4)}/h"
        )

    if args.x_post and 1 <= args.x_post <= len(rows):
        print("\n--- X post ---\n")
        print(format_x_post(rows[args.x_post - 1]))


if __name__ == "__main__":
    main()
