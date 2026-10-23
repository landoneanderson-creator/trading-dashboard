"""Measure forward returns after each signal to calibrate hold periods.

For every setup, compares the average forward return after a signal with the average
forward return of any random day for the same stocks (the baseline). The best horizon is
the one with the largest edge over baseline per sqrt(days held), so longer holds have to
earn their extra time and risk.
"""
from __future__ import annotations

import json
import math

import pandas as pd

from . import config, indicators

MIN_SAMPLES = 30
DEDUPE_DAYS = 5  # ignore repeat signals on the same stock within this many bars


def _events_from_column(ind: pd.DataFrame, column: str, allowed: pd.Series | None = None) -> list[int]:
    """Bars where the column fires, skipping repeats and days outside `allowed` (the market regime)."""
    fired = ind[column].astype(bool)
    if allowed is not None:
        fired &= allowed.reindex(ind.index).fillna(True).astype(bool)
    idx, last = [], -10_000
    for i in range(len(ind)):
        if fired.iloc[i] and i - last > DEDUPE_DAYS:
            idx.append(i)
            last = i
    return idx


def _insider_events(ind: pd.DataFrame, ticker: str, trades: pd.DataFrame | None) -> dict[str, list[int]]:
    out = {"insider_cluster": [], "insider_buy": []}
    if trades is None or trades.empty:
        return out
    t = trades[(trades["ticker"] == ticker) & (trades["value"] >= config.INSIDER_MIN_TRADE_VALUE)]
    if "pattern" in t:
        t = t[t["pattern"] != "routine"]
    if t.empty:
        return out
    filed = pd.to_datetime(t["filed"])
    for day, grp in t.groupby(filed):
        # Count distinct buyers in the lookback window ending on this filing day.
        window = t[(filed > day - pd.Timedelta(days=config.INSIDER_LOOKBACK_DAYS)) & (filed <= day)]
        pos = ind.index.searchsorted(day)  # first bar on or after the filing became public
        if pos >= len(ind):
            continue
        key = "insider_cluster" if window["owner"].nunique() >= 2 else "insider_buy"
        out[key].append(int(pos))
    return out


def _congress_events(ind: pd.DataFrame, ticker: str, ctrades: pd.DataFrame | None) -> dict[str, list[int]]:
    if ctrades is None or ctrades.empty:
        return {"congress_buy": []}
    t = ctrades[(ctrades["ticker"] == ticker) & (ctrades["tx_type"] == "P") & (ctrades["asset_type"] == "ST")]
    idx = []
    for day in sorted(pd.to_datetime(t["filed"]).unique()):
        pos = int(ind.index.searchsorted(day))  # first bar once the purchase was public
        if pos < len(ind) and (not idx or pos - idx[-1] > DEDUPE_DAYS):
            idx.append(pos)
    return {"congress_buy": idx}


def run(prices: dict[str, pd.DataFrame], trades: pd.DataFrame | None = None,
        horizons: list[int] = config.BACKTEST_HORIZONS, congress_trades: pd.DataFrame | None = None) -> dict:
    samples = {s: {h: [] for h in horizons} for s in config.SETUPS}
    baseline = {h: [] for h in horizons}
    # Chart setups only count on days the dashboard would have shown them (market above its 200-day).
    market = prices.get(config.REGIME_TICKER)
    allowed = indicators.risk_on(market) if market is not None else None
    for ticker, df in prices.items():
        ind = indicators.add_all(df)
        close = ind["close"].to_numpy()
        n = len(close)
        for h in horizons:
            if n > h:
                fwd = close[h:] / close[:-h] - 1
                baseline[h].extend(fwd[200:].tolist())  # skip warm-up bars
        events = {"breakout": _events_from_column(ind, "breakout", allowed),
                  "pullback": _events_from_column(ind, "pullback", allowed)}
        events.update(_insider_events(ind, ticker, trades))
        events.update(_congress_events(ind, ticker, congress_trades))
        for setup, idxs in events.items():
            for i in idxs:
                for h in horizons:
                    if i + h < n:
                        samples[setup][h].append(close[i + h] / close[i] - 1)

    base_mean = {h: (sum(v) / len(v) if v else 0.0) for h, v in baseline.items()}
    report = {}
    for setup, by_h in samples.items():
        rows, best, best_val = [], None, -math.inf
        for h in horizons:
            r = by_h[h]
            if not r:
                continue
            mean = sum(r) / len(r)
            edge = mean - base_mean[h]
            rows.append({"horizon": h, "n": len(r), "avg_return": round(mean, 4),
                         "win_rate": round(sum(x > 0 for x in r) / len(r), 3),
                         "edge_vs_baseline": round(edge, 4)})
            if len(r) >= MIN_SAMPLES and edge / math.sqrt(h) > best_val:
                best, best_val = h, edge / math.sqrt(h)
        report[setup] = {"by_horizon": rows, "best_horizon": best}
    return report


def save_hold_periods(report: dict) -> dict:
    """Persist calibrated hold periods for setups with enough samples and a positive edge."""
    calibrated = {}
    for setup, r in report.items():
        best = r["best_horizon"]
        row = next((x for x in r["by_horizon"] if x["horizon"] == best), None)
        if best and row and row["edge_vs_baseline"] > 0:
            calibrated[setup] = {"best_horizon": best, **row}
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.HOLD_PERIODS_FILE.write_text(json.dumps(calibrated, indent=2))
    (config.DATA_DIR / "backtest_report.json").write_text(json.dumps(report, indent=2))
    return calibrated
