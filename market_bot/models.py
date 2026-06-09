from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import Dict, Any


@dataclass(frozen=True)
class AnalysisDecision:
    symbol: str
    analysis_intent: str
    confidence: float
    week_start: date
    week_end: date
    close_start: float
    close_end: float
    pct_change_week: float
    trend_score: float
    momentum_score: float
    relative_strength_score: float
    volume_score: float
    volatility_score: float
    composite_score: float
    down_probability: float
    recommendation: str
    rationale: str
    metrics: Dict[str, float]

    def as_payload(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["week_start"] = self.week_start.isoformat()
        payload["week_end"] = self.week_end.isoformat()
        return payload
