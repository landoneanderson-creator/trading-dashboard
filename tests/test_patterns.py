import datetime as dt

import pandas as pd

from tradedash import edgar, patterns
from test_edgar import FORM4


def _buys(owner, dates, ticker="ACME"):
    return pd.DataFrame({"ticker": ticker, "owner": owner, "trade_date": dates})


def test_tag_routine_opportunistic_and_new():
    history = pd.concat([
        _buys("Every March", ["2023-03-10", "2024-03-12", "2025-03-11"]),
        _buys("Yearly Odd Months", ["2023-05-01", "2024-11-20", "2025-08-08"]),
        _buys("Two Years Only", ["2024-03-01", "2025-03-01"]),
    ])
    trades = pd.concat([
        _buys("Every March", ["2026-03-09"]),
        _buys("Yearly Odd Months", ["2026-03-09"]),
        _buys("Two Years Only", ["2026-03-09"]),
        _buys("Brand New", ["2026-03-09"]),
    ])
    tagged = patterns.tag(trades, history)
    assert tagged["pattern"].tolist() == ["routine", "opportunistic", "new", "new"]


def test_tag_uses_no_future_data():
    trades = _buys("Every March", ["2023-03-10", "2024-03-12", "2025-03-11", "2026-03-09"])
    assert patterns.tag(trades)["pattern"].tolist() == ["new", "new", "new", "routine"]


def test_tag_empty():
    assert "pattern" in patterns.tag(pd.DataFrame(columns=edgar.COLUMNS)).columns


def test_summarize_drops_routine_and_flags_opportunistic():
    base = edgar.parse_form4(FORM4, "acc-1", "2026-10-16")[0]
    routine = {**base, "owner": "Routine Rita", "accession": "acc-2", "pattern": "routine"}
    opp = {**base, "owner": "Odd Otto", "accession": "acc-3", "pattern": "opportunistic"}
    s = edgar.summarize(pd.DataFrame([{**base, "pattern": "new"}, routine, opp]), dt.date(2026, 10, 20))
    row = s.iloc[0]
    assert row["buyers"] == 2 and row["opportunistic"]
    assert "Routine Rita" not in row["owners"]


class FakeSession:
    def __init__(self, content):
        self.content, self.calls = content, 0

    def get(self, url, timeout=None):
        self.calls += 1
        resp = type("R", (), {})()
        resp.status_code, resp.content = 200, self.content
        return resp


def test_update_history_is_incremental(tmp_path, monkeypatch):
    from test_simulate import _dataset
    monkeypatch.setattr(patterns, "HISTORY_FILE", tmp_path / "h.csv")
    monkeypatch.setattr(patterns, "QUARTERS_FILE", tmp_path / "q.txt")
    monkeypatch.setattr(patterns.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(patterns, "HISTORY_QUARTERS", 2)
    s1 = FakeSession(_dataset())
    hist = patterns.update_history(dt.date(2026, 4, 1), s1)
    assert s1.calls == 2
    assert hist["owner"].tolist() == ["Doe Jane"]  # 10% owner fund dropped
    s2 = FakeSession(_dataset())
    patterns.update_history(dt.date(2026, 4, 1), s2)
    assert s2.calls == 0
