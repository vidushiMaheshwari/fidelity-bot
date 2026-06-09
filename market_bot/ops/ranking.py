from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from market_bot.models import AnalysisDecision


@dataclass(frozen=True)
class RankedDecision:
    symbol: str
    criticality: str
    score: float
    rank: int


BUY_RECOMMENDATIONS = {
    "buy_now": 1.0,
    "buy_on_pullback": 0.75,
    "watch_for_entry_signal": 0.42,
    "watch": 0.20,
    "do_not_buy": 0.10,
}

HOLD_RECOMMENDATIONS = {
    "trim_or_exit": 1.0,
    "reduce_or_pause": 0.80,
    "monitor_closely": 0.58,
    "hold_with_lower_risk": 0.30,
    "hold_watch": 0.15,
}

def criticality_score(decision: AnalysisDecision) -> float:
    recommendation_weight = _recommendation_weight(decision)

    if decision.analysis_intent == "buy":
        trend_signal = max(0.0, decision.composite_score)
        direction_signal = max(0.0, 1.0 - decision.down_probability)
        trend_bias = max(0.0, decision.trend_score)
        momentum_bias = max(0.0, decision.momentum_score)
    else:
        trend_signal = max(0.0, -decision.trend_score)
        direction_signal = decision.down_probability
        trend_bias = max(0.0, -decision.trend_score)
        momentum_bias = max(0.0, -decision.momentum_score)

    composite_signal = 0.60 * trend_signal + 0.20 * direction_signal + 0.10 * trend_bias + 0.10 * momentum_bias
    score = (composite_signal * 0.65 + recommendation_weight * 0.35) * decision.confidence
    return round(float(max(0.0, min(1.0, score))), 4)


def criticality_label(
    decision: AnalysisDecision,
    score: float,
    min_confidence: float = 0.6,
) -> str:
    recommendation_weight = _recommendation_weight(decision)
    is_strong_sell = decision.analysis_intent == "hold" and decision.recommendation in {"trim_or_exit", "reduce_or_pause"}
    is_strong_buy = decision.analysis_intent == "buy" and decision.recommendation == "buy_now"

    if score >= 0.85 and decision.confidence >= min_confidence and recommendation_weight >= 0.75 and is_strong_sell:
        return "critical_sell"
    if score >= 0.85 and decision.confidence >= min_confidence and recommendation_weight >= 0.75 and is_strong_buy:
        return "critical_buy"
    if score >= 0.70 and decision.confidence >= 0.7:
        return "important"
    return "watch"


def rank_decisions(
    decisions: Iterable[AnalysisDecision],
    criticality_threshold: float = 0.0,
    min_critical_confidence: float = 0.6,
) -> List[Tuple[RankedDecision, AnalysisDecision]]:
    ranked = []
    for decision in decisions:
        score = criticality_score(decision)
        category = criticality_label(
            decision,
            score=score,
            min_confidence=min_critical_confidence,
        )
        if score < criticality_threshold:
            continue
        ranked.append((RankedDecision(decision.symbol, category, score, 0), decision))

    ranked.sort(
        key=lambda item: (
            _rank_priority(item[0].criticality),
            item[0].score,
            item[1].trend_score + item[1].momentum_score,
        ),
        reverse=True,
    )

    final = []
    for index, (info, decision) in enumerate(ranked, start=1):
        final.append((RankedDecision(info.symbol, info.criticality, info.score, index), decision))
    return final


def _recommendation_weight(decision: AnalysisDecision) -> float:
    recommendation_map = BUY_RECOMMENDATIONS if decision.analysis_intent == "buy" else HOLD_RECOMMENDATIONS
    return recommendation_map.get(decision.recommendation, 0.0)


def _rank_priority(label: str) -> int:
    return {"critical_sell": 4, "critical_buy": 3, "important": 2, "watch": 1}.get(label, 0)
