import numpy as np
import pandas as pd
import pytest


def make_prices(closes, volume=2_000_000):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    vol = np.full(len(closes), volume, dtype=float)
    return pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99,
                         "close": closes, "volume": vol}, index=idx)


@pytest.fixture
def uptrend():
    rng = np.random.default_rng(0)
    return make_prices(100 * np.cumprod(1 + 0.002 + rng.normal(0, 0.005, 300)))


@pytest.fixture
def breakout(uptrend):
    df = uptrend.copy()
    df.iloc[-1, df.columns.get_loc("close")] = df["high"].iloc[-21:-1].max() * 1.03
    df.iloc[-1, df.columns.get_loc("high")] = df["close"].iloc[-1] * 1.005
    df.iloc[-1, df.columns.get_loc("volume")] = 6_000_000
    return df
