from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from market_bot.models import AnalysisDecision
from market_bot.analysis.stocks.indicators import add_derived_features


def analyze_symbol(
    symbol: str,
    frame: pd.DataFrame,
    benchmark: Optional[pd.Series] = None,
    analysis_intent: str = "hold",
    confidence_threshold: float = 0.0,
    risk_profile: str = "moderate",
) -> AnalysisDecision:
    if not (0.0 <= confidence_threshold <= 1.0):
        raise ValueError(f"{symbol}: confidence_threshold must be between 0 and 1. Got {confidence_threshold}.")
    frame = add_derived_features(frame)
    frame = frame.dropna(subset=["Close"]).copy()
    if len(frame) < 30:
        raise ValueError(f"{symbol}: not enough data ({len(frame)} rows). Need at least 30 data points.")

    end = frame.iloc[-1]
    week_lookback = max(1, min(6, len(frame) - 1))
    start = frame.iloc[-week_lookback]

    pct_week = _safe_ratio(end["Close"], start["Close"]) - 1.0
    pct_1d = _safe_ratio(end["Close"], frame.iloc[-2]["Close"]) - 1.0 if len(frame) >= 2 else 0.0
    pct_5d = end["ret_5d"]
    close = frame["Close"]
    latest_date = pd.Timestamp(end["Date"]).date()
    week_start_date = pd.Timestamp(start["Date"]).date()
    vol_20 = end["volatility_20"] if pd.notna(end["volatility_20"]) else 0.0
    atr_pct = end["atr_pct_14"] if pd.notna(end["atr_pct_14"]) else 0.0

    trend_score = _trend_score(
        close.iloc[-1],
        end["sma_20"],
        end["sma_50"],
        end["sma_200"],
    )

    momentum_score = _momentum_score(
        float(end["rsi_14"]),
        float(end["macd"]),
        float(end["macd_signal"]),
        float(end["macd_hist"]),
        float(pct_1d or 0.0),
        float(pct_5d or 0.0),
    )

    relative_score = _relative_strength_score(frame, benchmark)

    volume_score = _volume_score(end["Close"], frame.iloc[-2]["Close"] if len(frame) >= 2 else np.nan, end["volume_ratio"], frame["Volume"])
    volatility_score = _volatility_score(atr_pct, vol_20, end["bb_width_20"])

    composite = (
        0.35 * trend_score
        + 0.30 * momentum_score
        + 0.15 * relative_score
        + 0.10 * volume_score
        + 0.10 * volatility_score
    )
    composite = float(np.clip(composite, -1.0, 1.0))

    raw_down_probability = (1.0 - composite) / 2.0
    down_probability = float(np.clip(raw_down_probability, 0.01, 0.99))

    analysis_intent = analysis_intent.strip().lower()
    if analysis_intent not in {"hold", "buy"}:
        raise ValueError(
            f"{symbol}: unsupported analysis intent '{analysis_intent}'."
            " Use 'hold' or 'buy'."
        )
    risk_profile = risk_profile.strip().lower()
    if risk_profile not in {"conservative", "moderate", "aggressive"}:
        raise ValueError(
            f"{symbol}: unsupported risk profile '{risk_profile}'."
            " Use 'conservative', 'moderate', or 'aggressive'."
        )

    confidence = confidence_score(
        trend_score,
        momentum_score,
        relative_score,
        volume_score,
        volatility_score,
        available_rows=len(frame),
    )

    recommendation = _recommendation(
        down_probability,
        relative_score,
        composite,
        trend_score,
        momentum_score,
        analysis_intent,
        confidence=confidence,
        confidence_threshold=confidence_threshold,
        risk_profile=risk_profile,
    )
    rationale = _build_rationale(
        trend_score=trend_score,
        relative_score=relative_score,
        rsi=end["rsi_14"],
        pct_week=pct_week,
        macd_hist=end["macd_hist"],
        volume_ratio=end["volume_ratio"],
        atr_pct=atr_pct,
        week_performance=close.iloc[-1] - close.iloc[-2] if len(frame) >= 2 else 0.0,
    )

    metrics = {
        "pct_change_week": float(pct_week),
        "pct_change_1d": float(pct_1d or 0.0),
        "pct_change_5d": float(pct_5d or 0.0),
        "trend_score": float(trend_score),
        "momentum_score": float(momentum_score),
        "relative_strength_score": float(relative_score),
        "volume_score": float(volume_score),
        "volatility_score": float(volatility_score),
        "volatility_20": float(vol_20 or 0.0),
        "atr_pct_14": float(atr_pct or 0.0),
        "rsi_14": float(end["rsi_14"]) if pd.notna(end["rsi_14"]) else 0.0,
        "bb_width_20": float(end["bb_width_20"]) if pd.notna(end["bb_width_20"]) else 0.0,
        "volume_ratio": float(end["volume_ratio"]) if pd.notna(end["volume_ratio"]) else 0.0,
        "macd_hist": float(end["macd_hist"]) if pd.notna(end["macd_hist"]) else 0.0,
        "confidence": float(confidence),
    }

    return AnalysisDecision(
        symbol=symbol.upper(),
        analysis_intent=analysis_intent,
        confidence=confidence,
        week_start=week_start_date,
        week_end=latest_date,
        close_start=float(start["Close"]),
        close_end=float(end["Close"]),
        pct_change_week=float(pct_week),
        trend_score=float(trend_score),
        momentum_score=float(momentum_score),
        relative_strength_score=float(relative_score),
        volume_score=float(volume_score),
        volatility_score=float(volatility_score),
        composite_score=composite,
        down_probability=down_probability,
        recommendation=recommendation,
        rationale=rationale,
        metrics=metrics,
    )


