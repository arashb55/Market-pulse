#!/usr/bin/env python3
"""
Market Pulse - a one-glance market dashboard.

Builds a self-contained HTML dashboard (indexes, futures, volatility, yields,
dollar, CAD, commodities, crypto, sectors, S&P 500 heatmap, breadth, intraday
path, risk-on/off regime) from free Yahoo Finance data via yfinance.

Usage
  python market_pulse.py                 build now, open in browser
  python market_pulse.py --auto          scheduler mode: only builds at the open,
                                         midday and close (New York time), once each
  python market_pulse.py --demo          synthetic data, to preview the layout offline
  python market_pulse.py --no-open       don't launch the browser
  python market_pulse.py --telegram      also send summary + file to Telegram

All timing is done in New York time, so it stays right through every DST change
regardless of the computer's local clock.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time as dtime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo


def _pause_if_double_clicked():
    """Keep the window open on errors when the script was double-clicked."""
    try:
        if "--auto" not in sys.argv and sys.stdin and sys.stdin.isatty():
            input("\nPress Enter to close this window...")
    except Exception:
        pass


def _ensure_packages():
    """Install any missing packages automatically (first run / double-click)."""
    import importlib.util
    import subprocess
    needed = ["numpy", "pandas", "plotly", "yfinance", "requests", "lxml", "tzdata"]
    missing = [m for m in needed if importlib.util.find_spec(m) is None]
    if not missing:
        return
    print(f"First run: installing {', '.join(missing)} - this takes a minute or two...")
    req = Path(__file__).resolve().parent / "requirements.txt"
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "-r", str(req)])
    if rc != 0:
        print("\nAutomatic install failed. Open a terminal in this folder and run:\n"
              f'  "{sys.executable}" -m pip install -r requirements.txt')
        _pause_if_double_clicked()
        sys.exit(1)
    importlib.invalidate_caches()


_ensure_packages()

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

# ════════════════════════════════════════════════════════════════════════════
# CONFIG - edit freely
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
LOCAL_TZ = ZoneInfo(os.environ.get("MP_LOCAL_TZ", "America/Vancouver"))
NY = ZoneInfo("America/New_York")

# "classic" = green up / red down.  "cvd" = blue up / red down (colour-blind safe)
COLOR_SCHEME = os.environ.get("MP_COLORS", "classic")

# Scheduler windows (New York time). In --auto mode the script builds a snapshot
# when the clock is inside a window and that snapshot hasn't been built today.
SESSIONS = [  # (key, label, start ET, end ET)
    ("open",   "Opening Bell", dtime(9, 35),  dtime(10, 15)),
    ("midday", "Midday",       dtime(12, 30), dtime(13, 10)),
    ("close",  "Closing Bell", dtime(16, 5),  dtime(16, 45)),
]

# (label, yahoo ticker, kind)   kind: idx | fut | vol | yld | fx | cmd | cry
INDEXES = [("S&P 500", "^GSPC", "idx"), ("Nasdaq 100", "^NDX", "idx"),
           ("Dow Jones", "^DJI", "idx"), ("Russell 2000", "^RUT", "idx"),
           ("TSX Composite", "^GSPTSE", "idx")]
FUTURES = [("S&P futures", "ES=F", "fut"), ("Nasdaq futures", "NQ=F", "fut"),
           ("Dow futures", "YM=F", "fut"), ("Russell futures", "RTY=F", "fut")]
VOL_RATES = [("VIX", "^VIX", "vol"), ("US 3M", "^IRX", "yld"), ("US 2Y", "2YY=F", "yld"),
             ("US 5Y", "^FVX", "yld"), ("US 10Y", "^TNX", "yld"), ("US 30Y", "^TYX", "yld")]
FX_CMD = [("Dollar Index", "DX-Y.NYB", "fx"), ("USD/CAD", "CAD=X", "fx"),
          ("Gold", "GC=F", "cmd"), ("Silver", "SI=F", "cmd"), ("WTI Crude", "CL=F", "cmd"),
          ("Brent Crude", "BZ=F", "cmd"), ("Natural Gas", "NG=F", "cmd"), ("Copper", "HG=F", "cmd")]
CRYPTO = [("Bitcoin", "BTC-USD", "cry"), ("Ethereum", "ETH-USD", "cry"), ("Solana", "SOL-USD", "cry")]

SECTORS = [("XLK", "Technology"), ("XLC", "Communication"), ("XLY", "Cons. Discretionary"),
           ("XLF", "Financials"), ("XLI", "Industrials"), ("XLV", "Health Care"),
           ("XLP", "Cons. Staples"), ("XLE", "Energy"), ("XLB", "Materials"),
           ("XLU", "Utilities"), ("XLRE", "Real Estate")]

# Risk-on/off ratios: (label, numerator, denominator). Ratio above its 50-day MA = risk-on.
RATIOS = [("Discretionary / Staples", "XLY", "XLP"), ("High-yield / IG credit", "HYG", "LQD"),
          ("Stocks / Long bonds", "SPY", "TLT"), ("Copper / Gold", "HG=F", "GC=F"),
          ("Small caps / Large caps", "IWM", "SPY"), ("Semis / S&P 500", "SMH", "SPY")]

INTRADAY = [("S&P 500 (SPY)", "SPY"), ("Nasdaq 100 (QQQ)", "QQQ"),
            ("Russell 2000 (IWM)", "IWM"), ("Dow (DIA)", "DIA")]

EXTRA = ["SPY", "QQQ", "IWM", "DIA", "HYG", "LQD", "TLT", "SMH"]

GICS_SHORT = {"Information Technology": "Technology", "Communication Services": "Communication",
              "Consumer Discretionary": "Cons. Discretionary", "Consumer Staples": "Cons. Staples"}

# Fallback universe if the S&P 500 list can't be downloaded (top names by size)
FALLBACK_UNIVERSE = {
    "Technology": "AAPL MSFT NVDA AVGO ORCL CRM AMD ADBE CSCO ACN IBM QCOM TXN INTU NOW AMAT MU LRCX ADI KLAC PANW SNPS CDNS INTC ANET",
    "Communication": "GOOGL META NFLX TMUS DIS VZ T CMCSA",
    "Cons. Discretionary": "AMZN TSLA HD MCD LOW BKNG TJX SBUX NKE CMG ORLY",
    "Financials": "BRK-B JPM V MA BAC WFC GS MS AXP SPGI BLK C SCHW PGR CB",
    "Industrials": "GE CAT RTX UNP HON BA DE LMT UPS ETN ADP WM",
    "Health Care": "LLY UNH JNJ ABBV MRK TMO ABT ISRG AMGN DHR PFE BSX SYK VRTX GILD",
    "Cons. Staples": "WMT PG COST KO PEP PM MO MDLZ CL",
    "Energy": "XOM CVX COP EOG SLB MPC PSX",
    "Materials": "LIN SHW APD ECL FCX NEM",
    "Utilities": "NEE SO DUK CEG SRE AEP",
    "Real Estate": "PLD AMT EQIX WELL SPG PSA",
}

LINKS = [("Economic calendar", "https://www.forexfactory.com/calendar"),
         ("Earnings calendar", "https://www.earningswhispers.com/calendar"),
         ("Finviz map", "https://finviz.com/map.ashx"),
         ("CNN Fear & Greed", "https://www.cnn.com/markets/fear-and-greed")]

# ════════════════════════════════════════════════════════════════════════════
# THEME
# ════════════════════════════════════════════════════════════════════════════
INK, INK2, MUTED = "#f0efec", "#c3c2b7", "#898781"
GRID, BASE, SURF, PAGE = "#2c2c2a", "#383835", "#1a1a19", "#0d0d0d"
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500"]
if COLOR_SCHEME == "cvd":
    UP, DOWN = "#3987e5", "#e66767"
else:
    UP, DOWN = "#0ca30c", "#e5484d"
NEUTRAL = BASE


def chg_color(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return MUTED
    return UP if v > 0 else DOWN if v < 0 else MUTED


def _mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def cell_bg(v, scale):
    """Diverging cell colour: neutral grey at 0, up/down at +/-scale."""
    if v is None or np.isnan(v):
        return SURF
    t = max(-1.0, min(1.0, v / scale)) if scale else 0
    return _mix(NEUTRAL, UP if t > 0 else DOWN, abs(t) ** 0.8 * 0.85)


DIVERGING = [[0.0, DOWN], [0.25, _mix(NEUTRAL, DOWN, 0.6)], [0.5, NEUTRAL],
             [0.75, _mix(NEUTRAL, UP, 0.6)], [1.0, UP]]


def base_layout(fig, height, **kw):
    kw.setdefault("margin", dict(l=8, r=8, t=8, b=8))
    kw.setdefault("showlegend", False)
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family='system-ui,-apple-system,"Segoe UI",sans-serif', color=INK2, size=12),
        hoverlabel=dict(bgcolor="#262624", bordercolor=BASE, font=dict(color=INK, size=12)),
        **kw)
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=BASE, linecolor=BASE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=BASE, linecolor=BASE, tickfont=dict(color=MUTED))
    return fig


def fig_html(fig, div_id):
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id,
                       config={"displayModeBar": False, "responsive": True})


# ════════════════════════════════════════════════════════════════════════════
# DATA
# ════════════════════════════════════════════════════════════════════════════
def all_macro_tickers():
    t = [x[1] for grp in (INDEXES, FUTURES, VOL_RATES, FX_CMD, CRYPTO) for x in grp]
    t += [s for s, _ in SECTORS] + EXTRA
    for _, a, b in RATIOS:
        t += [a, b]
    return list(dict.fromkeys(t))


def _cache_get(name, max_age_days):
    p = CACHE_DIR / name
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _cache_put(name, obj):
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / name).write_text(json.dumps(obj))


def load_universe():
    """S&P 500 constituents (ticker, name, sector). Cached for 7 days."""
    cached = _cache_get("sp500.json", 7)
    if cached:
        return pd.DataFrame(cached)
    try:
        import requests
        r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                         headers={"User-Agent": "Mozilla/5.0 (MarketPulse dashboard)"}, timeout=20)
        r.raise_for_status()
        tbl = pd.read_html(StringIO(r.text), attrs={"id": "constituents"})[0]
        df = pd.DataFrame({
            "ticker": tbl["Symbol"].str.replace(".", "-", regex=False),
            "name": tbl["Security"],
            "sector": tbl["GICS Sector"].map(lambda s: GICS_SHORT.get(s, s)),
        })
        _cache_put("sp500.json", df.to_dict("records"))
        return df
    except Exception as e:
        print(f"  ! S&P 500 list unavailable ({e}); using built-in top-name list")
        rows = [{"ticker": t, "name": t, "sector": s}
                for s, ts in FALLBACK_UNIVERSE.items() for t in ts.split()]
        return pd.DataFrame(rows)


def load_market_caps(tickers):
    """Market caps (cached 7 days). Missing ones fall back to dollar volume later."""
    cached = _cache_get("mcaps.json", 7) or {}
    missing = [t for t in tickers if t not in cached]
    if missing:
        import yfinance as yf
        from concurrent.futures import as_completed

        def one(t):
            try:
                return t, float(yf.Ticker(t).fast_info["marketCap"])
            except Exception:
                return t, None
        budget = 75  # seconds; whatever isn't fetched now is filled in on later runs
        print(f"[3/4] Fetching market caps for heatmap sizing ({len(missing)} left, max {budget}s)...")
        t0, done = time.time(), 0
        ex = ThreadPoolExecutor(max_workers=16)
        futs = [ex.submit(one, t) for t in missing]
        try:
            for f in as_completed(futs, timeout=budget):
                t, mc = f.result()
                done += 1
                if mc and mc > 0:
                    cached[t] = mc
                if done % 100 == 0:
                    print(f"      {done}/{len(missing)}")
        except Exception:
            print("      time budget reached - the rest will be fetched on the next runs")
        ex.shutdown(wait=False, cancel_futures=True)
        _cache_put("mcaps.json", cached)
        print(f"      done in {time.time() - t0:.0f}s")
    return cached


def _closes(df):
    """yf.download result -> (close DataFrame, volume DataFrame) with ticker columns."""
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        close = df["Close"] if "Close" in lvl0 else df.xs("Close", axis=1, level=1)
        vol = df["Volume"] if "Volume" in lvl0 else df.xs("Volume", axis=1, level=1)
    else:
        close, vol = df[["Close"]], df[["Volume"]]
    idx = pd.to_datetime(close.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    day = idx.normalize()
    close = close.copy(); vol = vol.copy()
    close.index, vol.index = day, day
    # Yahoo sometimes returns today's bar twice (or an empty row) after the close:
    # keep one row per date (last valid value) and drop rows with no data at all.
    close = _one_row_per_day(close).dropna(how="all")
    vol = vol.groupby(level=0).last().reindex(close.index)
    return close, vol


def _one_row_per_day(df):
    """Collapse duplicate rows for the same date. Per stock, prefer the latest value
    that actually differs from the previous day's close (a stale copy of yesterday's
    close is what makes every stock show +0.00% after hours)."""
    if not df.index.has_duplicates:
        return df
    out, prev = [], None
    for day, block in df.groupby(level=0, sort=True):
        if len(block) == 1:
            row = block.iloc[0]
        else:
            vals = block.to_numpy(dtype=float)
            valid = ~np.isnan(vals)
            fresh = valid & (vals != prev.to_numpy(dtype=float)) if prev is not None else valid
            pick = np.where(fresh.any(0), fresh, valid)
            # index of the last True per column (or 0 when the column is all-NaN)
            last_ix = np.where(pick.any(0), pick.shape[0] - 1 - np.argmax(pick[::-1], axis=0), 0)
            row = pd.Series(vals[last_ix, np.arange(vals.shape[1])], index=df.columns)
        row.name = day
        out.append(row)
        if row.notna().any():
            prev = row if prev is None else row.fillna(prev)
    return pd.DataFrame(out)


def fetch_live():
    import yfinance as yf
    print("[1/4] Downloading indexes, rates, commodities, crypto, sectors...")
    macro = all_macro_tickers()
    raw = yf.download(macro, period="1y", interval="1d", auto_adjust=True,
                      progress=False, threads=True, group_by="column")
    mclose, _ = _closes(raw)

    uni = load_universe()
    print(f"[2/4] Downloading {len(uni)} S&P 500 stocks (about 30-60 seconds)...")
    rawu = yf.download(list(uni["ticker"]), period="1y", interval="1d", auto_adjust=True,
                       progress=False, threads=True, group_by="column")
    uclose, uvol = _closes(rawu)

    tick = [t for _, t in INTRADAY]
    try:
        rawi = yf.download(tick, period="5d", interval="5m", auto_adjust=True,
                           progress=False, threads=True, group_by="column", prepost=False)
        ic = rawi["Close"] if isinstance(rawi.columns, pd.MultiIndex) else rawi[["Close"]]
        ic.index = pd.to_datetime(ic.index)
        ic.index = (ic.index.tz_convert(NY) if ic.index.tz is not None else ic.index.tz_localize("UTC").tz_convert(NY))
    except Exception as e:
        print(f"  ! intraday unavailable: {e}")
        ic = pd.DataFrame()

    mcaps = load_market_caps(list(uni["ticker"]))
    return dict(macro=mclose, uclose=uclose, uvol=uvol, intraday=ic, universe=uni, mcaps=mcaps)


def fetch_demo(seed=7):
    """Synthetic but realistic-looking data, for previewing the layout offline."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=252)
    levels = {"^GSPC": 6750, "^NDX": 24800, "^DJI": 47100, "^RUT": 2450, "^GSPTSE": 30200,
              "ES=F": 6770, "NQ=F": 24900, "YM=F": 47300, "RTY=F": 2460, "^VIX": 16,
              "^IRX": 3.85, "2YY=F": 3.55, "^FVX": 3.70, "^TNX": 4.10, "^TYX": 4.70,
              "DX-Y.NYB": 99.5, "CAD=X": 1.40, "GC=F": 4050, "SI=F": 50, "CL=F": 60,
              "BZ=F": 64, "NG=F": 4.4, "HG=F": 5.0, "BTC-USD": 95000, "ETH-USD": 3200,
              "SOL-USD": 140}

    def walk(n, start, vol, drift=0.0003):
        r = rng.normal(drift, vol, n)
        return start * np.exp(np.cumsum(r) - np.cumsum(r)[-1])

    macro = {}
    for t in all_macro_tickers():
        lv = levels.get(t, rng.uniform(30, 600))
        if t in ("^IRX", "2YY=F", "^FVX", "^TNX", "^TYX"):
            s = lv + np.cumsum(rng.normal(0, 0.035, 252)); s += lv - s[-1]
        elif t == "^VIX":
            s = np.clip(lv + np.cumsum(rng.normal(0, 0.9, 252)) * 0.4, 11, 45); s[-1] = lv
        else:
            s = walk(252, lv, 0.022 if "USD" in t and t != "CAD=X" else 0.011)
        macro[t] = s
    mclose = pd.DataFrame(macro, index=idx)

    uni = pd.DataFrame([{"ticker": t, "name": t, "sector": s}
                        for s, ts in FALLBACK_UNIVERSE.items() for t in ts.split()])
    sec_beta = {s: rng.normal(0, 0.008) for s in FALLBACK_UNIVERSE}
    mkt = rng.normal(0.0004, 0.009, 252)
    uc = {}
    for _, row in uni.iterrows():
        r = mkt + rng.normal(sec_beta[row.sector] / 20, 0.004, 252) + rng.normal(0, 0.014, 252)
        r[-1] = mkt[-1] * 0.8 + sec_beta[row.sector] + rng.normal(0, 0.012)
        uc[row.ticker] = rng.uniform(40, 700) * np.exp(np.cumsum(r) - np.cumsum(r)[-1])
    uclose = pd.DataFrame(uc, index=idx)
    uvol = pd.DataFrame(rng.uniform(1e6, 3e7, uclose.shape), index=idx, columns=uclose.columns)
    mcaps = {t: float(rng.lognormal(np.log(1.5e11), 1.0)) for t in uclose.columns}
    for t, v in {"AAPL": 4e12, "MSFT": 3.8e12, "NVDA": 4.5e12, "GOOGL": 3.2e12, "AMZN": 2.5e12,
                 "META": 1.6e12, "AVGO": 1.6e12, "TSLA": 1.4e12, "BRK-B": 1.05e12, "JPM": 8.5e11}.items():
        mcaps[t] = v

    day = pd.Timestamp.today().normalize()
    bars = pd.date_range(day + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min", tz=NY)
    ic = {}
    for _, t in INTRADAY:
        prev = mclose[t].iloc[-2]
        path = np.cumsum(rng.normal(0.00004, 0.0011, 78))
        path = path - path[-1] + np.log(mclose[t].iloc[-1] / prev)
        ic[t] = prev * np.exp(path)
    ic = pd.DataFrame(ic, index=bars)
    return dict(macro=mclose, uclose=uclose, uvol=uvol, intraday=ic, universe=uni, mcaps=mcaps)


# ════════════════════════════════════════════════════════════════════════════
# METRICS
# ════════════════════════════════════════════════════════════════════════════
def series_metrics(s: pd.Series, kind: str = ""):
    s = s.dropna()
    if len(s) < 2:
        return None
    last, prev = float(s.iloc[-1]), float(s.iloc[-2])
    yld = kind == "yld"

    def chg(n):
        if len(s) <= n:
            return np.nan
        b = float(s.iloc[-1 - n])
        return (last - b) * 100 if yld else (last / b - 1) * 100

    this_year = s.index[-1].year
    before = s[s.index.year < this_year]
    ybase = float(before.iloc[-1]) if len(before) else float(s.iloc[0])
    return dict(last=last, prev=prev, d1=chg(1), d5=chg(5), m1=chg(21), m3=chg(63),
                ytd=(last - ybase) * 100 if yld else (last / ybase - 1) * 100,
                hi52=float(s.max()), lo52=float(s.min()), spark=s.iloc[-63:].tolist(),
                date=s.index[-1])


def compute(data):
    m = data["macro"]
    out = {"tiles": {}}
    for grp in (INDEXES, FUTURES, VOL_RATES, FX_CMD, CRYPTO):
        for label, t, kind in grp:
            if t in m:
                r = series_metrics(m[t], kind)
                if r:
                    out["tiles"][t] = dict(label=label, kind=kind, **r)

    out["sectors"] = []
    for t, name in SECTORS:
        if t in m:
            r = series_metrics(m[t])
            if r:
                out["sectors"].append(dict(ticker=t, name=name, **r))

    # Universe: daily change, breadth, heatmap
    uc = data["uclose"].dropna(how="all", axis=1)
    ref_date = m["^GSPC"].dropna().index[-1] if "^GSPC" in m else uc.index[-1]
    uc = uc.loc[:ref_date]
    # each stock's change from its own last two real prices (no forward-filling)
    last, prev = {}, {}
    for t in uc.columns:
        sv = uc[t].dropna()
        if len(sv) >= 2 and sv.index[-1] == uc.index[-1]:
            last[t], prev[t] = float(sv.iloc[-1]), float(sv.iloc[-2])
    last, prev = pd.Series(last, dtype=float), pd.Series(prev, dtype=float)
    pct = (last / prev - 1) * 100
    if len(pct) and (pct.abs() < 1e-9).mean() > 0.8:
        tail = data["uclose"].iloc[-3:, :3].to_string()
        msg = "Most stocks show 0% change - Yahoo returned odd data. Last rows:\n" + tail
        print("  ! " + msg)
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "data_warning.txt").write_text(f"{datetime.now():%Y-%m-%d %H:%M}\n{msg}\n", encoding="utf-8")
    ma50, ma200 = uc.rolling(50).mean().iloc[-1], uc.rolling(200, min_periods=150).mean().iloc[-1]
    hi52, lo52 = uc.max(), uc.min()
    dv = (data["uclose"] * data["uvol"]).iloc[-20:].mean()
    uni = data["universe"].set_index("ticker")
    rows = []
    for t in uc.columns:
        if t not in uni.index or t not in pct.index or np.isnan(pct[t]):
            continue
        cap = data["mcaps"].get(t) or (dv.get(t, np.nan) * 40)
        if not cap or np.isnan(cap):
            continue
        rows.append(dict(ticker=t, name=str(uni.at[t, "name"]), sector=uni.at[t, "sector"],
                         price=float(last[t]), pct=float(pct[t]), cap=float(cap),
                         a50=bool(last[t] > ma50[t]) if not np.isnan(ma50[t]) else None,
                         a200=bool(last[t] > ma200[t]) if not np.isnan(ma200[t]) else None,
                         nh=bool(last[t] >= hi52[t] * 0.999), nl=bool(last[t] <= lo52[t] * 1.001)))
    u = pd.DataFrame(rows)
    out["universe"] = u
    n = len(u)
    out["breadth"] = dict(
        n=n, adv=int((u.pct > 0.02).sum()), dec=int((u.pct < -0.02).sum()),
        unch=int(((u.pct >= -0.02) & (u.pct <= 0.02)).sum()),
        a50=float(u.a50.dropna().mean() * 100) if n else np.nan,
        a200=float(u.a200.dropna().mean() * 100) if n else np.nan,
        nh=int(u.nh.sum()), nl=int(u.nl.sum()),
        ew=float(u.pct.mean()) if n else np.nan,
        cw=float(np.average(u.pct, weights=u.cap)) if n else np.nan)

    # Ratios & regime
    ratios, signals = [], []
    for label, a, b in RATIOS:
        if a in m and b in m:
            r = (m[a] / m[b]).dropna()
            if len(r) > 60:
                ma = r.rolling(50).mean()
                on = bool(r.iloc[-1] > ma.iloc[-1])
                ratios.append(dict(label=label, series=r.iloc[-126:], ma=ma.iloc[-126:], on=on,
                                   d20=(r.iloc[-1] / r.iloc[-21] - 1) * 100))
                signals.append((label, on, "above its 50-day average" if on else "below its 50-day average"))
    spx = m["^GSPC"].dropna() if "^GSPC" in m else None
    if spx is not None and len(spx) > 200:
        on = bool(spx.iloc[-1] > spx.rolling(200).mean().iloc[-1])
        signals.append(("S&P 500 trend", on, "above 200-day average" if on else "below 200-day average"))
    vix = out["tiles"].get("^VIX")
    if vix:
        on = vix["last"] < 20
        signals.append(("Volatility", on, f"VIX {vix['last']:.1f} {'<' if on else '≥'} 20"))
    b = out["breadth"]
    if not np.isnan(b["a50"]):
        on = b["a50"] >= 50
        signals.append(("Breadth", on, f"{b['a50']:.0f}% of stocks above 50-day"))
    out["ratios"] = ratios
    out["signals"] = signals
    out["score"] = round(100 * sum(1 for s in signals if s[1]) / len(signals)) if signals else 50

    # Intraday
    ic = data["intraday"]
    out["intraday"] = None
    if ic is not None and len(ic):
        day = ic.index[-1].date()
        today = ic[ic.index.date == day]
        series = {}
        for label, t in INTRADAY:
            if t in today and t in m:
                dser = m[t].dropna()
                prevs = dser[dser.index.date < day]
                if len(prevs):
                    series[label] = (today[t].dropna() / float(prevs.iloc[-1]) - 1) * 100
        out["intraday"] = dict(day=day, series=series)
    return out


