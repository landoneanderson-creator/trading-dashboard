"""Collect open-market insider purchases (Form 4, code "P") from SEC EDGAR.

Uses the free EDGAR daily form index to find Form 4 filings, then parses each filing's
XML. Results are cached in data/insider_trades.csv so each daily run only fetches new days.
"""
from __future__ import annotations

import datetime as dt
import re
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from . import config

ARCHIVES = "https://www.sec.gov/Archives/"
TRADES_FILE = config.DATA_DIR / "insider_trades.csv"
PROCESSED_FILE = config.DATA_DIR / "insider_processed_days.txt"
COLUMNS = [
    "filed", "accession", "ticker", "issuer", "owner", "title",
    "is_director", "is_officer", "is_ten_pct", "trade_date", "shares", "price", "value",
]


class SecClient:
    def __init__(self, user_agent: str = config.SEC_USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._min_interval = 1.0 / config.SEC_MAX_REQUESTS_PER_SEC
        self._last = 0.0

    def get(self, url: str) -> requests.Response | None:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self._last = time.monotonic()
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                return None
            time.sleep(2 ** (attempt + 1))
        resp.raise_for_status()
        return None


def daily_index_url(day: dt.date) -> str:
    qtr = (day.month - 1) // 3 + 1
    return f"{ARCHIVES}edgar/daily-index/{day.year}/QTR{qtr}/form.{day:%Y%m%d}.idx"


def parse_form_index(text: str) -> list[str]:
    """Return unique filing paths for Form 4 rows in a daily form.idx file."""
    paths, seen_header = [], False
    for line in text.splitlines():
        if line.startswith("---"):
            seen_header = True
            continue
        if not seen_header or not line.startswith("4 "):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 5 and parts[0] == "4":
            paths.append(parts[-1])
    return list(dict.fromkeys(paths))


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _flag(value: str) -> bool:
    return value.lower() in ("1", "true")


def parse_form4(submission: str, accession: str = "", filed: str = "") -> list[dict]:
    """Extract open-market purchases from a Form 4 submission (.txt with embedded XML)."""
    match = re.search(r"<XML>(.*?)</XML>", submission, re.S | re.I)
    xml = (match.group(1) if match else submission).strip()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    if root.tag != "ownershipDocument":
        return []

    ticker = _text(root, "issuer/issuerTradingSymbol").upper()
    issuer = _text(root, "issuer/issuerName")
    owners = root.findall("reportingOwner")
    owner = "; ".join(_text(o, "reportingOwnerId/rptOwnerName") for o in owners)
    rel = owners[0].find("reportingOwnerRelationship") if owners else None
    title = _text(rel, "officerTitle")

    rows = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _text(tx, "transactionCoding/transactionCode") != "P":
            continue
        if _text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value") not in ("A", ""):
            continue
        try:
            shares = float(_text(tx, "transactionAmounts/transactionShares/value") or 0)
            price = float(_text(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        rows.append({
            "filed": filed, "accession": accession, "ticker": ticker, "issuer": issuer,
            "owner": owner, "title": title,
            "is_director": _flag(_text(rel, "isDirector")),
            "is_officer": _flag(_text(rel, "isOfficer")),
            "is_ten_pct": _flag(_text(rel, "isTenPercentOwner")),
            "trade_date": _text(tx, "transactionDate/value"),
            "shares": shares, "price": price, "value": round(shares * price, 2),
        })
    return rows


def load_trades() -> pd.DataFrame:
    if TRADES_FILE.exists():
        return pd.read_csv(TRADES_FILE, dtype={"accession": str, "ticker": str})
    return pd.DataFrame(columns=COLUMNS)


def _processed_days() -> set[str]:
    if PROCESSED_FILE.exists():
        return set(PROCESSED_FILE.read_text().split())
    return set()


def update_cache(today: dt.date | None = None, client: SecClient | None = None) -> pd.DataFrame:
    """Fetch any unprocessed business days in the backfill window and append to the cache."""
    today = today or dt.date.today()
    client = client or SecClient()
    done = _processed_days()
    trades = load_trades()
    new_rows = []
    for offset in range(config.INSIDER_BACKFILL_DAYS, -1, -1):
        day = today - dt.timedelta(days=offset)
        key = day.isoformat()
        if day.weekday() >= 5 or key in done:
            continue
        resp = client.get(daily_index_url(day))
        if resp is None:  # holiday, or index not published yet
            if offset > 1:
                done.add(key)
            continue
        paths = parse_form_index(resp.text)
        print(f"[edgar] {key}: {len(paths)} Form 4 filings")
        for path in paths:
            accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
            filing = client.get(ARCHIVES + path)
            if filing is not None:
                new_rows.extend(parse_form4(filing.text, accession, key))
        done.add(key)

    if new_rows:
        trades = pd.concat([trades, pd.DataFrame(new_rows, columns=COLUMNS)], ignore_index=True)
        trades = trades.drop_duplicates(subset=["accession", "trade_date", "shares", "price"])
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(TRADES_FILE, index=False)
    PROCESSED_FILE.write_text("\n".join(sorted(done)) + "\n")
    return trades


def summarize(trades: pd.DataFrame, as_of: dt.date, lookback_days: int = config.INSIDER_LOOKBACK_DAYS) -> pd.DataFrame:
    """Per-ticker insider buying summary over the lookback window."""
    if trades.empty:
        return pd.DataFrame(columns=["ticker", "buyers", "total_value", "officer_buy", "last_buy", "owners"])
    df = trades.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    start = pd.Timestamp(as_of - dt.timedelta(days=lookback_days))
    df = df[(df["trade_date"] >= start) & (df["value"] >= config.INSIDER_MIN_TRADE_VALUE)]
    df = df[df["ticker"].fillna("").str.fullmatch(r"[A-Z][A-Z.\-]{0,6}")]
    if df.empty:
        return pd.DataFrame(columns=["ticker", "buyers", "total_value", "officer_buy", "last_buy", "owners"])
    exec_title = df["title"].fillna("").str.contains(r"CEO|Chief Executive|CFO|Chief Financial|President", case=False)
    df["officer_buy"] = df["is_officer"].astype(bool) & exec_title
    grouped = df.groupby("ticker").agg(
        buyers=("owner", "nunique"),
        total_value=("value", "sum"),
        officer_buy=("officer_buy", "any"),
        last_buy=("trade_date", "max"),
        owners=("owner", lambda s: ", ".join(sorted(set(s))[:4])),
    ).reset_index()
    return grouped.sort_values("total_value", ascending=False)