def _trend_score(close: float, sma20: float, sma50: float, sma200: float) -> float:
    if pd.isna(sma20) or pd.isna(sma50) or pd.isna(sma200):
        return 0.0
    if close > sma20 > sma50 > sma200:
        return 1.0
    if close < sma20 < sma50 < sma200:
        return -1.0
    if close > sma20:
        return 0.35
    if close < sma20:
        return -0.35
    return 0.0


def _momentum_score(rsi: float, macd: float, macd_signal: float, macd_hist: float, ret1: float, ret5: float) -> float:
    score = 0.0
    if not pd.isna(rsi):
        if rsi >= 70:
            score += 0.45
        elif rsi >= 60:
            score += 0.25
        elif rsi <= 30:
            score -= 0.55
        elif rsi <= 40:
            score -= 0.25
    if not pd.isna(macd) and not pd.isna(macd_signal):
        if macd > macd_signal and macd_hist > 0:
            score += 0.30
        elif macd < macd_signal and macd_hist < 0:
            score -= 0.30
    if not pd.isna(ret1) and not pd.isna(ret5):
        if ret1 > 0 and ret5 > 0:
            score += 0.20
        elif ret1 < 0 and ret5 < 0:
            score -= 0.20
        elif ret1 < 0 < ret5:
            score -= 0.10
    return float(np.clip(score, -1.0, 1.0))


def _relative_strength_score(frame: pd.DataFrame, benchmark: Optional[pd.Series]) -> float:
    if benchmark is None or benchmark.empty:
        return 0.0
    aligned = frame.set_index(pd.to_datetime(frame["Date"])).join(
        benchmark.rename("benchmark").to_frame(),
        how="inner",
    )
    if aligned.shape[0] < 20:
        return 0.0

    aligned["asset_ret_5d"] = aligned["Close"].pct_change(5)
    aligned["bench_ret_5d"] = aligned["benchmark"].pct_change(5)
    asset = aligned["asset_ret_5d"].iloc[-1]
    bench = aligned["bench_ret_5d"].iloc[-1]
    if pd.isna(asset) or pd.isna(bench):
        return 0.0

    diff = asset - bench
    if diff >= 0.03:
        return 0.5
    if diff >= 0.01:
        return 0.3
    if diff <= -0.03:
        return -0.5
    if diff <= -0.01:
        return -0.3
    return 0.0