# ════════════════════════════════════════════════════════════════════════════
# RENDER HELPERS
# ════════════════════════════════════════════════════════════════════════════
def fmt_val(v, kind):
    if v is None or np.isnan(v):
        return "–"
    if kind == "yld":
        return f"{v:.2f}%"
    if kind == "fx":
        return f"{v:.4f}" if v < 10 else f"{v:.2f}"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if kind == "cry" else f"{v:,.2f}"
    return f"{v:.2f}" if abs(v) >= 1 else f"{v:.4f}"


def fmt_chg(v, kind="", digits=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    if kind == "yld":
        return f"{v:+.0f} bp" if abs(v) >= 10 else f"{v:+.1f} bp"
    return f"{v:+.{digits}f}%"


def arrow(v):
    if v is None or np.isnan(v) or v == 0:
        return "■"
    return "▲" if v > 0 else "▼"


def sparkline(vals, w=128, h=34):
    v = np.asarray([x for x in vals if x is not None and not np.isnan(x)], float)
    if len(v) < 2:
        return ""
    lo, hi = v.min(), v.max()
    rng = (hi - lo) or 1
    xs = np.linspace(2, w - 5, len(v))
    ys = (h - 4) - (v - lo) / rng * (h - 8)
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    col = chg_color(v[-1] - v[0])
    area = f"M{xs[0]:.1f},{h} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + f" L{xs[-1]:.1f},{h} Z"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<path d="{area}" fill="{col}" opacity="0.10"/>'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.6" '
            f'stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="2.6" fill="{col}"/></svg>')


