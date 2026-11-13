"""Validation tests. Run: pytest -q   (or: python tests/test_hx50.py)"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import synthetic
from hx50 import queries
from hx50.build import build_all
from hx50.engine import Params

_cache: dict = {}


def built(**kw):
    key = tuple(sorted(kw.items()))
    if key not in _cache:
        tmp = Path(tempfile.mkdtemp())
        prices, shares = synthetic.make()
        prices.to_csv(tmp / "p.csv", index=False)
        shares.to_csv(tmp / "s.csv", index=False)
        p = Params(**kw)
        build_all(tmp / "t.db", tmp / "p.csv", tmp / "s.csv", p)
        _cache[key] = (sqlite3.connect(tmp / "t.db"), p, prices, shares)
    return _cache[key]


# TESTS use a fake market (tests/synthetic.py) so edge cases like holidays and missing prices are guaranteed.
# Each test below checks one promise of the index: weights add to 100%, no top-10 stock held, level never jumps, etc.
def test_weights_sum_to_one_and_n_names():
    con, p, *_ = built()
    for v in ("stabilised", "raw"):
        df = pd.read_sql("SELECT date, COUNT(*) c, SUM(weight) s FROM composition_daily WHERE variant=? GROUP BY date", con, params=(v,))
        assert (df.c == p.n).all()
        assert ((df.s - 1).abs() < 1e-9).all()


def test_no_top_k_ever_held():
    con, p, *_ = built()
    df = pd.read_sql("SELECT date, ticker, rank FROM composition_daily", con)
    # on rebalance days the holdings are chosen from that day's ranks -> none may be in the top k
    rb = {r[0] for r in con.execute("SELECT date FROM rebalance_dates")}
    chosen = df[df.date.isin(rb)].dropna(subset=["rank"])
    assert (chosen["rank"] > p.exclude_top).all()


def test_raw_is_exactly_rank_11_to_60_on_rebalance_days():
    con, p, *_ = built()
    for d, in con.execute("SELECT date FROM rebalance_dates"):
        held = {r[0] for r in con.execute("SELECT ticker FROM composition_daily WHERE variant='raw' AND date=?", (d,))}
        want = {r[0] for r in con.execute(
            "SELECT ticker FROM ranked WHERE date=? AND rk>? AND rk<=?", (d, p.exclude_top, p.exclude_top + p.n))}
        assert held == want, d


def test_rebalance_is_first_trading_day_of_week_and_holiday_handled():
    con, *_ = built()
    rb = [r[0] for r in con.execute("SELECT date FROM rebalance_dates ORDER BY date")]
    assert "2026-01-19" not in rb          # holiday removed from the calendar
    assert "2026-01-20" in rb              # Tuesday becomes the rebalance day
    weeks = pd.to_datetime(pd.Series(rb)).dt.isocalendar()
    assert not weeks[["year", "week"]].duplicated().any()


# The strongest test: recompute the level a DIFFERENT way (count shares held x price) and require it to match the engine.
def test_level_matches_independent_units_based_recomputation():
    """Re-derive the level from *units held* (no weights, no returns): at each rebalance buy
    equal dollar amounts, then mark to market. Must equal the engine's level -> no jumps."""
    con, p, prices, _ = built()
    px = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index().ffill()
    cal = [r[0] for r in con.execute("SELECT date FROM calendar ORDER BY date")]
    rb = {r[0] for r in con.execute("SELECT date FROM rebalance_dates")}
    for v in ("stabilised", "raw"):
        comp = pd.read_sql("SELECT date, ticker FROM composition_daily WHERE variant=?", con, params=(v,))
        members = comp.groupby("date")["ticker"].apply(list).to_dict()
        lv = pd.read_sql("SELECT date, level FROM index_level WHERE variant=? ORDER BY date", con, params=(v,)).set_index("date")["level"]
        units, level = {}, p.base
        for i, d in enumerate(cal):
            if units:
                level = sum(u * px.at[d, t] for t, u in units.items())
            if i == 0 or d in rb:
                units = {t: (level / p.n) / px.at[d, t] for t in members[d]}
            assert abs(level - lv[d]) < 1e-6 * level, (v, d, level, lv[d])


def test_level_starts_at_base_and_no_jump_on_rebalance_days():
    con, p, *_ = built()
    lv = pd.read_sql("SELECT * FROM index_level WHERE variant='stabilised' ORDER BY date", con)
    assert lv.level.iloc[0] == p.base
    # level change == that day's portfolio return on every day, rebalance or not
    assert ((lv.level / lv.level.shift() - 1 - lv.daily_return).abs().dropna() < 1e-12).all()


def test_stabilisation_reduces_churn():
    con, *_ = built()
    s = pd.read_sql("SELECT * FROM r5_summary", con).set_index("variant")
    assert s.at["stabilised", "total_turnover"] < s.at["raw", "total_turnover"]
    assert s.at["stabilised", "names_added"] < s.at["raw", "names_added"]


