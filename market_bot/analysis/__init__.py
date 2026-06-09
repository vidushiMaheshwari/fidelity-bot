"""Core analysis package.

The analysis layer is now organized as:

- ``analysis.stocks`` for per-symbol technical analysis.
- ``analysis.portfolio`` for portfolio-level risk context.
"""

from market_bot.analysis.stocks import analyze_symbol, confidence_score, add_derived_features

__all__ = [
    "analyze_symbol",
    "confidence_score",
    "add_derived_features",
]
