import datetime as dt
import io
import zipfile

import pandas as pd

from tradedash import congress, signals

PTR_TEXT = """Clerk of the House of Representatives • Legislative Resource Center
PERIODIC TRANSACTION REPORT
Name: Hon. Jane Example
ID Owner Asset Transaction Date Notification Amount Cap.
Type Date Gains >
$200?
SP Apple Inc. - Common Stock (AAPL) [ST] P 09/14/2026 09/15/2026 $15,001 -
$50,000
F S : New
JT NVIDIA Corporation - Common Stock (NVDA) [ST] S (partial) 09/02/2026 09/03/2026 $1,001 - $15,000
Microsoft Corp (MSFT) [OP] P 09/03/2026 09/04/2026 $1,001 - $15,000
Berkshire Hathaway Inc. Class B (BRK.B) [ST] P 9/5/2026 9/6/2026 Over $50,000,000
"""

INDEX = ("Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\n"
         "Hon.\tExample\tJane\t\tP\tCA01\t2026\t9/20/2026\t20031234\n"
         "Hon.\tPaper\tPat\t\tP\tTX02\t2026\t9/21/2026\t9115555\n"
         "Hon.\tAnnual\tAl\t\tA\tNY03\t2026\t5/15/2026\t10071234\n")


def test_parse_ptr_text():
    rows = congress.parse_ptr_text(PTR_TEXT, "20031234", "Jane Example", "2026-09-20")
    by = {r["ticker"]: r for r in rows}
    assert set(by) == {"AAPL", "NVDA", "MSFT", "BRK.B"}
    assert by["AAPL"]["tx_type"] == "P" and by["AAPL"]["amount_low"] == 15_001 and by["AAPL"]["amount_high"] == 50_000
    assert by["NVDA"]["tx_type"] == "S"
    assert by["MSFT"]["asset_type"] == "OP"
    assert by["BRK.B"]["amount_low"] == 50_000_000 and by["BRK.B"]["trade_date"] == "2026-09-05"


def test_parse_index_keeps_ptrs():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2026FD.txt", INDEX)
    rows = congress.parse_index(buf.getvalue())
    assert [r["doc_id"] for r in rows] == ["20031234", "9115555"]
    assert rows[0]["member"] == "Jane Example" and rows[0]["filed"] == dt.date(2026, 9, 20)
    assert congress.is_efiled("20031234") and not congress.is_efiled("9115555")


def test_summarize_only_stock_purchases():
    rows = congress.parse_ptr_text(PTR_TEXT, "20031234", "Jane Example", "2026-09-20")
    other = {**rows[0], "member": "Sam Other", "doc_id": "20039999"}
    s = congress.summarize(pd.DataFrame(rows + [other]), dt.date(2026, 10, 1))
    aapl = s[s["ticker"] == "AAPL"].iloc[0]
    assert aapl["members"] == 2 and aapl["total_low"] == 30_002
    assert "NVDA" not in s["ticker"].tolist() and "MSFT" not in s["ticker"].tolist()


def test_congress_idea(uptrend):
    rows = congress.parse_ptr_text(PTR_TEXT, "20031234", "Jane Example", "2026-09-20")
    s = congress.summarize(pd.DataFrame(rows), dt.date(2026, 10, 1))
    ideas = signals.build_ideas({"AAPL": uptrend}, pd.DataFrame(), s)
    i = ideas.iloc[0]
    assert i["setup"] == "congress_buy" and i["grade"] == "A"
    assert any("Jane Example" in r for r in i["reasons"])


def test_pdf_text_extraction(tmp_path):
    import pytest
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure()
    fig.text(0.05, 0.5, r"Apple Inc. (AAPL) [ST] P 09/14/2026 09/15/2026 \$1,001 – \$15,000", fontsize=8)
    path = tmp_path / "ptr.pdf"
    fig.savefig(path)
    rows = congress.parse_ptr_text(congress._pdf_text(path.read_bytes()))
    assert rows and rows[0]["ticker"] == "AAPL"
