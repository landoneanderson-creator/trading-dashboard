"""Free end-of-day prices: Yahoo Finance chart API, falling back to Stooq CSV."""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (trading-dashboard)"}


def from_yahoo(ticker: str, range_: str = "2y", session: requests.Session | None = None) -> pd.DataFrame:
    s = session or requests
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.replace('.', '-')}"
    resp = s.get(url, params={"range": range_, "interval": "1d"}, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open": quote["open"], "high": quote["high"], "low": quote["low"],
        "close": quote["close"], "volume": quote["volume"],
    }, index=pd.to_datetime(result["timestamp"], unit="s").normalize())
    adj = result["indicators"].get("adjclose")
    if adj:
        # Scale OHLC by the split/dividend adjustment so indicators aren't broken by splits.
        factor = pd.Series(adj[0]["adjclose"], index=df.index) / df["close"]
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * factor
    return df.dropna()


def from_stooq(ticker: str, session: requests.Session | None = None) -> pd.DataFrame:
    s = session or requests
    sym = ticker.lower().replace(".", "-") + ".us"
    resp = s.get("https://stooq.com/q/d/l/", params={"s": sym, "i": "d"}, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if "Close" not in df.columns:
        raise ValueError(f"no stooq data for {ticker}")
    df = df.rename(columns=str.lower).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df[["open", "high", "low", "close", "volume"]].tail(520).dropna()


def get_history(ticker: str, session: requests.Session | None = None) -> pd.DataFrame | None:
    for fetch in (from_yahoo, from_stooq):
        try:
            df = fetch(ticker, session=session)
            if len(df) >= 60:
                return df
        except Exception as exc:  # network errors, bad symbols, schema changes
            print(f"[prices] {fetch.__name__} failed for {ticker}: {exc}")
    return None


def get_many(tickers: list[str], pause: float = 0.25) -> dict[str, pd.DataFrame]:
    session = requests.Session()
    out = {}
    for t in tickers:
        df = get_history(t, session=session)
        if df is not None:
            out[t] = df
        time.sleep(pause)
    return out
