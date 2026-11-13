"""API contract tests (need fastapi + httpx). Run: pytest -q"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic
from hx50.build import build_all
from hx50.engine import Params


@pytest.fixture(scope="module")
def client():
    tmp = Path(tempfile.mkdtemp())
    prices, shares = synthetic.make()
    (tmp / "data").mkdir()
    prices.to_csv(tmp / "data" / "prices.csv", index=False)
    shares.to_csv(tmp / "data" / "shares.csv", index=False)
    db = tmp / "data" / "hx50.db"
    build_all(db, tmp / "data" / "prices.csv", tmp / "data" / "shares.csv", Params())
    from fastapi.testclient import TestClient
    from hx50 import api
    api.DB_PATH = str(db)
    api.ROOT = tmp          # /rebuild reads <ROOT>/data/*.csv
    return TestClient(api.app)


def test_index(client):
    r = client.get("/index", params={"start": "2026-01-05", "end": "2026-02-27"}).json()
    assert r[0]["level"] == 1000.0 and r[0]["cumulative_return"] == 0
    assert all(set(x) >= {"date", "level", "daily_return", "cumulative_return"} for x in r)


def test_composition_weights_are_current_not_static(client):
    rebal_day = client.get("/composition", params={"date": "2026-01-12"}).json()      # a Monday rebalance
    mid_week = client.get("/composition", params={"date": "2026-01-14"}).json()
    assert {c["weight"] for c in rebal_day["constituents"]} == {0.02}
    assert len({c["weight"] for c in mid_week["constituents"]}) > 10                  # drifted
    assert abs(mid_week["weight_sum"] - 1) < 1e-9 and mid_week["count"] == 50


def test_composition_non_trading_day_and_errors(client):
    assert client.get("/composition", params={"date": "2026-01-19"}).json()["as_of"] == "2026-01-16"
    assert client.get("/composition", params={"date": "2020-01-01"}).status_code == 404
    assert client.get("/composition", params={"date": "garbage"}).status_code == 422
    assert client.get("/index", params={"variant": "nope"}).status_code == 422


def test_changes_and_summary(client):
    ch = client.get("/changes").json()
    assert ch[0]["initial_construction"] and all("turnover" in c for c in ch)
    s = {r["variant"]: r for r in client.get("/summary").json()}
    assert s["stabilised"]["total_turnover"] < s["raw"]["total_turnover"]


def test_rebuild_changes_mandate_and_bad_mandate_is_rejected(client):
    assert client.post("/rebuild", params={"n": 30, "exclude_top": 20, "buffer": 5}).status_code == 200
    assert client.get("/composition", params={"date": "2026-03-02"}).json()["count"] == 30
    assert client.post("/rebuild", params={"n": 500, "exclude_top": 10, "buffer": 5}).status_code == 422
    assert client.get("/composition", params={"date": "2026-03-02"}).json()["count"] == 30   # still serving