def _volume_score(close: float, prev_close: float, volume_ratio: float, volume_series: pd.Series) -> float:
    if pd.isna(volume_ratio) or pd.isna(prev_close) or pd.isna(close):
        return 0.0
    if close < prev_close and volume_ratio >= 1.7 and volume_series.notna().sum() >= 20:
        return -0.45
    if close > prev_close and volume_ratio >= 1.7:
        return 0.15
    return 0.0


def _volatility_score(atr_pct: float, vol20: float, bb_width: float) -> float:
    score = 0.0
    if pd.notna(atr_pct) and atr_pct > 0.05:
        score -= 0.35
    elif pd.notna(atr_pct) and atr_pct > 0.025:
        score -= 0.15
    if pd.notna(vol20):
        if vol20 > 0.55:
            score -= 0.15
        elif vol20 < 0.22:
            score += 0.10
    if pd.notna(bb_width) and bb_width > 0.10:
        score -= 0.10
    return float(np.clip(score, -1.0, 1.0))


def _recommendation(
    down_prob: float,
    relative_score: float,
    composite: float,
    trend_score: float,
    momentum_score: float,
    analysis_intent: str,
    confidence: float,
    confidence_threshold: float,
    risk_profile: str,
) -> str:
    if confidence < confidence_threshold:
        if analysis_intent == "buy":
            return "watch"
        return "hold_watch"

    if analysis_intent == "hold":
        return _recommendation_for_hold(down_prob, relative_score, composite, risk_profile)
    return _recommendation_for_buy(down_prob, relative_score, composite, trend_score, momentum_score, risk_profile)


def _risk_profile_adjustment(profile: str) -> dict:
    if profile == "conservative":
        return {
            "trim_or_exit": 0.72,
            "monitor": 0.55,
            "reduce_composite": -0.15,
            "reduce_relative": -0.15,
            "hold_better_composite": 0.18,
            "hold_better_down_prob": 0.32,
            "buy_now_composite": 0.52,
            "buy_now_down_prob": 0.30,
            "buy_pullback_composite": 0.28,
            "buy_pullback_down_prob": 0.40,
            "do_not_buy_down_prob": 0.68,
            "do_not_buy_trend": -0.18,
            "do_not_buy_momentum": -0.15,
            "wait_entry_down_prob": 0.48,
            "wait_entry_composite": -0.02,
            "watch_relative_threshold": -0.25,
            "watch_down_prob": 0.36,
        }
    if profile == "aggressive":
        return {
            "trim_or_exit": 0.80,
            "monitor": 0.65,
            "reduce_composite": -0.25,
            "reduce_relative": -0.25,
            "hold_better_composite": 0.10,
            "hold_better_down_prob": 0.40,
            "buy_now_composite": 0.38,
            "buy_now_down_prob": 0.42,
            "buy_pullback_composite": 0.16,
            "buy_pullback_down_prob": 0.50,
            "do_not_buy_down_prob": 0.82,
            "do_not_buy_trend": -0.30,
            "do_not_buy_momentum": -0.24,
            "wait_entry_down_prob": 0.60,
            "wait_entry_composite": -0.12,
            "watch_relative_threshold": -0.35,
            "watch_down_prob": 0.44,
        }

    return {
        "trim_or_exit": 0.75,
        "monitor": 0.60,
        "reduce_composite": -0.2,
        "reduce_relative": -0.2,
        "hold_better_composite": 0.15,
        "hold_better_down_prob": 0.35,
        "buy_now_composite": 0.45,
        "buy_now_down_prob": 0.35,
        "buy_pullback_composite": 0.20,
        "buy_pullback_down_prob": 0.45,
        "do_not_buy_down_prob": 0.75,
        "do_not_buy_trend": -0.25,
        "do_not_buy_momentum": -0.2,
        "wait_entry_down_prob": 0.55,
        "wait_entry_composite": -0.05,
        "watch_relative_threshold": -0.3,
        "watch_down_prob": 0.4,
    }


