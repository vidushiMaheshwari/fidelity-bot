from __future__ import annotations

import numpy as np
import pandas as pd


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy().sort_values("Date").reset_index(drop=True)
    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"]

    frame["ret_1d"] = close.pct_change()
    frame["ret_5d"] = close.pct_change(5)
    frame["ret_10d"] = close.pct_change(10)
    frame["ret_20d"] = close.pct_change(20)

    frame["sma_20"] = close.rolling(20, min_periods=20).mean()
    frame["sma_50"] = close.rolling(50, min_periods=20).mean()
    frame["sma_200"] = close.rolling(200, min_periods=50).mean()
    frame["ema_20"] = close.ewm(span=20, adjust=False).mean()
    frame["ema_50"] = close.ewm(span=50, adjust=False).mean()

    frame["rsi_14"] = _relative_strength_index(close, period=14)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    frame["macd"] = ema12 - ema26
    frame["macd_signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]

    frame["prev_close"] = close.shift(1)
    tr1 = high - low
    tr2 = (high - frame["prev_close"]).abs()
    tr3 = (low - frame["prev_close"]).abs()
    frame["true_range"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    frame["atr_14"] = frame["true_range"].rolling(14, min_periods=14).mean()
    frame["atr_pct_14"] = frame["atr_14"] / close

    frame["bb_mid_20"] = close.rolling(20, min_periods=20).mean()
    frame["bb_std_20"] = close.rolling(20, min_periods=20).std()
    frame["bb_upper_20"] = frame["bb_mid_20"] + (2 * frame["bb_std_20"])
    frame["bb_lower_20"] = frame["bb_mid_20"] - (2 * frame["bb_std_20"])
    frame["bb_width_20"] = (frame["bb_upper_20"] - frame["bb_lower_20"]) / frame["bb_mid_20"]

    frame["volatility_20"] = frame["ret_1d"].rolling(20, min_periods=20).std() * np.sqrt(252)
    frame["volume_avg_20"] = volume.rolling(20, min_periods=20).mean()
    frame["volume_ratio"] = volume / frame["volume_avg_20"]

    return frame


def _relative_strength_index(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
    return rsi
