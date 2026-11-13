"""HX-50 index engine. Pure logic: takes ranks/returns/rebalance dates, returns DataFrames.

Mechanics (all parameters are arguments so the mandate can be changed live):
  * Weights are tracked as fractions of the portfolio. The level is only ever updated by the
    portfolio return  r_t = sum_i w_i(t-1) * ret_i(t),  L_t = L_{t-1} * (1 + r_t).
    Membership changes alter weights, never the level => R3 holds by construction.
  * On a rebalance day, that day's return is earned by the OLD holdings; selection uses the
    close-of-day ranks and the new equal weights apply from that close (no look-ahead).
  * Between rebalances weights drift with price moves (R2: positions are held).
"""
# ---------------------------------------------------------------------------------------------
# READING GUIDE (plain words)
# This file is the "brain". SQL has already prepared the list of trading days, each stock's
# rank each day, and each stock's return each day. The engine walks the days one by one.
# It keeps three things in hand: the stocks we hold, each one's weight, and the index level.
# The small functions below each do ONE job; run() is just the day-by-day loop that calls them:
#   portfolio_return -> how much the basket moved today
#   drift_weights    -> let winners grow and losers shrink, still adding to 100%
#   select_members   -> pick the stocks (only runs on rebalance days)
#   equal_weights    -> give the picked stocks the same slice each
#   turnover         -> measure how much trading a rebalance caused, and why
# ---------------------------------------------------------------------------------------------
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import pandas as pd

from . import config


# Params = the "settings card" of the index. Everything the client could ask to change lives here,
# so a live change like "make it 30 names" is just a different number, not a code change.
# The defaults come from config.py, so there is a single place to change them.
@dataclass
class Params:
    n: int = config.N                     # number of constituents
    exclude_top: int = config.EXCLUDE_TOP  # largest names excluded (R1)
    buffer: int = config.BUFFER            # R5: a member survives while rank <= exclude_top + n + buffer
    top_buffer: int = config.TOP_BUFFER    # R5 (optional): member survives until rank <= exclude_top - top_buffer. 0 = strict
    base: float = config.BASE_LEVEL        # starting level (R3)
    cost_bps: float = config.COST_BPS      # assumed one-way trading cost, only used for the cost estimate
    window: int = config.REENTRY_WINDOW    # re-entry window in rebalances for the churn metric


def load_inputs(con: sqlite3.Connection):
    # Read the SQL result tables and turn them into plain Python dictionaries.
    # Dictionaries make the daily loop easy to read: ranks["2026-05-04"]["AAPL"] -> 3
    cal = [r[0] for r in con.execute("SELECT date FROM calendar ORDER BY date")]    # trading days, in order
    rebal = {r[0] for r in con.execute("SELECT date FROM rebalance_dates")}          # days we rebalance on
    ranks: dict[str, dict[str, int]] = {}                                            # date -> {ticker: rank}
    for d, t, rk in con.execute("SELECT date, ticker, rk FROM ranked"):
        ranks.setdefault(d, {})[t] = rk
    rets: dict[str, dict[str, float]] = {}                                           # date -> {ticker: return}
    for d, t, r in con.execute("SELECT date, ticker, ret FROM returns"):
        rets.setdefault(d, {})[t] = r
    return cal, rebal, ranks, rets


# --- the small single-job helpers -------------------------------------------------------------

def equal_weights(members: list[str], n: int) -> dict[str, float]:
    """Give each held name the same slice (1/n)."""
    return {t: 1.0 / n for t in members}


def portfolio_return(weights: dict[str, float], day_rets: dict[str, float]) -> float:
    """Today's basket return = sum of (each slice x that stock's return). Missing return => 0."""
    return sum(w * day_rets.get(t, 0.0) for t, w in weights.items())


def drift_weights(weights: dict[str, float], day_rets: dict[str, float], r: float) -> dict[str, float]:
    """Let the slices move with prices. Dividing by (1 + r) keeps them adding up to 100%."""
    return {t: w * (1.0 + day_rets.get(t, 0.0)) / (1.0 + r) for t, w in weights.items()}


def select_members(rank_map: dict[str, int], current: list[str], p: Params, stabilised: bool) -> list[str]:
    """Pick the n members for the next period from today's ranks."""
    k, n = p.exclude_top, p.n
    # Everyone allowed in: rank is below the excluded top group. Sorted best rank first.
    candidates = sorted((t for t, r in rank_map.items() if r > k), key=rank_map.get)
    if len(candidates) < n:
        raise ValueError(f"only {len(candidates)} eligible names, need {n}")   # not enough data to fill the index
    if not stabilised or not current:
        return candidates[:n]                       # plain rule: ranks k+1 .. k+n
    # --- Stabilised rule (the "buffer") ---
    # Do not drop a current member just because it slipped a little past the line. Keep incumbents
    # while they stay inside the wider band, then fill empty seats with the best-ranked non-members.
    keep = sorted((t for t in current if t in rank_map and k - p.top_buffer < rank_map[t] <= k + n + p.buffer),
                  key=rank_map.get)
    kept = set(keep)
    fill = [t for t in candidates if t not in kept][: n - len(keep)]
    return keep + fill