def _recommendation_for_hold(
    down_prob: float,
    relative_score: float,
    composite: float,
    risk_profile: str,
) -> str:
    thresholds = _risk_profile_adjustment(risk_profile)
    if down_prob >= thresholds["trim_or_exit"]:
        return "trim_or_exit"
    if down_prob >= thresholds["monitor"]:
        return "monitor_closely"
    if relative_score < thresholds["reduce_relative"] and composite < thresholds["reduce_composite"]:
        return "reduce_or_pause"
    if down_prob <= thresholds["hold_better_down_prob"] and composite >= thresholds["hold_better_composite"]:
        return "hold_with_lower_risk"
    return "hold_watch"


def _recommendation_for_buy(
    down_prob: float,
    relative_score: float,
    composite: float,
    trend_score: float,
    momentum_score: float,
    risk_profile: str,
) -> str:
    thresholds = _risk_profile_adjustment(risk_profile)
    if down_prob >= thresholds["do_not_buy_down_prob"]:
        return "do_not_buy"
    if trend_score < thresholds["do_not_buy_trend"] and momentum_score < thresholds["do_not_buy_momentum"]:
        return "do_not_buy"
    if down_prob >= thresholds["wait_entry_down_prob"] and composite < thresholds["wait_entry_composite"]:
        return "watch_for_entry_signal"
    if composite >= thresholds["buy_now_composite"] and down_prob <= thresholds["buy_now_down_prob"]:
        return "buy_now"
    if relative_score < thresholds["watch_relative_threshold"] and down_prob > thresholds["watch_down_prob"]:
        return "watch_for_entry_signal"
    if down_prob <= thresholds["buy_pullback_down_prob"] and composite >= thresholds["buy_pullback_composite"]:
        return "buy_on_pullback"
    return "watch"


def confidence_score(
    trend_score: float,
    momentum_score: float,
    relative_score: float,
    volume_score: float,
    volatility_score: float,
    available_rows: int,
) -> float:
    score = (
        abs(trend_score)
        + abs(momentum_score)
        + abs(relative_score)
        + abs(volume_score)
        + abs(volatility_score)
    ) / 5.0
    if available_rows < 30:
        score *= max(0.5, available_rows / 30.0)
    return float(np.clip(score, 0.0, 1.0))


def _safe_ratio(numerator: float, denominator: float) -> float:
    if pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
        return 0.0
    return numerator / denominator


def _build_rationale(
    trend_score: float,
    relative_score: float,
    rsi: float,
    pct_week: float,
    macd_hist: float,
    volume_ratio: float,
    atr_pct: float,
    week_performance: float,
) -> str:
    trend_text = "bullish trend structure" if trend_score > 0.2 else ("bearish trend structure" if trend_score < -0.2 else "mixed trend structure")
    momentum_parts = []
    if pd.notna(rsi):
        if rsi >= 70:
            momentum_parts.append("RSI elevated")
        elif rsi <= 40:
            momentum_parts.append("RSI weak momentum")
        elif rsi >= 60:
            momentum_parts.append("RSI supportive")
        elif rsi <= 50:
            momentum_parts.append("RSI cautious")
    if not pd.isna(macd_hist):
        if macd_hist > 0:
            momentum_parts.append("MACD histogram above zero")
        else:
            momentum_parts.append("MACD histogram below zero")
    if not momentum_parts:
        momentum_parts.append("momentum mixed")

    relative_text = "relative strength vs benchmark is positive" if relative_score > 0 else ("negative relative strength" if relative_score < 0 else "neutral versus benchmark")
    vol_parts = []
    if pd.notna(volume_ratio) and volume_ratio >= 1.7:
        vol_parts.append(f"high volume on latest bar ({volume_ratio:.2f}x average)")
    if pd.notna(atr_pct):
        if atr_pct > 0.05:
            vol_parts.append(f"high ATR volatility ({atr_pct:.2%})")
        elif atr_pct < 0.02:
            vol_parts.append(f"low ATR volatility ({atr_pct:.2%})")
    if not vol_parts:
        vol_parts.append("average current volatility profile")

    return (
        f"Weekly move: {pct_week:.2%} from {week_performance:.2f} point change; "
        f"{trend_text}; {', '.join(momentum_parts)}; {relative_text}; "
        f"volatility: {', '.join(vol_parts)}."
    )