def tile(t):
    k = t["kind"]
    d1 = t["d1"]
    rng52 = t["hi52"] - t["lo52"]
    pos = (t["last"] - t["lo52"]) / rng52 * 100 if rng52 else 50
    tip = (f"{t['label']} · 5D {fmt_chg(t['d5'], k)} · 1M {fmt_chg(t['m1'], k)} · "
           f"3M {fmt_chg(t['m3'], k)} · YTD {fmt_chg(t['ytd'], k)}")
    return f"""
<div class="tile" title="{html.escape(tip)}">
  <div class="t-head"><span class="t-name">{html.escape(t['label'])}</span>
    <span class="t-chg" style="color:{chg_color(d1)}">{arrow(d1)} {fmt_chg(d1, k)}</span></div>
  <div class="t-val">{fmt_val(t['last'], k)}</div>
  {sparkline(t['spark'])}
  <div class="t-foot"><span>5D <b style="color:{chg_color(t['d5'])}">{fmt_chg(t['d5'], k, 1)}</b></span>
    <span>1M <b style="color:{chg_color(t['m1'])}">{fmt_chg(t['m1'], k, 1)}</b></span>
    <span>YTD <b style="color:{chg_color(t['ytd'])}">{fmt_chg(t['ytd'], k, 1)}</b></span></div>
  <div class="rng" title="52-week range: {fmt_val(t['lo52'], k)} – {fmt_val(t['hi52'], k)}">
    <span class="rng-lo">52w</span><div class="rng-bar"><i style="left:{pos:.0f}%"></i></div></div>
</div>"""