def turnover(drifted: dict[str, float], new_w: dict[str, float]):
    """How much trading a rebalance caused, split so it is clear WHY it is non-zero.
      membership = trading names that enter/leave     reweight = trimming/topping-up kept names to equal.
    The 0.5 makes it one-way (a sell of X and a buy of X count as X, not 2X)."""
    added = set(new_w) - set(drifted)
    dropped = set(drifted) - set(new_w)
    retained = set(new_w) & set(drifted)
    t_member = 0.5 * (sum(new_w[t] for t in added) + sum(drifted[t] for t in dropped))
    t_reweight = 0.5 * sum(abs(new_w[t] - drifted[t]) for t in retained)
    return added, dropped, t_member, t_reweight


# --- the day-by-day loop ----------------------------------------------------------------------

def run(con: sqlite3.Connection, p: Params, stabilised: bool):
    # `stabilised` is the ONLY difference between the two index versions. Same loop, same maths.
    cal, rebal, ranks, rets = load_inputs(con)
    level_rows, comp_rows, change_rows = [], [], []   # three output "notebooks" we fill as we go
    members: list[str] = []                            # stocks we hold right now
    weights: dict[str, float] = {}                     # each one's slice of the portfolio (adds to 1.0)
    level = p.base                                     # the index number, starts at 1000

    for i, d in enumerate(cal):                        # one loop pass = one trading day
        rmap = ranks.get(d, {})                        # today's ranks
        if i == 0:
            # Day 1: nothing to earn yet. Build the first portfolio with equal weights.
            members = select_members(rmap, [], p, stabilised)
            weights = equal_weights(members, p.n)
            change_rows.append(dict(date=d, added=members, dropped=[], turnover=1.0,
                                    turnover_membership=1.0, turnover_reweight=0.0, is_initial=1))
            r = 0.0
        else:
            dr = rets.get(d, {})
            r = portfolio_return(weights, dr)          # (1) how much did the basket move today?
            level *= 1.0 + r                           # (2) move the level - the ONLY place it changes
            drifted = drift_weights(weights, dr, r)    # (3) let the slices drift with prices
            if d in rebal:
                # (4a) rebalance: pick again on today's ranks, reset to equal weights, measure the trading.
                new_members = select_members(rmap, members, p, stabilised)
                new_w = equal_weights(new_members, p.n)
                added, dropped, t_member, t_reweight = turnover(drifted, new_w)
                change_rows.append(dict(
                    date=d, added=sorted(added), dropped=sorted(dropped),
                    turnover=t_member + t_reweight, turnover_membership=t_member,
                    turnover_reweight=t_reweight, is_initial=0))
                members, weights = new_members, new_w  # the new portfolio takes over from today's close
            else:
                weights = drifted                      # (4b) normal day: just carry the drifted weights forward
        # Save today's index number. `rebalanced` flag lets the UI mark rebalance days.
        level_rows.append(dict(date=d, level=level, daily_return=r, cum_return=level / p.base - 1.0,
                               rebalanced=int(i == 0 or d in rebal)))
        # Save a snapshot of the holdings at today's close (powers the "composition on date X" view).
        for t, w in weights.items():
            comp_rows.append(dict(date=d, ticker=t, weight=w, rank=rmap.get(t)))

    # Turn the three notebooks into tables. build.py writes them into the database.
    level_df = pd.DataFrame(level_rows)
    comp_df = pd.DataFrame(comp_rows)
    ch = pd.DataFrame(change_rows)
    ch["n_added"] = ch["added"].map(len)
    ch["n_dropped"] = ch["dropped"].map(len)
    # Lists cannot be stored in a SQL cell, so store them as JSON text; queries.py turns them back.
    ch["added"] = ch["added"].map(json.dumps)
    ch["dropped"] = ch["dropped"].map(json.dumps)
    return level_df, comp_df, ch


def summarise(level_df: pd.DataFrame, changes: pd.DataFrame, p: Params) -> dict:
    """R5 comparison metrics for one variant (initial construction excluded)."""
    # The scoreboard used to compare stabilised vs plain: how much trading, how much churn, what return.
    rb = changes[changes["is_initial"] == 0].reset_index(drop=True)   # skip day-1 build (100% by definition)
    last_drop: dict[str, int] = {}     # ticker -> the rebalance number when it was last dropped
    reentries = 0                      # "yo-yo" count: dropped, then bought back soon after
    for idx, row in rb.iterrows():
        for t in json.loads(row["added"]):
            if t in last_drop and idx - last_drop[t] <= p.window:
                reentries += 1
        for t in json.loads(row["dropped"]):
            last_drop[t] = idx
    total_turnover = float(rb["turnover"].sum())
    cum = float(level_df["cum_return"].iloc[-1])
    drag = 2.0 * total_turnover * p.cost_bps / 1e4      # buys + sells, each side costs cost_bps
    return dict(
        rebalances=int(len(rb)),
        avg_turnover=float(rb["turnover"].mean()) if len(rb) else 0.0,
        total_turnover=total_turnover,
        total_turnover_membership=float(rb["turnover_membership"].sum()),
        total_turnover_reweight=float(rb["turnover_reweight"].sum()),
        names_added=int(rb["n_added"].sum()),
        names_dropped=int(rb["n_dropped"].sum()),
        reentries_within_window=reentries,
        cum_return_gross=cum,
        est_cost_drag=drag,
        cum_return_net_approx=cum - drag,
        final_level=float(level_df["level"].iloc[-1]),
    )
