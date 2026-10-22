import datetime as dt
import json

import numpy as np
import pandas as pd

from tradedash import backtest, dashboard, signals
from conftest import make_prices


def insider_summary(ticker, buyers=3, value=600_000, officer=True):
    return pd.DataFrame([{"ticker": ticker, "buyers": buyers, "total_value": value,
                          "officer_buy": officer, "last_buy": pd.Timestamp("2026-10-15"),
                          "owners": "A, B, C"}])


def test_breakout_idea_has_consistent_plan(breakout):
    ideas = signals.build_ideas({"XYZ": breakout}, pd.DataFrame())
    assert len(ideas) == 1
    i = ideas.iloc[0]
    assert i["setup"] == "breakout" and i["grade"] == "B"
    assert i["stop"] < i["entry"] < i["target"]
    assert i["hold_days"] > 0


def test_insider_cluster_in_uptrend_is_grade_a(uptrend):
    ideas = signals.build_ideas({"ACME": uptrend}, insider_summary("ACME"))
    i = ideas.iloc[0]
    assert i["setup"] == "insider_cluster" and i["grade"] == "A"
    assert any("3 insiders" in r for r in i["reasons"])


def test_no_setup_no_idea():
    flat = make_prices(np.full(260, 50.0))
    assert signals.build_ideas({"FLAT": flat}, pd.DataFrame()).empty


def test_illiquid_skipped(breakout):
    thin = breakout.copy()
    thin["volume"] = 1000
    assert signals.build_ideas({"THIN": thin}, pd.DataFrame()).empty


def test_calibrated_hold_periods_override(tmp_path, monkeypatch, breakout):
    f = tmp_path / "hold.json"
    f.write_text(json.dumps({"breakout": {"best_horizon": 20}}))
    monkeypatch.setattr(signals.config, "HOLD_PERIODS_FILE", f)
    assert signals.build_ideas({"XYZ": breakout}, pd.DataFrame()).iloc[0]["hold_days"] == 20


def test_backtest_report_shape(uptrend, tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    prices = {f"T{i}": make_prices(100 * np.cumprod(1 + rng.normal(0.0005, 0.02, 400))) for i in range(5)}
    report = backtest.run(prices)
    assert set(report) == set(signals.config.SETUPS)
    for r in report.values():
        for row in r["by_horizon"]:
            assert 0 <= row["win_rate"] <= 1
    monkeypatch.setattr(backtest.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backtest.config, "HOLD_PERIODS_FILE", tmp_path / "hold.json")
    backtest.save_hold_periods(report)
    assert (tmp_path / "backtest_report.json").exists()


def test_render_escapes_and_includes_disclaimer(breakout):
    ideas = signals.build_ideas({"XYZ": breakout}, pd.DataFrame())
    news = {"XYZ": [{"title": "<script>x</script>", "link": "https://example.com/a", "date": ""}]}
    page = dashboard.render(ideas, news, None, dt.datetime(2026, 10, 22))
    assert "Not financial advice" in page
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert "tradingview.com/chart/?symbol=XYZ" in page