def tiles_block(grp, tiles):
    return '<div class="tiles">' + "".join(tile(tiles[t]) for _, t, _ in grp if t in tiles) + "</div>"


# ── charts ──────────────────────────────────────────────────────────────────
def chart_regime(score):
    col = UP if score >= 67 else DOWN if score <= 33 else "#c98500"
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=score, number=dict(font=dict(size=40, color=INK), suffix=""),
        gauge=dict(axis=dict(range=[0, 100], tickvals=[0, 33, 67, 100], tickfont=dict(color=MUTED, size=10)),
                   bar=dict(color=col, thickness=0.28), bgcolor=SURF, borderwidth=0,
                   steps=[dict(range=[0, 33], color=_mix(SURF, DOWN, .22)),
                          dict(range=[33, 67], color=_mix(SURF, "#c98500", .18)),
                          dict(range=[67, 100], color=_mix(SURF, UP, .22))])))
    return base_layout(fig, 190, margin=dict(l=24, r=24, t=16, b=0))


def chart_intraday(intra):
    if not intra or not intra["series"]:
        return None
    fig = go.Figure()
    ends = []
    for i, (label, s) in enumerate(intra["series"].items()):
        x = s.index.tz_localize(None) if s.index.tz is not None else s.index
        fig.add_trace(go.Scatter(x=x, y=s.values, mode="lines", name=label,
                                 line=dict(color=SERIES[i % 4], width=2),
                                 hovertemplate=f"{label.split(' (')[0]}  %{{y:+.2f}}%<extra></extra>"))
        ends.append([float(s.values[-1]), label.split(" (")[0], x[-1], SERIES[i % 4]])
    # direct end labels, nudged apart so they never overlap
    allv = np.concatenate([s.values for s in intra["series"].values()])
    gap = max(float(np.nanmax(allv) - np.nanmin(allv)), 0.4) * 0.075
    ends.sort(key=lambda e: e[0])
    ys = [e[0] for e in ends]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    for (v, name, xe, col), y in zip(ends, ys):
        fig.add_annotation(x=xe, y=y, text=f"<b>{name}</b> {v:+.2f}%", showarrow=False, xanchor="left",
                           xshift=8, font=dict(color=INK2, size=11))
    day = pd.Timestamp(intra["day"])
    fig.add_hline(y=0, line=dict(color=BASE, width=1))
    base_layout(fig, 300, hovermode="x unified", margin=dict(l=8, r=150, t=8, b=8))
    fig.update_yaxes(ticksuffix="%", zeroline=False)
    fig.update_xaxes(tickformat="%H:%M", showgrid=False,
                     range=[day + pd.Timedelta(hours=9, minutes=30), day + pd.Timedelta(hours=16)])
    return fig


