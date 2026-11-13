"""D1 evidence: compare yfinance closes with Stooq's for a few tickers over the downloaded period.
Prints the max relative difference per ticker. Paste the result into NOTES.md.   python compare_sources.py
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).resolve().parent / "data"
SAMPLE = ["AAPL", "MSFT", "JPM", "XOM", "KO", "HD"]

prices = pd.read_csv(DATA / "prices.csv")
for t in SAMPLE:
    try:
        r = requests.get(f"https://stooq.com/q/d/l/?s={t.lower()}.us&i=d", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        if "Date" not in r.text[:200]:
            print(f"{t}: stooq returned no CSV (HTTP {r.status_code}): {r.text[:150]!r}")
            continue
        s = pd.read_csv(io.StringIO(r.text))
        s = s.rename(columns={"Date": "date", "Close": "stooq"})[["date", "stooq"]]
        y = prices[prices.ticker == t][["date", "close"]]
        m = y.merge(s, on="date")
        diff = ((m.close - m.stooq).abs() / m.stooq).max()
        print(f"{t}: {len(m)} common days, max rel. diff yfinance vs stooq close = {diff:.4%}")
    except Exception as e:
        print(f"{t}: stooq unavailable ({type(e).__name__}: {e})")
