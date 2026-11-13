"""Download the HX-50 inputs with yfinance -> data/prices.csv, data/shares.csv, data/universe.csv.

    python fetch_data.py                      # last ~7 months of the current S&P 500
    python fetch_data.py --start 2026-03-01 --end 2026-10-16

Universe: current S&P 500 constituents (Wikipedia list) - ~500 liquid US large caps (D2).
Prices:   Yahoo Finance daily bars via yfinance; `close` is split-adjusted, `adj_close` also
          adjusts for dividends (D1).
Shares:   Yahoo's CURRENT shares-outstanding snapshot, fetched today (D4 limitation: not point-in-time).
"""
from __future__ import annotations

import argparse
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hx50 import config          # all defaults (dates, retries, threads, paths, URL) live here
from hx50.dedupe import secondary_share_classes

DATA = config.DATA_DIR
UA = {"User-Agent": config.USER_AGENT}


# fetch_data.py = the "shopper". Run once with internet; it saves CSVs so everything else works offline.


def sp500() -> pd.DataFrame:
    html = requests.get(config.UNIVERSE_URL, headers=UA, timeout=30).text
    df = pd.read_html(io.StringIO(html))[0]
    df = df.rename(columns={"Symbol": "wiki_symbol", "Security": "name", "GICS Sector": "sector"})
    df["ticker"] = df["wiki_symbol"].str.replace(".", "-", regex=False)      # BRK.B -> BRK-B (Yahoo style)
    return df[["ticker", "name", "sector"]]


# Ask Yahoo for one stock's share count. Retries 3 times (Yahoo sometimes fails randomly). None = unknown.
def get_shares(t: str):
    for attempt in range(config.SHARE_FETCH_RETRIES):
        try:
            s = yf.Ticker(t).fast_info["shares"]
            if s and s > 0:
                return t, float(s)
            return t, None
        except Exception:
            time.sleep(1 + attempt)
    return t, None


# Download daily close + adjusted close for all tickers in one bulk call, returned as a tidy table.
def download_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    raw = yf.download(tickers, start=start, end=end, auto_adjust=False, group_by="ticker",
                      threads=True, progress=False)
    frames = []
    for t in tickers:
        if t not in raw.columns.get_level_values(0):
            continue
        d = raw[t][["Close", "Adj Close"]].dropna().reset_index()
        d.columns = ["date", "close", "adj_close"]
        d["ticker"] = t
        frames.append(d)
    prices = pd.concat(frames)
    prices["date"] = pd.to_datetime(prices["date"]).dt.strftime("%Y-%m-%d")
    return prices[["date", "ticker", "close", "adj_close"]]


# Share count per ticker (threaded). Yahoo's current snapshot, so stamped with today's date.
def download_shares(tickers) -> pd.DataFrame:
    with ThreadPoolExecutor(config.SHARE_FETCH_THREADS) as ex:
        res = list(ex.map(get_shares, tickers))
    sh = pd.DataFrame(res, columns=["ticker", "shares"]).dropna()
    sh["asof"] = date.today().isoformat()
    sh["source"] = "yfinance fast_info.shares (current snapshot)"
    return sh


# main = orchestration only: fetch each piece, save it, print a one-line summary. The work is in the
# functions above and in sp500() / secondary_share_classes().
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.default_start())
    ap.add_argument("--end", default=config.default_end())
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)

    uni = sp500()                                           # 1) the company list
    uni.to_csv(config.UNIVERSE_CSV, index=False)
    tickers = uni["ticker"].tolist()
    print(f"universe: {len(tickers)} tickers")

    prices = download_prices(tickers, a.start, a.end)       # 2) prices
    prices.to_csv(config.PRICES_CSV, index=False)
    print(f"prices: {len(prices)} rows, {prices.ticker.nunique()} tickers, "
          f"{prices.date.nunique()} trading days ({prices.date.min()} -> {prices.date.max()})")

    sh = download_shares(prices.ticker.unique())            # 3) share counts
    sh.to_csv(config.SHARES_CSV, index=False)
    print(f"shares: {len(sh)} tickers with a share count")

    ex = secondary_share_classes(sh, prices)               # 4) drop duplicate share classes
    ex.to_csv(config.EXCLUDED_CSV, index=False)
    print("secondary share classes excluded (one line per company):")
    print(ex.to_string(index=False) if len(ex) else "  none")


if __name__ == "__main__":
    main()
