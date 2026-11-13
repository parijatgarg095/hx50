"""Synthetic market for tests: random-walk prices, a holiday, missing prices, a stock split."""
from __future__ import annotations

import numpy as np
import pandas as pd


# Builds a fake market: random prices for 120 stocks, a Monday holiday, and some missing prices.
def make(n_tickers: int = 120, n_days: int = 130, seed: int = 7):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days + 1)
    days = days.delete(days.get_loc(pd.Timestamp("2026-01-19")))     # a Monday holiday
    days = days[:n_days]
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    shares = rng.uniform(0.5e9, 5e9, n_tickers)
    start_px = rng.uniform(20, 400, n_tickers)
    # strong common noise on near-equal caps around the 60/70 boundary creates plenty of crossings
    rets = rng.normal(0.0004, 0.02, (n_days, n_tickers))
    px = start_px * np.cumprod(1 + rets, axis=0)
    rows = []
    for j, t in enumerate(tickers):
        for i, d in enumerate(days):
            rows.append((d.strftime("%Y-%m-%d"), t, px[i, j], px[i, j]))
    prices = pd.DataFrame(rows, columns=["date", "ticker", "close", "adj_close"])
    # missing prices: a few random gaps (incl. around rebalance days)
    drop = rng.choice(len(prices), size=60, replace=False)
    prices = prices.drop(prices.index[drop]).reset_index(drop=True)
    sh = pd.DataFrame({"ticker": tickers, "shares": shares})
    return prices, sh
