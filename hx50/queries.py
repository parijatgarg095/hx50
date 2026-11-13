"""Read-side queries used by the API (kept free of FastAPI so they can be tested directly)."""
from __future__ import annotations

import json
import sqlite3


# Each function below = one SQL read, shaped into plain Python dicts. The API just returns them as JSON.
# They only SELECT from tables that build.py already filled; nothing is calculated here.


def get_index(con: sqlite3.Connection, variant: str, start: str | None, end: str | None):
    rows = con.execute(
        "SELECT date, level, daily_return, cum_return, rebalanced FROM index_level "
        "WHERE variant=? AND date>=COALESCE(?, date) AND date<=COALESCE(?, date) ORDER BY date",
        (variant, start, end)).fetchall()
    return [dict(date=d, level=round(l, 4), daily_return=round(r, 6), cumulative_return=round(c, 6),
                 rebalanced=bool(rb)) for d, l, r, c, rb in rows]


def get_composition(con: sqlite3.Connection, variant: str, date: str):
    """Holdings at the close of `date`; a non-trading date resolves to the latest earlier trading day."""
    # If the asked date is a weekend/holiday, use the latest earlier trading day.
    row = con.execute("SELECT MAX(date) FROM composition_daily WHERE variant=? AND date<=?",
                      (variant, date)).fetchone()
    as_of = row[0]
    if as_of is None:
        return None      # asked for a date before the index began (API turns this into a 404)
    rows = con.execute(
        "SELECT ticker, weight, rank FROM composition_daily WHERE variant=? AND date=? "
        "ORDER BY weight DESC, ticker", (variant, as_of)).fetchall()
    return dict(requested=date, as_of=as_of, count=len(rows),
                weight_sum=round(sum(w for _, w, _ in rows), 10),
                constituents=[dict(ticker=t, weight=round(w, 6), market_cap_rank=rk) for t, w, rk in rows])


def get_changes(con: sqlite3.Connection, variant: str, start: str | None, end: str | None):
    rows = con.execute(
        "SELECT date, added, dropped, turnover, is_initial, turnover_membership, turnover_reweight FROM rebalances "
        "WHERE variant=? AND date>=COALESCE(?, date) AND date<=COALESCE(?, date) ORDER BY date",
        (variant, start, end)).fetchall()
    return [dict(date=d, added=json.loads(a), dropped=json.loads(dr), turnover=round(t, 6),
                 turnover_membership=round(tm, 6), turnover_reweight=round(tr, 6),
                 initial_construction=bool(i)) for d, a, dr, t, i, tm, tr in rows]


def get_summary(con: sqlite3.Connection):
    cur = con.execute("SELECT * FROM r5_summary")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_meta(con: sqlite3.Connection):
    out = {}
    for name in ("manifest", "params"):
        cur = con.execute(f"SELECT * FROM {name}")
        cols = [c[0] for c in cur.description]
        out[name] = dict(zip(cols, cur.fetchone()))
    return out
