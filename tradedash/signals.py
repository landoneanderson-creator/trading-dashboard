"""Combine insider buying and chart signals into ranked trade ideas with a trade plan."""
from __future__ import annotations

import json
import math

import pandas as pd

from . import config, indicators


def hold_periods() -> dict[str, int]:
    periods = {name: s["hold_days"] for name, s in config.SETUPS.items()}
    if config.HOLD_PERIODS_FILE.exists():
        calibrated = json.loads(config.HOLD_PERIODS_FILE.read_text())
        periods.update({k: int(v["best_horizon"]) for k, v in calibrated.items() if k in periods})
    return periods


def insider_score(ins: pd.Series | None) -> tuple[int, list[str]]:
    if ins is None:
        return 0, []
    score, reasons = 0, []
    buyers, value = int(ins["buyers"]), float(ins["total_value"])
    if buyers >= 2:
        score += min(40, 30 + 5 * (buyers - 2))
        reasons.append(f"{buyers} insiders bought ${value:,.0f} in the last {config.INSIDER_LOOKBACK_DAYS} days")
    else:
        score += 15 if value >= 100_000 else 8
        reasons.append(f"Insider bought ${value:,.0f} ({ins['owners']})")
    if bool(ins["officer_buy"]):
        score += 10
        reasons.append("CEO/CFO/President among the buyers")
    score += min(10, max(0, round(5 * math.log10(value / config.INSIDER_MIN_TRADE_VALUE))))
    return min(score, 50), reasons


def congress_score(c: pd.Series | None) -> tuple[int, list[str]]:
    if c is None:
        return 0, []
    members, low = int(c["members"]), float(c["total_low"])
    filed = pd.Timestamp(c["last_filed"]).date().isoformat()
    if members >= 2:
        score = min(30, 25 + 5 * (members - 2))
        reason = f"{members} members of Congress bought (at least ${low:,.0f}): {c['names']}"
    else:
        score = 12 if low >= 50_000 else 8
        reason = f"Rep. {c['names']} bought at least ${low:,.0f} (disclosed {filed})"
    return score, [reason]


def technical_score(row: pd.Series) -> tuple[int, list[str]]:
    score, reasons = 0, []
    if row["uptrend"]:
        score += 15
        reasons.append("Uptrend: price above rising 50/200-day averages")
    if row["breakout"]:
        score += 20
        reasons.append(f"Breakout to a {config.BREAKOUT_LOOKBACK}-day high on {row['vol_ratio']:.1f}x volume")
    if row["pullback"]:
        score += 20
        reasons.append(f"Pullback in uptrend, RSI {row['rsi']:.0f}")
    if row["close"] > row["sma20"]:
        score += 5
    if row["rsi"] > 75:
        score -= 10
        reasons.append(f"Overbought, RSI {row['rsi']:.0f}: consider waiting for a dip")
    return max(0, min(score, 50)), reasons


def _by_ticker(summary: pd.DataFrame | None) -> dict:
    if summary is None or summary.empty:
        return {}
    return {r["ticker"]: r for _, r in summary.iterrows()}


def evaluate(ticker: str, as_of: pd.Timestamp, row: pd.Series, ins: pd.Series | None,
             con: pd.Series | None, holds: dict[str, int]) -> dict | None:
    """Score one stock on one day's indicators. Returns the trade idea, or None if no setup."""
    if pd.isna(row["atr"]) or row["close"] < config.MIN_PRICE:
        return None
    catalyst = ins is not None or con is not None
    min_liquidity = config.MIN_AVG_DOLLAR_VOLUME / 5 if catalyst else config.MIN_AVG_DOLLAR_VOLUME
    if pd.isna(row["dollar_vol"]) or row["dollar_vol"] < min_liquidity:
        return None

    i_score, i_reasons = insider_score(ins)
    c_score, c_reasons = congress_score(con)
    t_score, t_reasons = technical_score(row)
    if ins is not None:
        setup = "insider_cluster" if int(ins["buyers"]) >= 2 else "insider_buy"
    elif con is not None:
        setup = "congress_buy"
    elif row["breakout"]:
        setup = "breakout"
    elif row["pullback"]:
        setup = "pullback"
    else:
        return None

    plan = config.SETUPS[setup]
    # Grade A = a data catalyst confirmed by the chart; B = one or the other.
    confirmed = catalyst and (row["uptrend"] or row["close"] > row["sma20"])
    grade = "A" if confirmed and t_score >= 15 else "B"
    hold = holds[setup]
    return {
        "ticker": ticker,
        "grade": grade,
        "score": i_score + c_score + t_score,
        "setup": setup,
        "as_of": as_of.date().isoformat(),
        "entry": round(row["close"], 2),
        "stop": round(row["close"] - plan["stop_atr"] * row["atr"], 2),
        "target": round(row["close"] + plan["target_atr"] * row["atr"], 2),
        "hold_days": hold,
        "exit_by": (as_of + pd.offsets.BDay(hold)).date().isoformat(),
        "rsi": round(row["rsi"], 1),
        "reasons": i_reasons + c_reasons + t_reasons,
    }


def rank(ideas: list[dict]) -> list[dict]:
    """Dashboard order: grade A first, then highest score."""
    return sorted(ideas, key=lambda i: (i["grade"], -i["score"]))[: config.MAX_IDEAS]


def build_ideas(prices: dict[str, pd.DataFrame], insider_summary: pd.DataFrame,
                congress_summary: pd.DataFrame | None = None) -> pd.DataFrame:
    holds = hold_periods()
    ins_by_ticker = _by_ticker(insider_summary)
    con_by_ticker = _by_ticker(congress_summary)
    ideas = []
    for ticker, df in prices.items():
        ind = indicators.add_all(df)
        idea = evaluate(ticker, ind.index[-1], ind.iloc[-1], ins_by_ticker.get(ticker),
                        con_by_ticker.get(ticker), holds)
        if idea:
            ideas.append(idea)
    return pd.DataFrame(rank(ideas)) if ideas else pd.DataFrame()
