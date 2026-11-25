#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strand Funding Desk — Streamlit UI."""

from __future__ import annotations

import os

import streamlit as st

from strand_scan import format_x_post, pct, scan

st.set_page_config(
    page_title="Strand Funding Desk",
    page_icon="◈",
    layout="wide",
)

st.title("Strand Funding Desk")
st.caption(
    "OKX · Kraken · optional Bybit · hourly-normalized APR · not financial advice"
)

with st.sidebar:
    st.header("Scan")
    min_apr = st.slider("Min spread APR %", 0.0, 50.0, 8.0, 0.5)
    top_n = st.slider("Top N", 1, 30, 10)
    min_oi = st.number_input("Min OI USD / leg", value=50_000, step=10_000)
    min_vol = st.number_input("Min 24h volume USD / leg", value=100_000, step=10_000)
    use_bybit = st.checkbox("Include Bybit", value=False)
    show_unstable = st.checkbox("Show unstable (Kraken next-hour flip risk)", value=False)
    run = st.button("Scan now", type="primary", use_container_width=True)

fee_bps = {
    "okx": float(os.environ.get("FEE_BPS_OKX", "5")),
    "kraken": float(os.environ.get("FEE_BPS_KRAKEN", "5")),
    "bybit": float(os.environ.get("FEE_BPS_BYBIT", "5.5")),
}

if run:
    with st.spinner("Fetching funding rates…"):
        try:
            rows = scan(
                use_bybit=use_bybit,
                min_apr_pct=min_apr,
                min_oi=float(min_oi),
                min_vol=float(min_vol),
                hide_unstable=not show_unstable,
                fee_bps=fee_bps,
            )
        except SystemExit as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Scan failed: {e}")
            st.stop()

    st.session_state["rows"] = rows
    st.session_state["scanned"] = True

rows = st.session_state.get("rows") or []
if not st.session_state.get("scanned"):
    st.info("Ustaw filtry w panelu bocznym i kliknij **Scan now**.")
    st.stop()

if not rows:
    st.warning("Brak par powyżej progu. Obniż min APR lub filtry płynności.")
    st.stop()

table = []
for i, r in enumerate(rows[:top_n], start=1):
    table.append(
        {
            "#": i,
            "Asset": r.asset,
            "Spread APR": pct(r.spread_apr, 1),
            "Long": f"{r.long.venue.upper()} {pct(r.long.hourly, 4)}/h",
            "Short": f"{r.short.venue.upper()} {pct(r.short.hourly, 4)}/h",
            "Payback d": f"{r.payback_days:.1f}" if r.payback_days is not None else "—",
            "Basis bps": f"{r.basis_bps:+.1f}" if r.basis_bps is not None else "—",
            "Unstable": "yes" if r.unstable else "",
        }
    )

st.subheader(f"Top {min(top_n, len(rows))} spreads")
st.dataframe(table, use_container_width=True, hide_index=True)

st.subheader("X post")
pick = st.selectbox(
    "Pozycja",
    options=list(range(1, min(top_n, len(rows)) + 1)),
    format_func=lambda n: f"#{n} {rows[n - 1].asset} ({pct(rows[n - 1].spread_apr, 1)})",
)
post = format_x_post(rows[pick - 1])
st.code(post, language=None)
st.download_button("Pobierz tekst posta", post, file_name=f"x_post_{rows[pick - 1].asset}.txt")

with st.expander("Szczegóły wybranej pary"):
    r = rows[pick - 1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Spread APR", pct(r.spread_apr, 2))
    c2.metric("Payback (dni)", f"{r.payback_days:.1f}" if r.payback_days is not None else "—")
    c3.metric("Basis bps", f"{r.basis_bps:+.1f}" if r.basis_bps is not None else "—")
    st.write(
        f"**Long** `{r.long.venue}` `{r.long.symbol}` — "
        f"{pct(r.long.hourly, 4)}/h · interval {r.long.interval_hours:g}h · "
        f"OI ${r.long.oi_usd:,.0f} · vol ${r.long.volume_usd:,.0f}"
    )
    st.write(
        f"**Short** `{r.short.venue}` `{r.short.symbol}` — "
        f"{pct(r.short.hourly, 4)}/h · interval {r.short.interval_hours:g}h · "
        f"OI ${r.short.oi_usd:,.0f} · vol ${r.short.volume_usd:,.0f}"
    )

st.caption(
    "APR jest warunkowy — tylko jeśli snapshot się utrzyma. Dwie osobne marże, ryzyko likwidacji jednej nogi."
)