def chart_bars(labels, vals, height=None, hover_extra=None):
    order = np.argsort(vals)
    labels = [labels[i] for i in order]
    vals = [vals[i] for i in order]
    extra = [hover_extra[i] for i in order] if hover_extra else labels
    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h", marker=dict(color=[chg_color(v) for v in vals], line=dict(width=0)),
        text=[f"{v:+.2f}%" for v in vals], textposition="outside", textfont=dict(color=INK2, size=11),
        customdata=extra, hovertemplate="%{customdata}: %{x:+.2f}%<extra></extra>", cliponaxis=False))
    span = max(abs(min(vals)), abs(max(vals)), 0.5) * 1.35
    base_layout(fig, height or max(240, 26 * len(vals) + 30), bargap=0.28,
                margin=dict(l=8, r=16, t=4, b=8))
    fig.update_xaxes(range=[-span, span], ticksuffix="%", zeroline=True, zerolinecolor=BASE, showgrid=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK2, size=12))
    return fig


def chart_treemap(u: pd.DataFrame):
    if u.empty:
        return None
    ids, labels, parents, values, colors, custom = ["S&P 500"], ["S&P 500"], [""], [u.cap.sum()], \
        [float(np.average(u.pct, weights=u.cap))], [["S&P 500", "", np.average(u.pct, weights=u.cap)]]
    for sec, g in u.groupby("sector"):
        w = float(np.average(g.pct, weights=g.cap))
        ids.append(sec); labels.append(sec); parents.append("S&P 500")
        values.append(g.cap.sum()); colors.append(w); custom.append([sec, "", w])
    for _, r in u.iterrows():
        ids.append(f"{r.sector}/{r.ticker}"); labels.append(r.ticker); parents.append(r.sector)
        values.append(r.cap); colors.append(r.pct)
        custom.append([r["name"], f"${r.price:,.2f}", r.pct])
    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=colors, colorscale=DIVERGING, cmin=-3, cmid=0, cmax=3,
                    line=dict(color=PAGE, width=1.2), pad=dict(t=20, l=2, r=2, b=2),
                    colorbar=dict(title="", ticksuffix="%", tickvals=[-3, -2, -1, 0, 1, 2, 3],
                                  thickness=10, len=0.6, tickfont=dict(color=MUTED, size=10),
                                  outlinewidth=0)),
        customdata=custom,
        texttemplate="<b>%{label}</b><br>%{customdata[2]:+.2f}%",
        textfont=dict(color="#ffffff", size=13), textposition="middle center",
        hovertemplate="<b>%{label}</b> %{customdata[0]}<br>%{customdata[1]}  %{customdata[2]:+.2f}%<extra></extra>",
        pathbar=dict(visible=True, textfont=dict(size=12)), maxdepth=3, tiling=dict(pad=0)))
    return base_layout(fig, 640, margin=dict(l=0, r=0, t=26, b=0))


def chart_ratios(ratios):
    if not ratios:
        return None
    cols = 3
    rows = math.ceil(len(ratios) / cols)
    titles = [f"{r['label']}  {'▲ risk-on' if r['on'] else '▼ risk-off'}" for r in ratios]
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles, vertical_spacing=0.18,
                        horizontal_spacing=0.06)
    for i, r in enumerate(ratios):
        rr, cc = i // cols + 1, i % cols + 1
        s, ma = r["series"], r["ma"]
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines", line=dict(color=SERIES[0], width=2),
                                 hovertemplate="%{x|%b %d}  %{y:.4f}<extra>ratio</extra>"), rr, cc)
        fig.add_trace(go.Scatter(x=ma.index, y=ma.values, mode="lines",
                                 line=dict(color=MUTED, width=1.3, dash="dot"),
                                 hovertemplate="%{x|%b %d}  %{y:.4f}<extra>50-day avg</extra>"), rr, cc)
    for i, a in enumerate(fig.layout.annotations):
        a.font = dict(size=12, color=UP if ratios[i]["on"] else DOWN)
    base_layout(fig, 220 * rows + 20, margin=dict(l=8, r=8, t=30, b=8))
    fig.update_xaxes(showgrid=False, tickformat="%b", nticks=6)
    fig.update_yaxes(showticklabels=False)
    return fig


