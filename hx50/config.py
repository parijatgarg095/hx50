"""One place for every knob: default dates, retry counts, file paths, and the index mandate
defaults. If you want to change a default, change it here instead of hunting through the code.

Two groups:
  * fetch settings  - used by fetch_data.py when downloading from Yahoo (needs internet).
  * mandate defaults - the index rules; engine.Params reads its defaults from here.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

# --- file locations (everything lives under data/) ---
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "hx50.db"
PRICES_CSV = DATA_DIR / "prices.csv"
SHARES_CSV = DATA_DIR / "shares.csv"
EXCLUDED_CSV = DATA_DIR / "excluded_classes.csv"
UNIVERSE_CSV = DATA_DIR / "universe.csv"

# --- data fetch (fetch_data.py) ---
HISTORY_DAYS = 215                      # how far back to download by default (~7 months)
SHARE_FETCH_RETRIES = 3                 # Yahoo share-count retries per ticker before giving up
SHARE_FETCH_THREADS = 8                 # how many share-count requests run at once
UNIVERSE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = "Mozilla/5.0 (hx50-assignment)"


def default_start() -> str:
    """Earliest date to download by default (today minus HISTORY_DAYS)."""
    return (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()


def default_end() -> str:
    """Latest date to download by default (today)."""
    return date.today().isoformat()


# --- index mandate defaults (engine.Params reads these) ---
N = 50                  # number of names held (R1: ranks 11..60)
EXCLUDE_TOP = 10        # skip the ten largest (R1)
BUFFER = 10             # stabilisation band width (R5)
TOP_BUFFER = 0          # optional buffer at the rank 10/11 edge (0 = strict)
BASE_LEVEL = 1000.0     # index starts here (R3)
COST_BPS = 10.0         # assumed one-way trading cost, used only for the cost estimate
REENTRY_WINDOW = 4      # "dropped then re-added within N rebalances" churn metric
