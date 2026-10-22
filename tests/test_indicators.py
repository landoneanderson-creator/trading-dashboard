import numpy as np
import pandas as pd

from tradedash import indicators
from conftest import make_prices


def test_rsi_bounds_and_extremes():
    up = indicators.rsi(pd.Series(np.arange(1, 60, dtype=float)))
    down = indicators.rsi(pd.Series(np.arange(60, 1, -1, dtype=float)))
    assert up.iloc[-1] == 100
    assert down.iloc[-1] < 1


def test_atr_matches_constant_range():
    df = make_prices(np.full(50, 100.0))
    assert abs(indicators.atr(df).iloc[-1] - 2.0) < 1e-9  # high-low = 101 - 99


def test_uptrend_and_breakout_flags(uptrend, breakout):
    assert bool(indicators.add_all(uptrend)["uptrend"].iloc[-1])
    last = indicators.add_all(breakout).iloc[-1]
    assert bool(last["breakout"]) and last["vol_ratio"] > 1.5


def test_pullback_flag():
    closes = list(np.linspace(50, 120, 260)) + list(np.linspace(120, 108, 8))
    last = indicators.add_all(make_prices(closes)).iloc[-1]
    assert bool(last["pullback"])
    assert last["rsi"] < 40
