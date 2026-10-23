"""Technical indicators on a daily OHLCV DataFrame (columns: open, high, low, close, volume)."""
import pandas as pd

from . import config


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = config.RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def atr(df: pd.DataFrame, n: int = config.ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["sma20"] = sma(out["close"], 20)
    out["sma50"] = sma(out["close"], 50)
    out["sma200"] = sma(out["close"], 200)
    out["rsi"] = rsi(out["close"])
    out["atr"] = atr(out)
    out["high_n"] = out["high"].rolling(config.BREAKOUT_LOOKBACK).max().shift()
    out["vol_avg50"] = out["volume"].rolling(50, min_periods=20).mean()
    out["vol_ratio"] = out["volume"] / out["vol_avg50"]
    out["dollar_vol"] = (out["close"] * out["volume"]).rolling(20, min_periods=10).mean()
    # Long-term trend; falls back to the 50-day when there isn't 200 days of history.
    long_ma = out["sma200"].fillna(out["sma50"])
    out["uptrend"] = (out["close"] > out["sma50"]) & (out["sma50"] > long_ma)
    # Oversold dip inside an intact longer-term uptrend.
    out["pullback"] = (out["sma50"] > long_ma) & (out["close"] > long_ma) & (out["rsi"] < config.PULLBACK_RSI_MAX)
    out["breakout"] = (
        (out["close"] > out["high_n"])
        & (out["vol_ratio"] >= config.BREAKOUT_VOLUME_RATIO)
        & (out["close"] > out["sma50"])
    )
    return out


def risk_on(market: pd.DataFrame, n: int = config.REGIME_MA) -> pd.Series:
    """True on days the market index closed above its n-day average (or lacks the history to say)."""
    ma = sma(market["close"], n)
    return (market["close"] > ma) | ma.isna()