def test_stabilised_members_stay_inside_buffer_band():
    con, p, *_ = built()
    rb = [r[0] for r in con.execute("SELECT date FROM rebalance_dates ORDER BY date")][1:]
    df = pd.read_sql("SELECT date, ticker, rank FROM composition_daily WHERE variant='stabilised'", con)
    df = df[df.date.isin(rb)].dropna(subset=["rank"])
    assert (df["rank"] <= p.exclude_top + p.n + p.buffer).all()


def test_turnover_bounds_and_mandate_parameters_are_live():
    con, p, *_ = built()
    ch = pd.read_sql("SELECT turnover FROM rebalances WHERE is_initial=0", con)
    assert ((ch.turnover >= 0) & (ch.turnover <= 1)).all()
    con2, p2, *_ = built(n=30, exclude_top=20, buffer=5)
    c = pd.read_sql("SELECT variant, date, COUNT(*) c FROM composition_daily GROUP BY variant, date", con2)
    assert (c.c == 30).all()


def test_queries_shape():
    con, p, *_ = built()
    idx = queries.get_index(con, "stabilised", None, None)
    assert idx[0]["level"] == 1000.0 and idx[0]["cumulative_return"] == 0.0
    comp = queries.get_composition(con, "stabilised", "2026-01-19")   # holiday -> previous trading day
    assert comp["as_of"] == "2026-01-16" and comp["count"] == p.n
    assert queries.get_composition(con, "stabilised", "2025-01-01") is None
    ch = queries.get_changes(con, "raw", None, None)
    assert ch[0]["initial_construction"] and isinstance(ch[1]["added"], list)


def test_composition_weights_drift_between_rebalances_and_match_units():
    """Regression for the 'static weights' bug: weights must be 1/N only on rebalance days and
    must equal units*price/level (market value share) on every day."""
    con, p, prices, _ = built()
    px = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index().ffill()
    cal = [r[0] for r in con.execute("SELECT date FROM calendar ORDER BY date")]
    rb = {r[0] for r in con.execute("SELECT date FROM rebalance_dates")}
    comp = pd.read_sql("SELECT date, ticker, weight FROM composition_daily WHERE variant='stabilised'", con)
    by_date = {d: g.set_index("ticker")["weight"] for d, g in comp.groupby("date")}
    lv = pd.read_sql("SELECT date, level FROM index_level WHERE variant='stabilised'", con).set_index("date")["level"]
    units, drifting_days = {}, 0
    for i, d in enumerate(cal):
        if i == 0 or d in rb:
            assert ((by_date[d] - 1 / p.n).abs() < 1e-12).all(), d       # equal weight right after rebalance
            units = {t: (lv[d] / p.n) / px.at[d, t] for t in by_date[d].index}
        else:
            drifting_days += 1
            assert by_date[d].std() > 1e-6, d                            # not static
            for t, w in by_date[d].items():
                assert abs(w - units[t] * px.at[d, t] / lv[d]) < 1e-9, (d, t)
    assert drifting_days > 50


def test_turnover_decomposition_adds_up_and_explains_nonzero_turnover():
    con, p, *_ = built()
    ch = pd.read_sql("SELECT * FROM rebalances WHERE is_initial=0", con)
    assert ((ch.turnover - ch.turnover_membership - ch.turnover_reweight).abs() < 1e-12).all()
    # even a rebalance with NO membership change has turnover > 0: retained names drifted off 2%
    quiet = ch[(ch.n_added == 0)]
    assert (quiet.turnover_membership == 0).all()
    assert (quiet.turnover_reweight > 0).all()
    # membership part is exactly (adds x 1/N + drops x drifted weight)/2, so with k adds it is >= k/(2N) * (1-drift)
    assert (ch[ch.n_added > 0].turnover_membership > 0).all()


def test_rebuild_is_deterministic_and_failed_rebuild_keeps_old_db():
    import hashlib
    tmp = Path(tempfile.mkdtemp())
    prices, shares = synthetic.make()
    prices.to_csv(tmp / "p.csv", index=False); shares.to_csv(tmp / "s.csv", index=False)
    p = Params()
    build_all(tmp / "t.db", tmp / "p.csv", tmp / "s.csv", p)
    c = sqlite3.connect(tmp / "t.db")
    rid1 = c.execute("SELECT run_id FROM manifest").fetchone()[0]
    lv1 = c.execute("SELECT level FROM index_level ORDER BY variant, date").fetchall(); c.close()
    build_all(tmp / "t.db", tmp / "p.csv", tmp / "s.csv", p)
    c = sqlite3.connect(tmp / "t.db")
    assert c.execute("SELECT run_id FROM manifest").fetchone()[0] == rid1
    assert c.execute("SELECT level FROM index_level ORDER BY variant, date").fetchall() == lv1
    c.close()
    before = hashlib.sha256((tmp / "t.db").read_bytes()).hexdigest()
    try:
        build_all(tmp / "t.db", tmp / "p.csv", tmp / "s.csv", Params(n=500))     # impossible mandate
        raise AssertionError("should have failed")
    except ValueError:
        pass
    assert hashlib.sha256((tmp / "t.db").read_bytes()).hexdigest() == before       # old DB untouched
    assert not (tmp / "t.db.tmp").exists()
    # changed data => different run_id (versioning of corrected prices)
    prices.loc[0, "close"] *= 1.01; prices.to_csv(tmp / "p.csv", index=False)
    build_all(tmp / "t.db", tmp / "p.csv", tmp / "s.csv", p)
    c = sqlite3.connect(tmp / "t.db")
    assert c.execute("SELECT run_id FROM manifest").fetchone()[0] != rid1