def chart_curve(m):
    pts = [("3M", "^IRX"), ("2Y", "2YY=F"), ("5Y", "^FVX"), ("10Y", "^TNX"), ("30Y", "^TYX")]
    pts = [(l, t) for l, t in pts if t in m and m[t].dropna().size > 25]
    if len(pts) < 3:
        return None
    now = [float(m[t].dropna().iloc[-1]) for _, t in pts]
    ago = [float(m[t].dropna().iloc[-22]) for _, t in pts]
    x = [l for l, _ in pts]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=ago, mode="lines+markers", name="1 month ago",
                             line=dict(color=MUTED, width=2, dash="dot"), marker=dict(size=8),
                             hovertemplate="%{x} %{y:.2f}%<extra>1 month ago</extra>"))
    fig.add_trace(go.Scatter(x=x, y=now, mode="lines+markers+text", name="Today",
                             line=dict(color=SERIES[0], width=2.5), marker=dict(size=9),
                             text=[f"{v:.2f}%" for v in now], textposition="top center",
                             textfont=dict(color=INK, size=11),
                             hovertemplate="%{x} %{y:.2f}%<extra>today</extra>"))
    base_layout(fig, 260, showlegend=True,
                legend=dict(orientation="h", y=-0.15, x=0, font=dict(color=INK2)))
    lo, hi = min(now + ago), max(now + ago)
    fig.update_yaxes(ticksuffix="%", range=[lo - 0.2, hi + 0.4])
    fig.update_xaxes(showgrid=False)
    return fig


# ── HTML blocks ─────────────────────────────────────────────────────────────
def sector_table(sectors):
    cols = [("d1", "1D"), ("d5", "5D"), ("m1", "1M"), ("m3", "3M"), ("ytd", "YTD")]
    scale = {k: max([abs(s[k]) for s in sectors if not np.isnan(s[k])] + [0.5]) for k, _ in cols}
    srt = sorted(sectors, key=lambda s: -s["d1"])
    head = "".join(f"<th>{h}</th>" for _, h in cols)
    body = ""
    for s in srt:
        cells = "".join(f'<td style="background:{cell_bg(s[k], scale[k])}">{fmt_chg(s[k], "", 1)}</td>'
                        for k, _ in cols)
        body += f'<tr><th class="sl">{s["name"]} <span class="mut">{s["ticker"]}</span></th>{cells}</tr>'
    return f'<table class="heat"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>'


def breadth_block(b):
    n = max(b["n"], 1)
    pa, pu, pd_ = b["adv"] / n * 100, b["unch"] / n * 100, b["dec"] / n * 100

    def meter(label, v):
        c = UP if v >= 50 else DOWN
        return (f'<div class="meter"><div class="m-top"><span>{label}</span><b>{v:.0f}%</b></div>'
                f'<div class="m-bar"><i style="width:{v:.0f}%;background:{c}"></i><span class="m-mid"></span></div></div>')

    ew_cw = b["ew"] - b["cw"]
    lead = "Average stock beating the index" if ew_cw > 0 else "Mega-caps doing the heavy lifting"
    return f"""
<div class="breadth">
  <div class="ad">
    <div class="ad-lbl"><span style="color:{UP}">▲ {b['adv']} advancing</span>
      <span class="mut">{b['unch']} flat</span><span style="color:{DOWN}">{b['dec']} declining ▼</span></div>
    <div class="ad-bar"><i style="width:{pa:.1f}%;background:{UP}"></i><i style="width:{pu:.1f}%;background:{BASE}"></i><i style="width:{pd_:.1f}%;background:{DOWN}"></i></div>
  </div>
  {meter('Above 50-day average', b['a50'])}
  {meter('Above 200-day average', b['a200'])}
  <div class="kv">
    <div><span>52-week highs</span><b style="color:{UP}">{b['nh']}</b></div>
    <div><span>52-week lows</span><b style="color:{DOWN}">{b['nl']}</b></div>
    <div><span>Avg stock (equal-wt)</span><b style="color:{chg_color(b['ew'])}">{fmt_chg(b['ew'])}</b></div>
    <div><span>Cap-weighted</span><b style="color:{chg_color(b['cw'])}">{fmt_chg(b['cw'])}</b></div>
  </div>
  <p class="note">{lead} ({ew_cw:+.2f} pts). Universe: {b['n']} S&amp;P 500 stocks.</p>
</div>"""


def movers_block(u):
    if u.empty:
        return ""

    def rows(df):
        mx = max(df.pct.abs().max(), 0.1)
        return "".join(
            f'<tr><td><b>{r.ticker}</b><span class="mut nm">{html.escape(str(r["name"]))[:22]}</span></td>'
            f'<td class="num">${r.price:,.2f}</td>'
            f'<td class="barc"><i style="width:{abs(r.pct) / mx * 100:.0f}%;background:{chg_color(r.pct)}"></i></td>'
            f'<td class="num" style="color:{chg_color(r.pct)}">{r.pct:+.2f}%</td></tr>'
            for _, r in df.iterrows())
    g = u.nlargest(8, "pct")
    l = u.nsmallest(8, "pct")
    return f"""<div class="movers">
  <div><h3>▲ Top gainers</h3><table class="mv">{rows(g)}</table></div>
  <div><h3>▼ Top losers</h3><table class="mv">{rows(l)}</table></div></div>"""


def signals_block(signals, score):
    label = "RISK-ON" if score >= 67 else "RISK-OFF" if score <= 33 else "NEUTRAL"
    col = UP if score >= 67 else DOWN if score <= 33 else "#c98500"
    items = "".join(
        f'<li><span class="ic" style="color:{UP if on else DOWN}">{"✓" if on else "✗"}</span>'
        f'<b>{html.escape(lab)}</b><span class="mut"> {html.escape(txt)}</span></li>'
        for lab, on, txt in signals)
    on_n = sum(1 for s in signals if s[1])
    return (f'<div class="regime-lbl" style="color:{col}">{label}</div>'
            f'<div class="mut c">{on_n} of {len(signals)} signals risk-on</div><ul class="sig">{items}</ul>')


def summary_text(res, session_label, now_ny):
    T = res["tiles"]

    def p(t, short=None, kind=""):
        if t not in T:
            return None
        x = T[t]
        return f"{short or x['label']} {fmt_val(x['last'], x['kind'])} ({fmt_chg(x['d1'], x['kind'])})"
    sec = sorted(res["sectors"], key=lambda s: s["d1"])
    b = res["breadth"]
    score = res["score"]
    regime = "Risk-On" if score >= 67 else "Risk-Off" if score <= 33 else "Neutral"
    lines = [f"📊 Market Pulse — {session_label} · {now_ny:%a %b %d}",
             " | ".join(filter(None, [p("^GSPC", "S&P"), p("^NDX", "NDX"), p("^RUT", "RUT"), p("^GSPTSE", "TSX")])),
             " · ".join(filter(None, [p("^VIX", "VIX"), p("^TNX", "10Y"), p("DX-Y.NYB", "DXY"), p("CAD=X", "USD/CAD")])),
             " · ".join(filter(None, [p("GC=F", "Gold"), p("CL=F", "WTI"), p("BTC-USD", "BTC")]))]
    if sec:
        up = sum(1 for s in sec if s["d1"] > 0)
        lines.append(f"Sectors {up}/{len(sec)} up — best {sec[-1]['name']} {sec[-1]['d1']:+.2f}%, "
                     f"worst {sec[0]['name']} {sec[0]['d1']:+.2f}%")
    lines.append(f"Breadth {b['adv']}▲ / {b['dec']}▼ · {b['a50']:.0f}% above 50-day")
    lines.append(f"Regime: {regime} ({score}/100)")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# PAGE
