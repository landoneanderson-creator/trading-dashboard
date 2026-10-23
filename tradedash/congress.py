"""Collect stock purchases by members of the U.S. House from the Clerk's public disclosures.

The Clerk publishes a yearly index of filings (a zip with a tab-separated file). Periodic
Transaction Reports (filing type "P") are PDFs; e-filed ones (8-digit DocIDs starting with "2")
have a text layer we can parse. Scanned paper filings (7-digit DocIDs) are skipped.

The Senate's disclosure site blocks automated requests from cloud servers, so it isn't covered.
Members can report trades up to 45 days after making them, so this is a slow-moving signal.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import time
import zipfile

import pandas as pd
import requests

from . import config

BASE = "https://disclosures-clerk.house.gov/public_disc"
TRADES_FILE = config.DATA_DIR / "congress_trades.csv"
PROCESSED_FILE = config.DATA_DIR / "congress_processed_docs.txt"
COLUMNS = ["filed", "doc_id", "member", "state_district", "ticker", "asset_type", "tx_type",
           "trade_date", "amount_low", "amount_high"]
HEADERS = {"User-Agent": "Mozilla/5.0 (trading-dashboard; personal research)"}

# "(AAPL) [ST] P 01/14/2026 01/15/2026 $1,001 - $15,000" once whitespace is collapsed.
TX_RE = re.compile(
    r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*\[([A-Z]{2})\]\s*"
    r"(P|S\s*\(partial\)|S|E)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?:(Over)\s+)?\$([\d,]+)(?:\s*[-–—−]\s*\$([\d,]+))?"
)


def is_efiled(doc_id: str) -> bool:
    return len(doc_id) == 8 and doc_id.startswith("2")


def parse_index(raw_zip: bytes) -> list[dict]:
    """Return PTR rows (member, filed date, DocID) from a yearly FD zip."""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
        text = zf.read(name).decode("utf-8-sig", errors="replace")
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter="\t"):
        if (r.get("FilingType") or "").strip() != "P":
            continue
        try:
            filed = dt.datetime.strptime(r["FilingDate"].strip(), "%m/%d/%Y").date()
        except (KeyError, ValueError):
            continue
        member = " ".join(p for p in (r.get("First", "").strip(), r.get("Last", "").strip()) if p)
        rows.append({"doc_id": r["DocID"].strip(), "filed": filed, "member": member,
                     "state_district": (r.get("StateDst") or "").strip(), "year": r["Year"].strip()})
    return rows


def _money(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


def parse_ptr_text(text: str, doc_id: str = "", member: str = "", filed: str = "",
                   state_district: str = "") -> list[dict]:
    text = re.sub(r"\s+", " ", text.replace("\x00", ""))
    rows = []
    for m in TX_RE.finditer(text):
        ticker, asset_type, tx, trade_date, _notified, over, low, high = m.groups()
        tx = "S" if tx.startswith("S") else tx
        low_v = _money(low)
        rows.append({
            "filed": filed, "doc_id": doc_id, "member": member, "state_district": state_district,
            "ticker": ticker, "asset_type": asset_type, "tx_type": tx,
            "trade_date": dt.datetime.strptime(trade_date, "%m/%d/%Y").date().isoformat(),
            "amount_low": low_v, "amount_high": low_v if over else (_money(high) or low_v),
        })
    return rows


def _pdf_text(raw: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def load_trades() -> pd.DataFrame:
    if TRADES_FILE.exists():
        return pd.read_csv(TRADES_FILE, dtype={"doc_id": str, "ticker": str})
    return pd.DataFrame(columns=COLUMNS)


def update_cache(today: dt.date | None = None, session: requests.Session | None = None,
                 window_days: int = 60) -> pd.DataFrame:
    """Download and parse e-filed PTRs filed in the last `window_days` that aren't cached yet."""
    today = today or dt.date.today()
    s = session or requests.Session()
    done = set(PROCESSED_FILE.read_text().split()) if PROCESSED_FILE.exists() else set()
    years = {today.year, (today - dt.timedelta(days=window_days)).year}

    filings = []
    for year in sorted(years):
        resp = s.get(f"{BASE}/financial-pdfs/{year}FD.zip", headers=HEADERS, timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        filings.extend(parse_index(resp.content))
    start = today - dt.timedelta(days=window_days)
    todo = [f for f in filings if f["filed"] >= start and f["doc_id"] not in done]
    skipped_scans = sum(not is_efiled(f["doc_id"]) for f in todo)
    print(f"[congress] {len(todo)} new trade reports ({skipped_scans} scanned, skipped)")

    new_rows, parsed_docs = [], 0
    for f in todo:
        if not is_efiled(f["doc_id"]):
            done.add(f["doc_id"])
            continue
        url = f"{BASE}/ptr-pdfs/{f['year']}/{f['doc_id']}.pdf"
        try:
            resp = s.get(url, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            rows = parse_ptr_text(_pdf_text(resp.content), f["doc_id"], f["member"],
                                  f["filed"].isoformat(), f["state_district"])
        except Exception as exc:  # one bad PDF shouldn't stop the run
            print(f"[congress] skipping {f['doc_id']}: {exc}")
            continue
        parsed_docs += bool(rows)
        new_rows.extend(rows)
        done.add(f["doc_id"])
        time.sleep(0.5)
    print(f"[congress] parsed {len(new_rows)} transactions from {parsed_docs} reports")

    trades = load_trades()
    if new_rows:
        trades = pd.concat([trades, pd.DataFrame(new_rows, columns=COLUMNS)], ignore_index=True)
        trades = trades.drop_duplicates()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(TRADES_FILE, index=False)
    PROCESSED_FILE.write_text("\n".join(sorted(done)) + "\n")
    return trades


def summarize(trades: pd.DataFrame, as_of: dt.date,
              lookback_days: int = config.CONGRESS_LOOKBACK_DAYS) -> pd.DataFrame:
    """Per-ticker summary of stock purchases disclosed in the lookback window."""
    cols = ["ticker", "members", "total_low", "last_filed", "names"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    df = trades[(trades["tx_type"] == "P") & (trades["asset_type"] == "ST")
                & trades["ticker"].fillna("").str.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?")
                & (trades["ticker"] != "NONE")].copy()
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df[df["filed"] >= pd.Timestamp(as_of - dt.timedelta(days=lookback_days))]
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = df.groupby("ticker").agg(
        members=("member", "nunique"),
        total_low=("amount_low", "sum"),
        last_filed=("filed", "max"),
        names=("member", lambda s: ", ".join(sorted(set(s))[:4])),
    ).reset_index()
    return out.sort_values(["members", "total_low"], ascending=False)