def test_split_adjusted_history_with_current_shares_leaves_index_unchanged():
    """A 2:1 split: the vendor restates the whole price history at half the price and the current
    share count doubles. Market cap, ranks and returns are unchanged, so the level must be too.
    (This is why close must be split-adjusted when paired with a current share count.)"""
    tmp = Path(tempfile.mkdtemp())
    prices, shares = synthetic.make()
    a = tmp / "a"; a.mkdir()
    prices.to_csv(a / "p.csv", index=False); shares.to_csv(a / "s.csv", index=False)
    build_all(a / "t.db", a / "p.csv", a / "s.csv", Params())
    q = prices.copy(); m = q.ticker == "T030"
    q.loc[m, ["close", "adj_close"]] = q.loc[m, ["close", "adj_close"]] / 2
    sh2 = shares.copy(); sh2.loc[sh2.ticker == "T030", "shares"] *= 2
    b = tmp / "b"; b.mkdir()
    q.to_csv(b / "p.csv", index=False); sh2.to_csv(b / "s.csv", index=False)
    build_all(b / "t.db", b / "p.csv", b / "s.csv", Params())
    qa = "SELECT level FROM index_level ORDER BY variant, date"
    la = [r[0] for r in sqlite3.connect(a / "t.db").execute(qa)]
    lb = [r[0] for r in sqlite3.connect(b / "t.db").execute(qa)]
    assert max(abs(x - y) / x for x, y in zip(la, lb)) < 1e-9


def test_secondary_share_classes_are_excluded_one_line_per_company():
    """Yahoo gives every class the company's total shares, so a dual-class company would be ranked twice.
    Duplicates (identical share count) must be detected and the secondary class never ranked or held."""
    from hx50.dedupe import secondary_share_classes
    tmp = Path(tempfile.mkdtemp())
    prices, shares = synthetic.make()
    big = shares.sort_values("shares").iloc[-1]["ticker"]               # a large company in the top ranks
    for t, new in (("T040", "T040B"), (big, big + "B")):
        dup = prices[prices.ticker == t].copy(); dup["ticker"] = new; dup["close"] *= 0.99; dup["adj_close"] *= 0.99
        prices = pd.concat([prices, dup])
        shares = pd.concat([shares, pd.DataFrame({"ticker": [new], "shares": [float(shares.loc[shares.ticker == t, "shares"].iloc[0])]})])
    ex = secondary_share_classes(shares, prices)
    assert set(ex.ticker) == {"T040B", big + "B"}                      # lower-priced class dropped
    prices.to_csv(tmp / "p.csv", index=False); shares.to_csv(tmp / "s.csv", index=False); ex.to_csv(tmp / "x.csv", index=False)
    build_all(tmp / "t.db", tmp / "p.csv", tmp / "s.csv", Params(), tmp / "x.csv")
    c = sqlite3.connect(tmp / "t.db")
    assert c.execute("SELECT COUNT(*) FROM ranked WHERE ticker IN ('T040B', ?)", (big + "B",)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM composition_daily WHERE ticker IN ('T040B', ?)", (big + "B",)).fetchone()[0] == 0
    # each remaining date still ranks exactly one line per company
    assert c.execute("SELECT MAX(n) FROM (SELECT COUNT(*) n FROM ranked GROUP BY date)").fetchone()[0] == 120


def test_top_buffer_is_optional_and_strict_by_default():
    from hx50.engine import select_members
    # 80 names ranked 1..80; current members are ranks 9..58 (two of them, ranks 9 and 10, sit inside the top 10)
    rank_map = {f"S{i:02d}": i for i in range(1, 81)}
    current = [f"S{i:02d}" for i in range(9, 59)]
    strict = select_members(rank_map, current, Params(), True)
    assert all(rank_map[t] > 10 for t in strict) and len(strict) == 50          # R1 strict by default
    soft = select_members(rank_map, current, Params(top_buffer=2), True)
    assert {"S09", "S10"} <= set(soft) and len(soft) == 50                      # tolerated inside the buffer
    assert "S08" not in select_members({**rank_map}, ["S08"] + current[:49], Params(top_buffer=2), True)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