# ════════════════════════════════════════════════════════════════════════════
CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0d0d0d;color:#f0efec;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1680px;margin:0 auto;padding:18px 20px 40px}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:14px}
h1{margin:0;font-size:26px;letter-spacing:-.3px}
h1 .pill{font-size:13px;vertical-align:middle;margin-left:10px;padding:4px 10px;border-radius:99px;background:#262624;color:#c3c2b7;font-weight:600}
.stamp{color:#898781;font-size:13px;text-align:right}
.summary{white-space:pre-line;color:#c3c2b7;font-size:13px;line-height:1.6;background:#222220;border-radius:10px;padding:10px 14px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(12,1fr)}
.card{background:#1a1a19;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:14px 16px;min-width:0}
.card h2{margin:0 0 10px;font-size:13px;font-weight:600;letter-spacing:.6px;text-transform:uppercase;color:#c3c2b7}
.card h2 .mut{text-transform:none;letter-spacing:0;font-weight:400}
.s12{grid-column:span 12}.s9{grid-column:span 9}.s8{grid-column:span 8}.s7{grid-column:span 7}.s6{grid-column:span 6}.s5{grid-column:span 5}.s4{grid-column:span 4}.s3{grid-column:span 3}
@media(max-width:1200px){.s9,.s8,.s7,.s6,.s5,.s4,.s3{grid-column:span 12}}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:10px}
.tile{background:#222220;border-radius:10px;padding:10px 12px 8px;cursor:default;transition:background .15s}
.tile:hover{background:#2a2a28}
.t-head{display:flex;justify-content:space-between;align-items:baseline;gap:6px}
.t-name{color:#c3c2b7;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t-chg{font-weight:700;font-size:13px;white-space:nowrap}
.t-val{font-size:21px;font-weight:650;margin:2px 0 2px;font-variant-numeric:tabular-nums}
.spark{width:100%;height:34px;display:block}
.t-foot{display:flex;justify-content:space-between;gap:6px;white-space:nowrap;font-size:11px;color:#898781;margin-top:4px;font-variant-numeric:tabular-nums}
.t-foot b{font-weight:600}
.rng{display:flex;align-items:center;gap:6px;margin-top:5px}
.rng-lo{font-size:10px;color:#898781}
.rng-bar{position:relative;flex:1;height:4px;border-radius:2px;background:#383835}
.rng-bar i{position:absolute;top:-3px;width:3px;height:10px;border-radius:2px;background:#f0efec;transform:translateX(-50%)}
.mut{color:#898781}.c{text-align:center}
.regime-lbl{text-align:center;font-size:22px;font-weight:800;letter-spacing:1px;margin-top:-6px}
ul.sig{list-style:none;padding:0;margin:10px 0 0;font-size:12.5px}
ul.sig li{padding:4px 0;border-top:1px solid #2c2c2a}
ul.sig .ic{display:inline-block;width:18px;font-weight:800}
table.heat{width:100%;border-collapse:separate;border-spacing:2px;font-size:12.5px;font-variant-numeric:tabular-nums}
table.heat th{color:#898781;font-weight:600;padding:4px 6px;text-align:center}
table.heat th.sl{text-align:left;color:#f0efec;font-weight:500;white-space:nowrap}
table.heat td{text-align:center;padding:6px 4px;border-radius:4px;color:#fff;font-weight:600}
.breadth .ad-lbl{display:flex;justify-content:space-between;font-size:12.5px;font-weight:600;margin-bottom:6px}
.ad-bar{display:flex;height:14px;border-radius:4px;overflow:hidden;gap:2px}
.ad-bar i{display:block;height:100%}
.meter{margin-top:14px}.m-top{display:flex;justify-content:space-between;font-size:12.5px;color:#c3c2b7;margin-bottom:5px}
.m-bar{position:relative;height:8px;border-radius:4px;background:#2c2c2a}
.m-bar i{display:block;height:100%;border-radius:4px}
.m-mid{position:absolute;left:50%;top:-3px;width:1px;height:14px;background:#898781}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;margin-top:16px}
.kv div{display:flex;justify-content:space-between;font-size:12.5px;color:#c3c2b7;border-bottom:1px solid #2c2c2a;padding-bottom:4px}
.kv b{font-variant-numeric:tabular-nums}
.note{font-size:12px;color:#898781;margin:12px 0 0}
.movers{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:700px){.movers{grid-template-columns:1fr}}
.movers h3{margin:0 0 6px;font-size:12.5px;color:#c3c2b7}
table.mv{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
table.mv td{padding:5px 4px;border-top:1px solid #2c2c2a;white-space:nowrap}
table.mv .nm{margin-left:6px;font-size:11px}
table.mv .num{text-align:right;width:1%}
table.mv .barc{width:28%}
table.mv .barc i{display:block;height:6px;border-radius:3px}
.links{display:flex;gap:10px;flex-wrap:wrap}
.links a{color:#f0efec;text-decoration:none;background:#262624;padding:7px 12px;border-radius:8px;font-size:12.5px}
.links a:hover{background:#30302d}
footer{color:#898781;font-size:11.5px;margin-top:18px}
"""


def build_html(res, data, session_label, now_ny, demo=False):
    T = res["tiles"]
    figs = {}
    figs["regime"] = chart_regime(res["score"])
    figs["intraday"] = chart_intraday(res["intraday"])
    if res["sectors"]:
        figs["sectors"] = chart_bars([f"{s['name']}" for s in res["sectors"]],
                                     [s["d1"] for s in res["sectors"]],
                                     hover_extra=[f"{s['name']} ({s['ticker']})" for s in res["sectors"]])
    xa = [(T[t]["label"], T[t]["d1"]) for grp in (INDEXES, FX_CMD, CRYPTO) for _, t, _ in grp
          if t in T and not np.isnan(T[t]["d1"])]
    if "^VIX" in T:
        xa.append(("VIX", T["^VIX"]["d1"]))
    if xa:
        figs["cross"] = chart_bars([a for a, _ in xa], [b for _, b in xa])
    figs["tree"] = chart_treemap(res["universe"])
    figs["ratios"] = chart_ratios(res["ratios"])
    figs["curve"] = chart_curve(data["macro"])
    F = {k: (fig_html(v, f"fig_{k}") if v is not None else '<p class="mut">No data.</p>') for k, v in figs.items()}

    intra_day = res["intraday"]["day"] if res["intraday"] else None
    summary = summary_text(res, session_label, now_ny)
    now_local = now_ny.astimezone(LOCAL_TZ)
    curve_note = ""
    if "^TNX" in T and "2YY=F" in T:
        sp = (T["^TNX"]["last"] - T["2YY=F"]["last"]) * 100
        curve_note = f'<span class="mut">2s10s spread {sp:+.0f} bp</span>'
    links = "".join(f'<a href="{u}" target="_blank" rel="noopener">{html.escape(l)} ↗</a>' for l, u in LINKS)

    body = f"""
<div class="wrap">
<header>
  <h1>Market Pulse <span class="pill">{session_label}</span>{' <span class="pill">DEMO DATA</span>' if demo else ''}</h1>
  <div class="stamp">{now_local:%A, %B %d %Y · %H:%M} local · {now_ny:%H:%M} New York<br>
  Data via Yahoo Finance · prices may be delayed ~15 min</div>
</header>
<div class="grid">
  <section class="card s3"><h2>Market regime</h2>{F['regime']}{signals_block(res['signals'], res['score'])}</section>
  <section class="card s9"><h2>Major indexes</h2>{tiles_block(INDEXES, T)}
    <h2 style="margin-top:14px">Index futures</h2>{tiles_block(FUTURES, T)}
    <h2 style="margin-top:14px">At a glance</h2><div class="summary">{html.escape(summary.split(chr(10), 1)[1])}</div></section>

  <section class="card s7"><h2>Today's path <span class="mut">% from previous close · {intra_day or ''}</span></h2>{F['intraday']}</section>
  <section class="card s5"><h2>Cross-asset scoreboard <span class="mut">1-day %</span></h2>{F.get('cross', '')}</section>

  <section class="card s12"><h2>S&amp;P 500 heatmap <span class="mut">sized by market cap · coloured by 1-day % · click a sector to zoom</span></h2>{F['tree']}</section>

  <section class="card s5"><h2>Sectors today</h2>{F.get('sectors', '')}</section>
  <section class="card s7"><h2>Sector performance <span class="mut">each column shaded on its own scale</span></h2>{sector_table(res['sectors']) if res['sectors'] else ''}</section>

  <section class="card s6"><h2>Volatility &amp; Treasury yields</h2>{tiles_block(VOL_RATES, T)}</section>
  <section class="card s6"><h2>Yield curve {curve_note}</h2>{F['curve']}</section>

  <section class="card s8"><h2>Dollar, CAD &amp; commodities</h2>{tiles_block(FX_CMD, T)}</section>
  <section class="card s4"><h2>Crypto</h2>{tiles_block(CRYPTO, T)}</section>

  <section class="card s4"><h2>Breadth</h2>{breadth_block(res['breadth'])}</section>
  <section class="card s8"><h2>Top movers <span class="mut">S&amp;P 500</span></h2>{movers_block(res['universe'])}</section>

  <section class="card s12"><h2>Risk-on / risk-off ratios <span class="mut">last 6 months · dotted = 50-day average</span></h2>{F['ratios']}</section>

  <section class="card s12"><h2>Calendars &amp; more</h2><div class="links">{links}</div></section>
</div>
<footer>Hover any tile for 5D / 1M / 3M / YTD. Tick on the 52w bar = where price sits in its 52-week range.
Yields change in basis points. Regime score = share of risk-on signals. For information only — not investment advice.</footer>
</div>"""
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Market Pulse · {session_label} · {now_ny:%b %d}</title>'
            f'<style>{CSS}</style><script>{get_plotlyjs()}</script></head><body>{body}</body></html>'), summary


# ════════════════════════════════════════════════════════════════════════════
# DELIVERY
# ════════════════════════════════════════════════════════════════════════════
def load_settings():
    p = BASE_DIR / "settings.json"
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("telegram_bot_token", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    s.setdefault("telegram_chat_id", os.environ.get("TELEGRAM_CHAT_ID", ""))
    return s


def send_telegram(summary, path, settings):
    tok, chat = settings.get("telegram_bot_token"), settings.get("telegram_chat_id")
    if not tok or not chat:
        print("  ! Telegram not configured (see README)")
        return
    import requests
    base = f"https://api.telegram.org/bot{tok}"
    try:
        requests.post(f"{base}/sendMessage", data={"chat_id": chat, "text": summary}, timeout=20)
        with open(path, "rb") as f:
            requests.post(f"{base}/sendDocument", data={"chat_id": chat},
                          files={"document": (path.name, f, "text/html")}, timeout=60)
        print("  sent to Telegram")
    except Exception as e:
        print(f"  ! Telegram failed: {e}")


def current_session(now_ny):
    if now_ny.weekday() >= 5:
        return None, "Weekend"
    for key, label, a, b in SESSIONS:
        if a <= now_ny.time() < b:
            return key, label
    t = now_ny.time()
    if t < dtime(9, 30):
        return None, "Pre-market"
    if t < dtime(16, 0):
        return None, "Intraday"
    return None, "After hours"


def main():
    ap = argparse.ArgumentParser(description="Market Pulse dashboard")
    ap.add_argument("--auto", action="store_true", help="scheduler mode (build only inside session windows)")
    ap.add_argument("--demo", action="store_true", help="use synthetic data")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--telegram", action="store_true", help="send to Telegram")
    ap.add_argument("--session", help="override session label")
    args = ap.parse_args()

    now_ny = datetime.now(NY)
    key, label = current_session(now_ny)
    if args.auto:
        if key is None:
            print(f"{now_ny:%H:%M} ET - outside snapshot windows ({label}); nothing to do.")
            return
        marker = OUT_DIR / "state" / f"{now_ny:%Y-%m-%d}_{key}.done"
        if marker.exists():
            print(f"{label} snapshot already built today.")
            return
    if args.session:
        label = args.session

    data = fetch_demo() if args.demo else fetch_live()
    print("[4/4] Building the dashboard...")
    res = compute(data)
    page, summary = build_html(res, data, label, now_ny, demo=args.demo)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "archive").mkdir(exist_ok=True)
    latest = OUT_DIR / "market_pulse_latest.html"
    latest.write_text(page, encoding="utf-8")
    slug = (key or label.lower().replace(" ", "-"))
    arch = OUT_DIR / "archive" / f"{now_ny:%Y-%m-%d}_{slug}.html"
    arch.write_text(page, encoding="utf-8")
    print(summary)
    print(f"\nSaved: {latest}")

    settings = load_settings()
    if args.telegram or settings.get("telegram_always"):
        send_telegram(summary, arch, settings)
    if not args.no_open and not settings.get("never_open_browser"):
        webbrowser.open(latest.as_uri())
    if args.auto:
        (OUT_DIR / "state").mkdir(exist_ok=True)
        (OUT_DIR / "state" / f"{now_ny:%Y-%m-%d}_{key}.done").write_text(datetime.now().isoformat())


if __name__ == "__main__":
    # Windows consoles often can't print emoji/arrows -> force UTF-8 output
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        main()
    except Exception:
        import traceback
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "error_log.txt").write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
        print(f"\nSomething went wrong - details saved to {OUT_DIR / 'error_log.txt'}")
        _pause_if_double_clicked()
        sys.exit(1)
