"""Label insider purchases as routine or opportunistic.

Cohen, Malloy and Pomorski ("Decoding Inside Information", 2012) found that insiders who trade in
the same calendar month year after year (a director who buys every March, say) carry no signal,
while trades that break an insider's usual pattern predict returns. Here, for each purchase:

- routine: the same insider bought the same stock in the same calendar month in each of the
  previous three years. These buys are dropped from the insider signal.
- opportunistic: the insider bought the stock in each of the previous three years, but not on a
  calendar pattern. These get a score boost.
- new: not enough buying history to tell. Scored as before.

Only open-market purchases are tracked, so the history is small. It comes from the SEC's free
quarterly Form 3/4/5 data sets, cached in data/insider_history.csv and topped up once a quarter.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from . import config, simulate

HISTORY_FILE = config.DATA_DIR / "insider_history.csv"
QUARTERS_FILE = config.DATA_DIR / "insider_history_quarters.txt"
HISTORY_QUARTERS = 13  # three full years before the live Form 4 cache takes over, plus overlap
YEARS = 3
COLUMNS = ["ticker", "owner", "trade_date"]


def load_history() -> pd.DataFrame:
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE, dtype=str)
    return pd.DataFrame(columns=COLUMNS)


def update_history(today: dt.date | None = None, session: requests.Session | None = None) -> pd.DataFrame:
    """Download any of the last HISTORY_QUARTERS quarterly data sets not cached yet."""
    today = today or dt.date.today()
    wanted = simulate.past_quarters(today, HISTORY_QUARTERS)
    done = set(QUARTERS_FILE.read_text().split()) if QUARTERS_FILE.exists() else set()
    missing = [(y, q) for y, q in wanted if f"{y}Q{q}" not in done]
    history = load_history()
    if not missing:
        return history
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": config.SEC_USER_AGENT})
    frames = [history]
    for y, q in missing:
        resp = session.get(simulate.DATASETS.format(y=y, q=q), timeout=300)
        if resp.status_code != 200:
            print(f"[patterns] {y}Q{q} insider data set unavailable ({resp.status_code})")
            continue
        part = simulate.parse_sec_dataset(resp.content)
        part = part[part["is_director"] | part["is_officer"]]
        print(f"[patterns] {y}Q{q}: {len(part)} officer/director purchases")
        frames.append(part[COLUMNS])
        done.add(f"{y}Q{q}")
    history = pd.concat(frames, ignore_index=True).drop_duplicates()
    cutoff = (today - dt.timedelta(days=366 * (YEARS + 1))).isoformat()
    history = history[history["trade_date"].astype(str) >= cutoff]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    history.sort_values(COLUMNS).to_csv(HISTORY_FILE, index=False)
    QUARTERS_FILE.write_text("\n".join(sorted(done)) + "\n")
    return history


def tag(trades: pd.DataFrame, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return a copy of trades with a "pattern" column: routine, opportunistic or new."""
    out = trades.copy()
    if out.empty:
        out["pattern"] = pd.Series(dtype=str)
        return out
    known = pd.concat([history[COLUMNS] if history is not None else None, out[COLUMNS]], ignore_index=True)
    when = pd.to_datetime(known["trade_date"], errors="coerce")
    keys = list(zip(known["ticker"].fillna(""), known["owner"].fillna("")))
    months = {(k, d.year, d.month) for k, d in zip(keys, when) if pd.notna(d)}
    years = {(k, d.year) for k, d in zip(keys, when) if pd.notna(d)}

    labels = []
    for ticker, owner, trade_date in zip(out["ticker"].fillna(""), out["owner"].fillna(""),
                                         pd.to_datetime(out["trade_date"], errors="coerce")):
        k = (ticker, owner)
        prior = range(trade_date.year - YEARS, trade_date.year) if pd.notna(trade_date) else []
        if prior and all((k, y, trade_date.month) in months for y in prior):
            labels.append("routine")
        elif prior and all((k, y) in years for y in prior):
            labels.append("opportunistic")
        else:
            labels.append("new")
    out["pattern"] = labels
    return out
