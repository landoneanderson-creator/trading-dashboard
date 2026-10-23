"""Tunable settings. Hold periods here are starting points; run the backtest to recalibrate."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
UNIVERSE_FILE = ROOT / "universe.txt"

# The SEC requires a descriptive User-Agent with contact info on every request.
# Set SEC_USER_AGENT (e.g. "trading-dashboard you@example.com") as a GitHub Actions secret.
SEC_USER_AGENT = (os.environ.get("SEC_USER_AGENT") or "").strip() or "trading-dashboard (set SEC_USER_AGENT env var)"
SEC_MAX_REQUESTS_PER_SEC = 8  # SEC fair-access limit is 10/s

# Days of Form 4 filings to backfill when the cache is empty.
INSIDER_BACKFILL_DAYS = int(os.environ.get("INSIDER_BACKFILL_DAYS", "10"))
INSIDER_LOOKBACK_DAYS = 30        # window used to score insider buying
INSIDER_MIN_TRADE_VALUE = 25_000  # ignore tiny open-market buys

# House members' stock purchases, by the date they were disclosed (trades can be up to 45 days older).
CONGRESS_LOOKBACK_DAYS = 45
CONGRESS_MIN_AMOUNT = 15_001  # ignore the smallest reporting bracket ($1,001-$15,000)

# Market regime: when the S&P 500 closes below its 200-day average, breakouts and pullbacks
# (chart-only setups) are paused. Insider and congressional buys still show, with a warning.
REGIME_TICKER = "SPY"
REGIME_MA = 200

# Technical settings
ATR_PERIOD = 14
RSI_PERIOD = 14
PULLBACK_RSI_MAX = 40
BREAKOUT_LOOKBACK = 20
BREAKOUT_VOLUME_RATIO = 1.5
MIN_PRICE = 5.0
MIN_AVG_DOLLAR_VOLUME = 5_000_000

# Per-setup trade plan: ATR multiples for stop and target, and suggested hold in trading days.
SETUPS = {
    "insider_cluster": {"stop_atr": 2.5, "target_atr": 5.0, "hold_days": 60},
    "insider_buy": {"stop_atr": 2.5, "target_atr": 4.0, "hold_days": 40},
    "congress_buy": {"stop_atr": 2.5, "target_atr": 4.0, "hold_days": 40},
    "breakout": {"stop_atr": 2.0, "target_atr": 4.0, "hold_days": 15},
    "pullback": {"stop_atr": 1.5, "target_atr": 3.0, "hold_days": 10},
}

# Overwritten by the backtest when data/hold_periods.json exists.
HOLD_PERIODS_FILE = DATA_DIR / "hold_periods.json"
BACKTEST_HORIZONS = [5, 10, 15, 20, 40, 60]

MAX_IDEAS = 25
