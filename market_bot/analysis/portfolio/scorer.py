from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

from market_bot.models import AnalysisDecision


@dataclass(frozen=True)
class PortfolioHolding:
    """Represents a position used for portfolio-level weighting."""

    symbol: str
    shares: float | None = None
    cost_basis: float | None = None
    currency: str | None = None


@dataclass(frozen=True)
class PortfolioTrade:
    """A single portfolio transaction used for realized performance tracking."""

    symbol: str
    action: str
    shares: float
    price: float
    fee: float | None = None
    trade_date: str | None = None
    currency: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class TradeLedgerSummary:
    """Aggregated details from a sequence of trades."""

    realized_pnl: float
    realized_pnl_pct: float
    realized_notional: float
    trade_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioRecommendation:
    """A compact risk-oriented portfolio recommendation."""

    label: str
    score: float
    explanation: str


@dataclass(frozen=True)
class PortfolioAnalysis:
    symbol_count: int
    weighted_down_probability: float
    weighted_confidence: float
    weighted_composite: float
    expected_week_return: float
    concentration_risk: float
    hhi: float
    effective_breadth: float
    max_weight: float
    top3_weight: float
    buy_pressure: float
    sell_pressure: float
    weighted_notional: float
    notional_gross: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    realized_pnl_pct: float
    realized_notional: float
    realized_trade_count: int
    recommendation: PortfolioRecommendation

    def as_payload(self) -> Dict[str, float | int | str]:
        return {
            "symbol_count": self.symbol_count,
            "weighted_down_probability": self.weighted_down_probability,
            "weighted_confidence": self.weighted_confidence,
            "weighted_composite": self.weighted_composite,
            "expected_week_return": self.expected_week_return,
            "concentration_risk": self.concentration_risk,
            "hhi": self.hhi,
            "effective_breadth": self.effective_breadth,
            "max_weight": self.max_weight,
            "top3_weight": self.top3_weight,
            "buy_pressure": self.buy_pressure,
            "sell_pressure": self.sell_pressure,
            "weighted_notional": self.weighted_notional,
            "notional_gross": self.notional_gross,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "realized_pnl": self.realized_pnl,
            "realized_pnl_pct": self.realized_pnl_pct,
            "realized_notional": self.realized_notional,
            "realized_trade_count": self.realized_trade_count,
            "recommendation_label": self.recommendation.label,
            "recommendation_score": self.recommendation.score,
            "recommendation_explanation": self.recommendation.explanation,
        }


def analyze_portfolio(
    decisions: Sequence[AnalysisDecision],
    holdings: Sequence[PortfolioHolding] | None = None,
    ledger_summary: TradeLedgerSummary | None = None,
) -> PortfolioAnalysis:
    """Build portfolio-level risk context from symbol-level analysis decisions."""
    if not decisions:
        raise ValueError("No decisions provided for portfolio analysis.")

    holding_lookup = {holding.symbol.upper(): holding for holding in (holdings or [])}
    decision_lookup = {decision.symbol.upper(): decision for decision in decisions}

    symbol_count = len(decisions)

    weights, notional_total, weighted_notional = _build_weights_and_notional(
        decision_lookup,
        holding_lookup,
    )

    weighted_down_probability = 0.0
    weighted_confidence = 0.0
    weighted_composite = 0.0
    expected_week_return = 0.0

    for symbol, weight in weights.items():
        decision = decision_lookup[symbol]
        weighted_down_probability += weight * decision.down_probability
        weighted_confidence += weight * decision.confidence
        weighted_composite += weight * decision.composite_score
        expected_week_return += weight * decision.pct_change_week

    # Concentration metrics
    sorted_weights = sorted(weights.values(), reverse=True)
    max_weight = sorted_weights[0] if sorted_weights else 0.0
    top3_weight = sum(sorted_weights[:3]) if len(sorted_weights) >= 3 else sum(sorted_weights)
    hhi = sum(weight * weight for weight in sorted_weights)
    effective_breadth = (1.0 / hhi) if hhi > 0 else 0.0

    concentration_risk = _score_concentration_risk(weights)

    # Signal pressure: fraction of weighted portfolio in sell/buy leaning recommendations
    sell_symbols = {
        "trim_or_exit",
        "reduce_or_pause",
        "monitor_closely",
    }
    buy_symbols = {"buy_now", "buy_on_pullback"}

    sell_pressure = sum(
        weights[symbol]
        for symbol, decision in decision_lookup.items()
        if decision.recommendation in sell_symbols
    )
    buy_pressure = sum(
        weights[symbol]
        for symbol, decision in decision_lookup.items()
        if decision.recommendation in buy_symbols
    )

    unrealized_pnl, unrealized_pnl_pct = _pnl_summary(
        decision_lookup,
        holding_lookup,
        notional_total=notional_total,
    )
    realized_pnl = ledger_summary.realized_pnl if ledger_summary else 0.0
    realized_pnl_pct = ledger_summary.realized_pnl_pct if ledger_summary else 0.0
    realized_notional = ledger_summary.realized_notional if ledger_summary else 0.0
    realized_trade_count = ledger_summary.trade_count if ledger_summary else 0

    rec = _portfolio_recommendation(
        weighted_down_probability=weighted_down_probability,
        concentration_risk=concentration_risk,
        weighted_composite=weighted_composite,
        weighted_confidence=weighted_confidence,
        sell_pressure=sell_pressure,
        buy_pressure=buy_pressure,
        effective_breadth=effective_breadth,
    )

    return PortfolioAnalysis(
        symbol_count=symbol_count,
        weighted_down_probability=round(float(weighted_down_probability), 4),
        weighted_confidence=round(float(weighted_confidence), 4),
        weighted_composite=round(float(weighted_composite), 4),
        expected_week_return=round(float(expected_week_return), 6),
        concentration_risk=round(float(concentration_risk), 4),
        hhi=round(float(hhi), 4),
        effective_breadth=round(float(effective_breadth), 4),
        max_weight=round(float(max_weight), 4),
        top3_weight=round(float(top3_weight), 4),
        buy_pressure=round(float(buy_pressure), 4),
        sell_pressure=round(float(sell_pressure), 4),
        weighted_notional=round(float(weighted_notional), 4),
        notional_gross=round(float(notional_total), 4),
        unrealized_pnl=round(float(unrealized_pnl), 4),
        unrealized_pnl_pct=round(float(unrealized_pnl_pct), 6),
        realized_pnl=round(float(realized_pnl), 4),
        realized_pnl_pct=round(float(realized_pnl_pct), 6),
        realized_notional=round(float(realized_notional), 4),
        realized_trade_count=realized_trade_count,
        recommendation=rec,
    )


