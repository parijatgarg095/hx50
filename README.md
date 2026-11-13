# HX-50 index

Equal-weighted index of US large caps ranked 11–60 by market cap, rebalanced weekly,
with a buffer-stabilised version (served by default) and the plain unstabilised version for comparison.

Stack: SQLite (SQL model in `sql/model.sql`) · Pandas/Python index engine · FastAPI · plain HTML + Chart.js.

## Run (Windows PowerShell; Python 3.10+)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m hx50.build                       # loads data/*.csv, runs SQL model, builds both indexes
uvicorn hx50.api:app --port 8000           # then open http://127.0.0.1:8000
```

macOS/Linux: `source .venv/bin/activate` instead of the Activate line.

`data/prices.csv`, `data/shares.csv` and `data/universe.csv` are included, so no download is needed.
To refresh them: `python fetch_data.py` (needs internet), then re-run the build.

The page shows the index level (stabilised vs unstabilised), the R5 comparison, the composition for any date
(weights drift between rebalances) and every rebalance with turnover split into membership and reweight parts.

## Change the mandate

```powershell
python -m hx50.build --n 30 --exclude-top 20 --buffer 5     # 30 names, exclude top 20, buffer 5 ranks
python -m hx50.build --top-buffer 2                         # optional buffer at the rank 10/11 edge (default 0 = strict)
```

The UI has the same controls (names / exclude top / buffer + Rebuild). Rebuilds are atomic: an invalid mandate is
rejected and the previous index keeps being served. `GET /meta` shows the run id and data/code hashes.

## API

| Endpoint | Returns |
|---|---|
| `GET /index?start=&end=` | level, daily return, cumulative return since base |
| `GET /composition?date=` | constituents + weights at the close of that date (non-trading date → previous trading day) |
| `GET /changes?start=&end=` | each rebalance: added, dropped, one-way turnover |
| `GET /meta`, `POST /rebuild?n=&exclude_top=&buffer=&top_buffer=` | run manifest / re-run with another mandate |
| `GET /summary` | R5 comparison, stabilised vs unstabilised |

All accept `variant=stabilised` (default) or `variant=raw`. Interactive docs at `/docs`.

## Tests

```powershell
pytest -q
```

## Layout

```
fetch_data.py        download prices/shares (yfinance)      hx50/config.py  defaults in one place
sql/model.sql        cleaning, market cap, ranks, returns, calendar, rebalance dates
hx50/engine.py       index logic (selection, level, drift, turnover, R5 metrics)
hx50/build.py        CSV -> SQLite -> engine -> persisted tables
hx50/queries.py      read queries   hx50/api.py  FastAPI   static/index.html  UI
tests/               validation on a synthetic market
```
