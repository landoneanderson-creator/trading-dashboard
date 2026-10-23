"""Simulate trading the dashboard's rules day by day over past data.

Each trading day the dashboard's ideas are rebuilt from data that was public on that day. The
top-ranked ideas are bought at the next day's open (up to MAX_POSITIONS, equal-sized), then
sold at the stop, the target, or the close on the last day of the hold, whichever comes first.
Historical insider purchases come from the SEC's free quarterly Form 3/4/5 data sets.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pandas as pd
import requests

from . import config, congress, edgar, indicators, signals

MAX_POSITIONS = 10
COST = 0.001  # round-trip commissions and slippage, as a fraction of the trade
DATASETS = "https://www.sec.gov/files/structureddata/data/form-345-data-sets/{y}q{q}_form345.zip"
RESULTS_FILE = config.DATA_DIR / "simulation.json"


def past_quarters(today: dt.date, n: int) -> list[tuple[int, int]]:
    """The n most recent fully published quarters (the current one isn't out yet)."""
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append((y, q))
    return sorted(out)


def _read_tsv(zf: zipfile.ZipFile, name: str, usecols: list[str]) -> pd.DataFrame:
    member = next(m for m in zf.namelist() if m.upper().endswith(name))
    with zf.open(member) as f:
        df = pd.read_csv(f, sep="\t", dtype=str, usecols=lambda c: c.upper() in usecols,
                         on_bad_lines="skip", low_memory=False)
    df.columns = [c.upper() for c in df.columns]
    return df


def parse_sec_dataset(raw: bytes) -> pd.DataFrame:
    """Open-market purchases from one quarterly Form 3/4/5 data set, in edgar.COLUMNS shape."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        sub = _read_tsv(zf, "SUBMISSION.TSV", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                                                "ISSUERNAME", "ISSUERTRADINGSYMBOL"])
        own = _read_tsv(zf, "REPORTINGOWNER.TSV", ["ACCESSION_NUMBER", "RPTOWNERNAME",
                                                    "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])
        tx = _read_tsv(zf, "NONDERIV_TRANS.TSV", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE",
                                                   "TRANS_SHARES", "TRANS_PRICEPERSHARE",
                                                   "TRANS_ACQUIRED_DISP_CD"])
    tx = tx[(tx["TRANS_CODE"] == "P") & (tx["TRANS_ACQUIRED_DISP_CD"].fillna("A") == "A")]
    sub = sub[sub["DOCUMENT_TYPE"].fillna("4").str.startswith("4")]
    own = own.groupby("ACCESSION_NUMBER").agg(
        owner=("RPTOWNERNAME", lambda s: "; ".join(s.dropna())),
        rel=("RPTOWNER_RELATIONSHIP", "first"),
        title=("RPTOWNER_TITLE", "first"),
    ).reset_index()
    df = tx.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER", how="left")
    rel = df["rel"].fillna("").str.lower()
    shares = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
    price = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
    out = pd.DataFrame({
        "filed": pd.to_datetime(df["FILING_DATE"], format="mixed", errors="coerce").dt.date.astype(str),
        "accession": df["ACCESSION_NUMBER"],
        "ticker": df["ISSUERTRADINGSYMBOL"].fillna("").str.upper().str.strip(),
        "issuer": df["ISSUERNAME"],
        "owner": df["owner"].fillna(""),
        "title": df["title"].fillna(""),
        "is_director": rel.str.contains("director"),
        "is_officer": rel.str.contains("officer"),
        "is_ten_pct": rel.str.contains("ten"),
        "trade_date": pd.to_datetime(df["TRANS_DATE"], format="mixed", errors="coerce").dt.date.astype(str),
        "shares": shares,
        "price": price,
        "value": (shares * price).round(2),
    })
    return out.dropna(subset=["value"])


def load_history(today: dt.date, quarters: int = 8) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update({"User-Agent": config.SEC_USER_AGENT})
    frames = []
    for y, q in past_quarters(today, quarters):
        resp = session.get(DATASETS.format(y=y, q=q), timeout=300)
        if resp.status_code != 200:
            print(f"[simulate] {y}Q{q} insider data set unavailable ({resp.status_code})")
            continue
        part = parse_sec_dataset(resp.content)
        print(f"[simulate] {y}Q{q}: {len(part)} insider purchases")
        frames.append(part)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=edgar.COLUMNS)


def _daily_summaries(trades: pd.DataFrame, days: pd.DatetimeIndex, lookback: int, fn) -> dict:
    """Summary of what was public on each day: filings in the lookback window up to that day."""
    if trades.empty:
        return {}
    t = trades.copy()
    t["_filed"] = pd.to_datetime(t["filed"], errors="coerce")
    out = {}
    for d in days:
        window = t[(t["_filed"] <= d) & (t["_filed"] > d - pd.Timedelta(days=lookback))]
        if not window.empty:
            s = fn(window.drop(columns="_filed"), d.date())
            if not s.empty:
                out[d] = {r["ticker"]: r for _, r in s.iterrows()}
    return out


def run(prices: dict[str, pd.DataFrame], insider_trades: pd.DataFrame, congress_trades: pd.DataFrame,
        start: pd.Timestamp, benchmark: str = "SPY", grade_a_only: bool = False) -> dict:
    holds = signals.hold_periods()
    ind = {t: indicators.add_all(df) for t, df in prices.items()}
    days = ind[benchmark].index
    days = days[days >= start]
    ins_by_day = _daily_summaries(insider_trades, days, config.INSIDER_LOOKBACK_DAYS, edgar.summarize)
    con_by_day = _daily_summaries(congress_trades, days, config.CONGRESS_LOOKBACK_DAYS, congress.summarize)
    chart_days = {t: set(df.index[df["breakout"] | df["pullback"]]) for t, df in ind.items()}

    cash, positions, pending, trades = 1.0, {}, [], []
    equity_curve = []
    for di, d in enumerate(days):
        # 1. Fill yesterday's orders at today's open.
        for idea in pending:
            t = idea["ticker"]
            if t in positions or len(positions) >= MAX_POSITIONS or d not in ind[t].index:
                continue
            equity = cash + sum(p["shares"] * ind[p["ticker"]]["close"].asof(d) for p in positions.values())
            size = min(cash, equity / MAX_POSITIONS)
            open_px = ind[t].at[d, "open"]
            if size <= 0 or not open_px > 0 or open_px <= idea["stop"]:
                continue
            cash -= size
            positions[t] = {**idea, "shares": size * (1 - COST / 2) / open_px, "entry_px": open_px,
                            "entry_date": d, "cost": size, "days": 0}
        pending = []

        # 2. Check stops, targets and time exits on today's bar.
        for t in list(positions):
            p = positions[t]
            if d not in ind[t].index:
                continue
            bar = ind[t].loc[d]
            p["days"] += 1
            exit_px = None
            if bar["low"] <= p["stop"]:
                exit_px = min(bar["open"], p["stop"]) if p["days"] > 1 else p["stop"]
                reason = "stop"
            elif bar["high"] >= p["target"]:
                exit_px = max(bar["open"], p["target"]) if p["days"] > 1 else p["target"]
                reason = "target"
            elif p["days"] >= p["hold_days"]:
                exit_px, reason = bar["close"], "time"
            if exit_px is not None:
                proceeds = p["shares"] * exit_px * (1 - COST / 2)
                cash += proceeds
                trades.append({"ticker": t, "setup": p["setup"], "grade": p["grade"],
                               "entry_date": p["entry_date"].date().isoformat(), "exit_date": d.date().isoformat(),
                               "return": proceeds / p["cost"] - 1, "days": p["days"], "exit": reason})
                del positions[t]

        equity = cash + sum(p["shares"] * ind[t]["close"].asof(d) for t, p in positions.items())
        equity_curve.append((d, equity))

        # 3. Build today's dashboard from data public as of today's close.
        ins_today, con_today = ins_by_day.get(d, {}), con_by_day.get(d, {})
        names = set(ins_today) | set(con_today) | {t for t, s in chart_days.items() if d in s}
        ideas = []
        for t in names:
            if t not in ind or d not in ind[t].index:
                continue
            idea = signals.evaluate(t, d, ind[t].loc[d], ins_today.get(t), con_today.get(t), holds)
            if idea and (idea["grade"] == "A" or not grade_a_only):
                ideas.append(idea)
        pending = [i for i in signals.rank(ideas) if i["ticker"] not in positions]

    return _report(equity_curve, trades, ind[benchmark], days, grade_a_only)


def _report(curve, trades, bench, days, grade_a_only) -> dict:
    eq = pd.Series([e for _, e in curve], index=[d for d, _ in curve])
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    b = bench["close"].loc[days]
    tr = pd.DataFrame(trades)

    def stats(df: pd.DataFrame) -> dict:
        if df.empty:
            return {"trades": 0}
        return {"trades": len(df), "win_rate": round((df["return"] > 0).mean(), 3),
                "avg_return": round(df["return"].mean(), 4), "avg_days": round(df["days"].mean(), 1)}

    return {
        "variant": "grade A only" if grade_a_only else "top ideas",
        "start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(),
        "total_return": round(eq.iloc[-1] - 1, 4),
        "annual_return": round(eq.iloc[-1] ** (1 / years) - 1, 4),
        "max_drawdown": round((eq / eq.cummax() - 1).min(), 4),
        "benchmark_return": round(b.iloc[-1] / b.iloc[0] - 1, 4),
        "benchmark_annual": round((b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1, 4),
        "benchmark_max_drawdown": round((b / b.cummax() - 1).min(), 4),
        "overall": stats(tr),
        "by_setup": {s: stats(g) for s, g in tr.groupby("setup")} if not tr.empty else {},
        "exits": tr["exit"].value_counts().to_dict() if not tr.empty else {},
    }


def save(results: list[dict]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, default=str))
