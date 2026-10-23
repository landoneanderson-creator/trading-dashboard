import datetime as dt

import pandas as pd

from tradedash import edgar

FORM4 = """<SEC-DOCUMENT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik><issuerName>Acme Corp</issuerName>
    <issuerTradingSymbol>acme</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-10-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>12.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-10-15</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>13</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</SEC-DOCUMENT>"""

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
3           SOME CO                                                       1111111     20261015    edgar/data/1111111/0001111111-26-000001.txt
4           ACME CORP                                                     320193      20261015    edgar/data/320193/0000320193-26-000010.txt
4           DOE JANE                                                      2222222     20261015    edgar/data/320193/0000320193-26-000010.txt
4/A         ACME CORP                                                     320193      20261015    edgar/data/320193/0000320193-26-000011.txt
"""


def test_parse_form_index_dedupes_and_filters():
    assert edgar.parse_form_index(INDEX) == ["edgar/data/320193/0000320193-26-000010.txt"]


def test_parse_form4_keeps_only_purchases():
    rows = edgar.parse_form4(FORM4, "acc-1", "2026-10-16")
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "ACME" and r["value"] == 125_000 and r["is_officer"]
    assert r["title"] == "Chief Executive Officer"


def test_parse_form4_handles_garbage():
    assert edgar.parse_form4("<XML>not xml</XML>") == []


def test_daily_index_url():
    assert edgar.daily_index_url(dt.date(2026, 10, 15)).endswith("daily-index/2026/QTR4/form.20261015.idx")


def test_summarize_cluster():
    base = edgar.parse_form4(FORM4, "acc-1", "2026-10-16")[0]
    other = {**base, "owner": "Smith Bob", "title": "", "is_officer": False, "is_director": True, "accession": "acc-2"}
    old = {**base, "owner": "Old Buyer", "trade_date": "2026-01-01", "accession": "acc-3"}
    tiny = {**base, "owner": "Tiny", "value": 1000, "accession": "acc-4"}
    s = edgar.summarize(pd.DataFrame([base, other, old, tiny]), dt.date(2026, 10, 20))
    row = s.iloc[0]
    assert row["ticker"] == "ACME" and row["buyers"] == 2 and row["officer_buy"]
    assert row["total_value"] == 250_000


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeClient:
    def __init__(self):
        self.urls = []

    def get(self, url, missing=(404,)):
        self.urls.append(url)
        if url.endswith(".idx"):
            return FakeResp(INDEX) if "20261015" in url else None
        return FakeResp(FORM4)


def test_update_cache_is_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "TRADES_FILE", tmp_path / "t.csv")
    monkeypatch.setattr(edgar, "PROCESSED_FILE", tmp_path / "p.txt")
    monkeypatch.setattr(edgar.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(edgar.config, "INSIDER_BACKFILL_DAYS", 3)
    client = FakeClient()
    trades = edgar.update_cache(dt.date(2026, 10, 16), client)
    assert len(trades) == 1 and trades.iloc[0]["ticker"] == "ACME"
    filings_fetched = sum(not u.endswith(".idx") for u in client.urls)
    client2 = FakeClient()
    edgar.update_cache(dt.date(2026, 10, 16), client2)
    assert sum(not u.endswith(".idx") for u in client2.urls) == 0  # 10/15 already processed
    assert filings_fetched == 1


class BlockedClient:
    def get(self, url, missing=(404,)):
        return None  # every index "missing", as when SEC answers 403 to everything


def test_update_cache_raises_when_every_index_is_missing(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(edgar, "TRADES_FILE", tmp_path / "t.csv")
    monkeypatch.setattr(edgar, "PROCESSED_FILE", tmp_path / "p.txt")
    monkeypatch.setattr(edgar.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(edgar.config, "INSIDER_BACKFILL_DAYS", 10)
    with pytest.raises(RuntimeError, match="blocked"):
        edgar.update_cache(dt.date(2026, 10, 16), BlockedClient())


def test_summarize_ignores_ten_pct_holders_and_funds():
    base = edgar.parse_form4(FORM4, "acc-1", "2026-10-16")[0]
    holder = {**base, "ticker": "BIG", "owner": "Big Insurer", "is_officer": False,
              "is_director": False, "is_ten_pct": True}
    fund = {**base, "ticker": "CEF", "issuer": "Example Strategic Fund Inc", "is_director": True}
    s = edgar.summarize(pd.DataFrame([base, holder, fund]), dt.date(2026, 10, 20))
    assert s["ticker"].tolist() == ["ACME"]
