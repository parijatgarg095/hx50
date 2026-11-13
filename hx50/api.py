"""HX-50 API. Run:  uvicorn hx50.api:app --port 8000   (UI is served at /)."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import date as _date
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .build import build_all
from .engine import Params
from . import config

# api.py = the "waiter". It takes a request from the browser, asks queries.py for the answer, returns JSON.
# Rule: no index maths here. The UI and API only READ what build.py already stored.
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("HX50_DB", str(config.DB_PATH))   # override with HX50_DB env var if needed
Variant = Literal["stabilised", "raw"]

app = FastAPI(title="HX-50 index API")
_rebuild_lock = threading.Lock()   # only one rebuild at a time (a second click gets HTTP 409)


# Open the database READ-ONLY for each request (mode=ro) so a request can never change the data.
def _con() -> sqlite3.Connection:
    if not Path(DB_PATH).exists():
        raise HTTPException(503, "database not built - run: python -m hx50.build")
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


# Validate date text like 2026-05-04; a bad one gives HTTP 422 instead of a confusing crash.
def _check(d: Optional[str], name: str) -> Optional[str]:
    if d is None:
        return None
    try:
        _date.fromisoformat(d)
    except ValueError:
        raise HTTPException(422, f"{name} must be YYYY-MM-DD")
    return d


@app.get("/index")
def index(start: Optional[str] = None, end: Optional[str] = None, variant: Variant = "stabilised"):
    """Index level, daily return and cumulative return since base."""
    con = _con()
    try:
        return queries.get_index(con, variant, _check(start, "start"), _check(end, "end"))
    finally:
        con.close()


@app.get("/composition")
def composition(date: str = Query(...), variant: Variant = "stabilised"):
    """Constituents and weights at the close of `date` (weights drift between rebalances)."""
    con = _con()
    try:
        res = queries.get_composition(con, variant, _check(date, "date"))
    finally:
        con.close()
    if res is None:
        raise HTTPException(404, "date is before the start of the index")
    return res


@app.get("/changes")
def changes(start: Optional[str] = None, end: Optional[str] = None, variant: Variant = "stabilised"):
    """Each rebalance: names added, names dropped, one-way turnover."""
    con = _con()
    try:
        return queries.get_changes(con, variant, _check(start, "start"), _check(end, "end"))
    finally:
        con.close()


@app.get("/summary")
def summary():
    """R5 comparison: stabilised vs raw."""
    con = _con()
    try:
        return queries.get_summary(con)
    finally:
        con.close()


@app.get("/meta")
def meta():
    """Current mandate parameters + reproducibility manifest (run id, data/code hashes, build time)."""
    con = _con()
    try:
        return queries.get_meta(con)
    finally:
        con.close()


@app.post("/rebuild")
def rebuild(n: int = Query(50, ge=1, le=500), exclude_top: int = Query(10, ge=0, le=500),
            buffer: int = Query(10, ge=0, le=500), top_buffer: int = Query(0, ge=0, le=500)):
    """Re-run the whole pipeline with a different mandate. Atomic: on failure the old index stays live."""
    # This is the "live mandate change" button. It reuses build_all - the exact same code as the command line.
    if not _rebuild_lock.acquire(blocking=False):
        raise HTTPException(409, "a rebuild is already running")
    try:
        old = Params()
        try:
            c = _con(); old = Params(**queries.get_meta(c)["params"]); c.close()
        except Exception:
            pass
        p = Params(n=n, exclude_top=exclude_top, buffer=buffer, top_buffer=top_buffer, base=old.base, cost_bps=old.cost_bps)
        try:
            build_all(Path(DB_PATH), ROOT / "data" / "prices.csv", ROOT / "data" / "shares.csv", p,
                      ROOT / "data" / "excluded_classes.csv")
        except ValueError as e:
            raise HTTPException(422, str(e))
    finally:
        _rebuild_lock.release()
    return meta()


# Serve the web page: / returns static/index.html, which then calls the endpoints above.
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(ROOT / "static" / "index.html")
