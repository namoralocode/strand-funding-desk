#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strand Funding Desk — Streamlit UI (X-ready cards)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from strand_scan import format_x_post, pct, scan

st.set_page_config(
    page_title="Strand Funding Desk",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
  .stApp { background: #0b0f14; color: #e8eef7; }
  [data-testid="stSidebar"] { background: #0e141c; border-right: 1px solid #1c2733; }
  h1, h2, h3 { color: #f3f7fc !important; letter-spacing: -0.02em; }
  .desk-hero {
    border: 1px solid #1e2a38;
    background: linear-gradient(180deg, #121a24 0%, #0b0f14 100%);
    border-radius: 16px;
    padding: 18px 20px;
    margin-bottom: 16px;
  }
  .desk-kicker {
    color: #7f93a8;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 6px;
  }
  .card {
    border: 1px solid #243244;
    background: #101821;
    border-radius: 14px;
    padding: 16px 16px 12px 16px;
    margin-bottom: 12px;
  }
  .card-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 10px;
  }
  .asset {
    font-size: 1.35rem;
    font-weight: 700;
    color: #f5f8fc;
  }
  .apr {
    font-size: 1.45rem;
    font-weight: 700;
    color: #3dde9a;
  }
  .legs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin: 8px 0 10px 0;
  }
  .leg {
    border-radius: 10px;
    padding: 10px 12px;
    border: 1px solid #243244;
    background: #0c1219;
  }
  .leg.long { border-color: #1f4d3a; }
  .leg.short { border-color: #4d2430; }
  .leg-label {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8aa0b5;
    margin-bottom: 4px;
  }
  .leg-venue { font-weight: 700; color: #eaf1f8; }
  .leg-rate { color: #c5d4e3; font-size: 0.95rem; margin-top: 2px; }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px 14px;
    color: #9fb0c2;
    font-size: 0.86rem;
    border-top: 1px solid #1c2733;
    padding-top: 10px;
  }
  .meta b { color: #d7e3ef; font-weight: 600; }
  .badge {
    display: inline-block;
    font-size: 0.7rem;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid #3a2a18;
    color: #f0c38a;
    background: #1a140c;
  }
</style>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Scan")
    min_apr = st.slider("Min spread APR %", 0.0, 50.0, 8.0, 0.5)
    top_n = st.slider("Top N cards", 1, 15, 5)
    min_oi = st.number_input("Min OI USD / leg", value=50_000, step=10_000)
    min_vol = st.number_input("Min 24h volume USD / leg", value=100_000, step=10_000)
    use_bybit = st.checkbox("Include Bybit", value=False)
    show_unstable = st.checkbox("Show unstable", value=False)
    run = st.button("Scan now", type="primary", use_container_width=True)
    st.caption("Snapshot only. Not financial advice.")

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
    st.session_state["scanned_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

rows = st.session_state.get("rows") or []
scanned_at = st.session_state.get("scanned_at", "—")

venues = "OKX · Kraken" + (" · Bybit" if use_bybit else "")
st.markdown(
    f"""
<div class="desk-hero">
  <div class="desk-kicker">EEA desk · public funding snapshot</div>
  <h1 style="margin:0 0 6px 0; font-size:1.7rem;">Strand Funding Desk</h1>
  <div style="color:#9fb0c2; font-size:0.92rem;">{venues} · scanned {scanned_at}</div>
</div>
""",
    unsafe_allow_html=True,
)

if not st.session_state.get("scanned"):
    st.info("Ustaw filtry z lewej i kliknij **Scan now**. Potem zrób zrzut kart.")
    st.stop()

if not rows:
    st.warning("Brak par powyżej progu. Obniż min APR lub filtry płynności.")
    st.stop()

show = rows[:top_n]

for i, r in enumerate(show, start=1):
    payback = f"{r.payback_days:.1f}d" if r.payback_days is not None else "—"
    basis = f"{r.basis_bps:+.1f}" if r.basis_bps is not None else "—"
    unstable = '<span class="badge">unstable next hour</span>' if r.unstable else ""
    st.markdown(
        f"""
<div class="card">
  <div class="card-top">
    <div class="asset">#{i} {r.asset} {unstable}</div>
    <div class="apr">{pct(r.spread_apr, 1)}</div>
  </div>
  <div class="legs">
    <div class="leg long">
      <div class="leg-label">Long · receive / pay less</div>
      <div class="leg-venue">{r.long.venue.upper()}</div>
      <div class="leg-rate">{pct(r.long.hourly, 4)}/h · {r.long.interval_hours:g}h settle</div>
    </div>
    <div class="leg short">
      <div class="leg-label">Short · pay / receive less</div>
      <div class="leg-venue">{r.short.venue.upper()}</div>
      <div class="leg-rate">{pct(r.short.hourly, 4)}/h · {r.short.interval_hours:g}h settle</div>
    </div>
  </div>
  <div class="meta">
    <span>Payback <b>{payback}</b></span>
    <span>Basis <b>{basis} bps</b></span>
    <span>OI L <b>${r.long.oi_usd:,.0f}</b></span>
    <span>OI S <b>${r.short.oi_usd:,.0f}</b></span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

with st.expander("Tabela (surowa)"):
    table = []
    for i, r in enumerate(show, start=1):
        table.append(
            {
                "#": i,
                "Asset": r.asset,
                "Spread APR": pct(r.spread_apr, 1),
                "Long": f"{r.long.venue.upper()} {pct(r.long.hourly, 4)}/h",
                "Short": f"{r.short.venue.upper()} {pct(r.short.hourly, 4)}/h",
                "Payback d": f"{r.payback_days:.1f}" if r.payback_days is not None else "—",
                "Basis bps": f"{r.basis_bps:+.1f}" if r.basis_bps is not None else "—",
            }
        )
    st.dataframe(table, use_container_width=True, hide_index=True)

st.subheader("Tekst pod post (opcjonalnie)")
pick = st.selectbox(
    "Karta",
    options=list(range(1, len(show) + 1)),
    format_func=lambda n: f"#{n} {show[n - 1].asset} ({pct(show[n - 1].spread_apr, 1)})",
)
post = format_x_post(show[pick - 1])
st.code(post, language=None)

st.caption(
    "APR warunkowy — tylko jeśli snapshot się utrzyma. Dwie marże, ryzyko likwidacji jednej nogi. Zrzut ekranu = najczystszy post na X."
)
