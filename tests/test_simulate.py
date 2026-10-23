import datetime as dt
import io
import zipfile

import numpy as np
import pandas as pd

from tradedash import dashboard, simulate
from conftest import make_prices


def test_past_quarters_skips_current():
    assert simulate.past_quarters(dt.date(2026, 10, 23), 3) == [(2026, 1), (2026, 2), (2026, 3)]
    assert simulate.past_quarters(dt.date(2026, 2, 1), 2) == [(2025, 3), (2025, 4)]


def _dataset() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.tsv",
                    "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERNAME\tISSUERTRADINGSYMBOL\n"
                    "a1\t15-JAN-2026\t4\tAcme Corp\tacme\n"
                    "a2\t16-JAN-2026\t4\tAcme Corp\tACME\n")
        zf.writestr("REPORTINGOWNER.tsv",
                    "ACCESSION_NUMBER\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
                    "a1\tDoe Jane\tDirector,Officer\tCEO\n"
                    "a2\tBig Fund\tTenPercentOwner\t\n")
        zf.writestr("NONDERIV_TRANS.tsv",
                    "ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tTRANS_ACQUIRED_DISP_CD\n"
                    "a1\t13-JAN-2026\tP\t10000\t12.5\tA\n"
                    "a1\t13-JAN-2026\tS\t100\t13\tD\n"
                    "a2\t14-JAN-2026\tP\t5000\t12\tA\n")
    return buf.getvalue()


def test_parse_sec_dataset():
    df = simulate.parse_sec_dataset(_dataset())
    assert len(df) == 2
    jane = df[df["owner"] == "Doe Jane"].iloc[0]
    assert jane["ticker"] == "ACME" and jane["filed"] == "2026-01-15" and jane["trade_date"] == "2026-01-13"
    assert jane["is_director"] and jane["is_officer"] and jane["value"] == 125_000
    assert not df[df["owner"] == "Big Fund"].iloc[0]["is_officer"]


def test_simulation_runs_and_reports():
    rng = np.random.default_rng(3)
    prices = {"SPY": make_prices(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 400)))}
    for i in range(6):
        closes = 50 * np.cumprod(1 + rng.normal(0.001, 0.02, 400))
        vol = np.where(rng.random(400) < 0.1, 6_000_000, 2_000_000)
        df = make_prices(closes)
        df["volume"] = vol
        prices["T" + "ABCDEF"[i]] = df
    start = prices["SPY"].index[250]
    insider = pd.DataFrame([{
        "filed": prices["TA"].index[300].date().isoformat(), "accession": "x", "ticker": "TA",
        "issuer": "T0 Inc", "owner": "Doe", "title": "CEO", "is_director": True, "is_officer": True,
        "is_ten_pct": False, "trade_date": prices["TA"].index[299].date().isoformat(),
        "shares": 10000, "price": 50, "value": 500_000}])
    r = simulate.run(prices, insider, pd.DataFrame(), start)
    assert r["overall"]["trades"] > 0
    assert -1 < r["total_return"] < 10 and r["max_drawdown"] <= 0
    assert "insider_buy" in r["by_setup"]
    page = dashboard.render(pd.DataFrame(), {}, None, dt.datetime(2026, 10, 23), simulation=[r])
    assert "What following the dashboard would have returned" in page
