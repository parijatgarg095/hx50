"""One line per company: drop secondary share classes.

Yahoo reports the COMPANY's total shares for every listed class (GOOGL and GOOG both show 12.23bn),
so price x shares values each class at the whole company's cap. Left alone, Alphabet would take two of
the top-10 slots and FOX/FOXA and NWS/NWSA would each appear twice near the index boundary.
Rule: tickers with an identical share count are one company; keep the class with the higher last close
(the larger line) and exclude the others from the ranking universe.

    python -m hx50.dedupe        # regenerates data/excluded_classes.csv from data/prices.csv + shares.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"


def secondary_share_classes(shares: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    # Latest closing price for each ticker (used to decide which share class to keep).
    last = prices.sort_values("date").groupby("ticker")["close"].last()
    rows = []
    # Group tickers that report the exact same share count -> they are the same company.
    for _, grp in shares.dropna(subset=["shares"]).groupby("shares"):
        tickers = list(grp["ticker"])
        if len(tickers) < 2:
            continue
        # Keep the class with the higher price (the main line); the rest go on the exclusion list.
        keep = max(tickers, key=lambda t: (last.get(t, 0.0), t))
        rows += [dict(ticker=t, kept_ticker=keep, reason="same company, secondary share class")
                 for t in tickers if t != keep]
    return pd.DataFrame(rows, columns=["ticker", "kept_ticker", "reason"])


def main() -> None:
    ex = secondary_share_classes(pd.read_csv(DATA / "shares.csv"), pd.read_csv(DATA / "prices.csv"))
    ex.to_csv(DATA / "excluded_classes.csv", index=False)
    print(ex.to_string(index=False) if len(ex) else "no secondary share classes found")


if __name__ == "__main__":
    main()
