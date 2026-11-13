"""Load CSVs -> SQLite (SQL model) -> run both HX-50 variants -> persist results.

    python -m hx50.build                     # defaults: 50 names, exclude top 10, buffer 10
    python -m hx50.build --n 30 --exclude-top 20 --buffer 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .engine import Params, run, summarise
from . import config

ROOT = Path(__file__).resolve().parent.parent


# build.py = the "conductor". It does not compute anything clever itself; it calls the others in order:
#   CSV files -> SQLite tables -> SQL model -> engine (twice) -> save results -> checksum -> swap in.


def build_db(con: sqlite3.Connection, prices_csv: Path, shares_csv: Path, excluded_csv: Path | None = None) -> None:
    # Step 1: copy the CSV files into the database as "raw_*" tables (untouched input).
    pd.read_csv(prices_csv)[["date", "ticker", "close", "adj_close"]].to_sql(
        "raw_prices", con, if_exists="replace", index=False)
    pd.read_csv(shares_csv)[["ticker", "shares"]].to_sql(
        "raw_shares", con, if_exists="replace", index=False)
    # The list of duplicate share classes to ignore (may not exist -> then an empty list).
    ex = pd.read_csv(excluded_csv)[["ticker"]] if excluded_csv and Path(excluded_csv).exists() else pd.DataFrame({"ticker": []})
    ex.to_sql("raw_excluded", con, if_exists="replace", index=False)
    # Step 2: run the SQL file that cleans, ranks and computes returns.
    con.executescript((ROOT / "sql" / "model.sql").read_text())


# A fingerprint (hash) of files: if even one byte changes, the fingerprint changes.
def _sha(*paths: Path) -> str:
    h = hashlib.sha256()
    for q in paths:
        h.update(Path(q).read_bytes())
    return h.hexdigest()


# Reject impossible settings early with a clear message (e.g. 50 names + skip 500 > 500 stocks available).
def validate(p: Params, n_universe: int) -> None:
    if p.n < 1 or p.exclude_top < 0 or p.buffer < 0 or p.top_buffer < 0:
        raise ValueError("n must be >= 1; exclude_top, buffer and top_buffer must be >= 0")
    if p.n + p.exclude_top > n_universe:
        raise ValueError(f"n + exclude_top = {p.n + p.exclude_top} exceeds universe size {n_universe}")


def build_all(db_path: Path, prices_csv: Path, shares_csv: Path, p: Params,
              excluded_csv: Path | None = None) -> pd.DataFrame:
    """Full, deterministic rebuild. Written to a temp file and swapped in atomically, so a failed
    rebuild (bad params, bad data, crash) leaves the previous good database untouched."""
    # Trick: build into a TEMP file; only when everything succeeded, swap it over the real one.
    # So a failure halfway never leaves a broken database behind.
    db_path = Path(db_path)
    tmp = db_path.with_suffix(".db.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        build_db(con, prices_csv, shares_csv, excluded_csv)
        n_universe = con.execute("SELECT COUNT(DISTINCT ticker) FROM mcap").fetchone()[0]
        validate(p, n_universe)
        # Step 3: run the engine twice - once with the buffer ("stabilised"), once without ("raw") - and compare.
        levels, comps, chs, summ = [], [], [], []
        for variant, stabilised in (("stabilised", True), ("raw", False)):
            lv, cp, ch = run(con, p, stabilised)
            for df in (lv, cp, ch):
                df.insert(0, "variant", variant)
            levels.append(lv); comps.append(cp); chs.append(ch)
            summ.append({"variant": variant, **summarise(lv, ch, p)})
        # Step 4: save the results as tables. The API only ever reads these; it never recalculates.
        pd.concat(levels).to_sql("index_level", con, index=False)
        pd.concat(comps).to_sql("composition_daily", con, index=False)
        pd.concat(chs).to_sql("rebalances", con, index=False)
        s = pd.DataFrame(summ)
        s.to_sql("r5_summary", con, index=False)
        pd.DataFrame([p.__dict__]).to_sql("params", con, index=False)
        # Indexes = fast lookup by (variant, date), which is how the API asks.
        con.execute("CREATE INDEX ix_lv ON index_level(variant, date)")
        con.execute("CREATE INDEX ix_cp ON composition_daily(variant, date)")
        con.execute("CREATE INDEX ix_rb ON rebalances(variant, date)")
        # Reproducibility manifest: same params + same data + same code => same run_id and same numbers.
        # Step 5: write a "receipt" (manifest). run_id is made from settings + code + data fingerprints.
        code = _sha(ROOT / "hx50" / "engine.py", ROOT / "sql" / "model.sql")
        data = _sha(prices_csv, shares_csv, *([excluded_csv] if excluded_csv and Path(excluded_csv).exists() else []))
        run_id = hashlib.sha256(json.dumps([p.__dict__, code, data], sort_keys=True).encode()).hexdigest()[:12]
        d0, d1, nd = con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM calendar").fetchone()
        pd.DataFrame([dict(run_id=run_id, built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           data_sha256=data, code_sha256=code, first_date=d0, last_date=d1, trading_days=nd,
                           universe_size=n_universe, python=platform.python_version(),
                           pandas=pd.__version__, sqlite=sqlite3.sqlite_version)]
                     ).to_sql("manifest", con, index=False)
        con.commit()
    except BaseException:   # anything went wrong -> throw the temp file away, keep the old DB, re-raise the error
        con.close()
        if tmp.exists():
            tmp.unlink()
        raise
    con.close()
    # Step 6: success -> swap. os.replace is atomic: readers see either the old file or the new one, never half.
    os.replace(tmp, db_path)
    return s


# Command-line entry: `python -m hx50.build --n 30 ...` builds the DB with those settings and prints the comparison table.
def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--n", type=int, default=config.N)
    a.add_argument("--exclude-top", type=int, default=config.EXCLUDE_TOP)
    a.add_argument("--buffer", type=int, default=config.BUFFER)
    a.add_argument("--top-buffer", type=int, default=config.TOP_BUFFER, help="optional buffer at the rank 10/11 edge (0 = strict R1)")
    a.add_argument("--base", type=float, default=config.BASE_LEVEL)
    a.add_argument("--cost-bps", type=float, default=config.COST_BPS)
    a.add_argument("--db", default=str(config.DB_PATH))
    a.add_argument("--prices", default=str(config.PRICES_CSV))
    a.add_argument("--shares", default=str(config.SHARES_CSV))
    a.add_argument("--excluded", default=str(config.EXCLUDED_CSV))
    a = a.parse_args()
    p = Params(n=a.n, exclude_top=a.exclude_top, buffer=a.buffer, top_buffer=a.top_buffer, base=a.base, cost_bps=a.cost_bps)
    s = build_all(Path(a.db), Path(a.prices), Path(a.shares), p, Path(a.excluded))
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(s.T.to_string(header=False))


if __name__ == "__main__":
    main()