def _build_weights_and_notional(
    decisions: Dict[str, AnalysisDecision],
    holdings: Dict[str, PortfolioHolding],
) -> tuple[Dict[str, float], float, float]:
    notional_allocation: Dict[str, float] = {}
    weighted_notional = 0.0
    weighted_by_holdings = 0.0
    notional_total = 0.0

    for symbol, decision in decisions.items():
        holding = holdings.get(symbol)
        if holding is not None and holding.shares and holding.shares > 0:
            notional = holding.shares * decision.close_end
            notional_allocation[symbol] = notional
            weighted_by_holdings += notional
            notional_total += notional
            continue

        # If holdings are not provided or not weighted, default equal notional sizing.
        notional_allocation[symbol] = 1.0
        notional_total += 1.0
    weighted_notional = weighted_by_holdings / notional_total if notional_total > 0 else 0.0

    if not notional_allocation:
        return {}, 0.0, 0.0

    total = sum(notional_allocation.values())
    if total <= 0:
        return {symbol: 1.0 / len(notional_allocation) for symbol in notional_allocation}, 0.0, weighted_notional

    normalized = {symbol: value / total for symbol, value in notional_allocation.items()}
    return normalized, notional_total, weighted_notional


def _pnl_summary(
    decisions: Dict[str, AnalysisDecision],
    holdings: Dict[str, PortfolioHolding],
    notional_total: float,
) -> Tuple[float, float]:
    if not holdings:
        return 0.0, 0.0

    total_unrealized = 0.0
    for symbol, holding in holdings.items():
        decision = decisions.get(symbol)
        if decision is None:
            continue
        if holding.shares is None or holding.shares <= 0:
            continue
        if holding.cost_basis is None or holding.cost_basis <= 0:
            continue
        total_unrealized += holding.shares * (decision.close_end - holding.cost_basis)

    unrealized_pct = 0.0
    if notional_total > 0:
        unrealized_pct = total_unrealized / notional_total
    return total_unrealized, unrealized_pct


def aggregate_holdings_from_trades(
    trades: Sequence[PortfolioTrade],
) -> Tuple[list[PortfolioHolding], TradeLedgerSummary]:
    """Build a holdings snapshot and realized-PnL summary from a trade ledger."""
    positions: Dict[str, list[float]] = {}
    warnings: list[str] = []
    realized_pnl = 0.0
    realized_notional = 0.0
    trade_count = 0

    for trade in sorted(trades, key=_trade_sort_key):
        symbol = trade.symbol.strip().upper()
        if not symbol:
            continue

        position = positions.setdefault(symbol, [0.0, 0.0])  # shares, avg_cost
        action = _normalize_action(trade.action)
        shares = trade.shares
        price = trade.price
        fee = trade.fee or 0.0
        if shares is None or shares <= 0:
            warnings.append(f"{symbol}: invalid shares '{trade.shares}', skipped.")
            continue
        if price is None or price <= 0:
            warnings.append(f"{symbol}: invalid price '{trade.price}', skipped.")
            continue

        trade_count += 1
        current_shares, current_cost = position

        if action == "BUY":
            total_cost = current_shares * (current_cost if current_cost else 0.0) + shares * price + fee
            current_shares += shares
            if current_shares > 0:
                current_cost = total_cost / current_shares
            position[0] = current_shares
            position[1] = current_cost
            continue

        if action == "SELL":
            if current_shares <= 0:
                warnings.append(f"{symbol}: sell with no open shares, skipped.")
                continue

            sells = min(current_shares, shares)
            realized_cost = sells * current_cost
            realized_notional += realized_cost
            realized_pnl += sells * (price - current_cost)
            if fee > 0:
                realized_pnl -= fee * (sells / shares)

            current_shares -= sells
            if current_shares <= 0:
                current_shares = 0.0
                current_cost = 0.0
            position[0] = current_shares
            position[1] = current_cost
            if shares > sells:
                warnings.append(f"{symbol}: sell exceeds open position by {shares - sells:.6g}; extra shares ignored.")
            continue

        warnings.append(f"{symbol}: unknown action '{trade.action}', skipped.")

    holdings: list[PortfolioHolding] = []
    for symbol, (shares, avg_cost) in positions.items():
        holdings.append(
            PortfolioHolding(
                symbol=symbol,
                shares=shares,
                cost_basis=avg_cost if shares > 0 else None,
            )
        )

    realized_pnl_pct = realized_pnl / realized_notional if realized_notional > 0 else 0.0
    return holdings, TradeLedgerSummary(
        realized_pnl=realized_pnl,
        realized_pnl_pct=realized_pnl_pct,
        realized_notional=realized_notional,
        trade_count=trade_count,
        warnings=tuple(warnings),
    )


def _normalize_action(value: str) -> str:
    value = value.strip().upper()
    if value in {"BUY", "B", "+"}:
        return "BUY"
    if value in {"SELL", "S", "-"}:
        return "SELL"
    return value


def _trade_sort_key(trade: PortfolioTrade) -> tuple[int, str]:
    if trade.trade_date:
        return (0, trade.trade_date)
    return (1, trade.symbol.upper())


def _score_concentration_risk(weights: Dict[str, float]) -> float:
    sorted_weights = sorted(weights.values(), reverse=True)
    if not sorted_weights:
        return 1.0

    max_weight = sorted_weights[0]
    top3_weight = sum(sorted_weights[:3]) if len(sorted_weights) >= 3 else sum(sorted_weights)

    # Conservative interpretation of concentration:
    # - keep risk low near 0.0 until one position dominates.
    # - ramp up strongly as top concentration rises and breadth narrows.
    max_component = max(0.0, min(1.0, (max_weight - 0.15) / 0.35))
    top3_component = 0.0 if len(sorted_weights) <= 1 else max(0.0, min(1.0, (top3_weight - 0.30) / 0.50))
    breadth_penalty = 0.0
    if top3_weight > 0:
        breadth_penalty = max(0.0, 1.0 - min(1.0, len(sorted_weights) / 12.0))

    return round(float(min(1.0, 0.50 * max_component + 0.30 * top3_component + 0.20 * breadth_penalty)), 4)


def _portfolio_recommendation(
    *,
    weighted_down_probability: float,
    concentration_risk: float,
    weighted_composite: float,
    weighted_confidence: float,
    sell_pressure: float,
    buy_pressure: float,
    effective_breadth: float,
) -> PortfolioRecommendation:
    urgency = max(
        0.0,
        0.55 * weighted_down_probability
        + 0.30 * concentration_risk
        + 0.15 * max(0.0, -weighted_composite),
    )

    if urgency > 0.75 or weighted_confidence < 0.35:
        if sell_pressure > 0.55:
            return PortfolioRecommendation(
                label="tighten_risk",
                score=round(float(min(1.0, urgency + 0.15)), 4),
                explanation="High downside + concentration + bearish spread suggests reducing risk or rebalancing.",
            )
        return PortfolioRecommendation(
            label="reduce_exposure",
            score=round(float(min(1.0, urgency)), 4),
            explanation="Signals indicate elevated portfolio risk; consider reducing new risk and monitoring entries.",
        )

    if weighted_down_probability <= 0.30 and weighted_composite >= 0.08 and concentration_risk <= 0.35:
        if buy_pressure >= 0.55:
            return PortfolioRecommendation(
                label="good_add_window",
                score=round(float(max(0.0, 1.0 - urgency)), 4),
                explanation="Portfolio context is supportive for adding with position sizing discipline.",
            )
        return PortfolioRecommendation(
            label="stay_ready",
            score=round(float(max(0.0, 1.0 - urgency)), 4),
            explanation="Portfolio risk is manageable; stay disciplined and wait for high-quality entries.",
        )

    if effective_breadth < 2.0 and weighted_composite < 0.0:
        return PortfolioRecommendation(
            label="concentration_warning",
            score=round(float(min(1.0, 0.55 + concentration_risk)), 4),
            explanation="Narrow/low-breadth portfolio on weak signal; avoid over-allocating any single name.",
        )

    return PortfolioRecommendation(
        label="balanced",
        score=round(float(1.0 - urgency), 4),
        explanation="Balanced setup; avoid forced action unless your watchlist triggers improve confidence.",
    )
