from __future__ import annotations

import argparse
import csv
import os
import json
import sqlite3
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from market_bot.analysis.portfolio import (
    PortfolioAnalysis,
    PortfolioHolding,
    PortfolioTrade,
    aggregate_holdings_from_trades,
    analyze_portfolio,
)
from market_bot.analysis.stocks.scorer import analyze_symbol
from market_bot.ops.ranking import RankedDecision, rank_decisions
from market_bot.data_fetch import fetch_history
from market_bot.models import AnalysisDecision
from market_bot.ops.scheduler import (
    build_analyze_command,
    build_cron_line,
    build_dual_analyze_command,
    install_cron_line,
)
from market_bot.storage import (
    connect_db,
    load_portfolio_holdings,
    save_analyses,
    save_portfolio_holdings,
    save_run,
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return run_analyze(args)
    if args.command == "history":
        return run_history(args)
    if args.command == "schedule":
        return run_schedule(args)
    return 1


def run_analyze(args: argparse.Namespace) -> int:
    symbols = _load_symbols(args)
    if not symbols:
        print("No symbols provided. Use --symbols or --watchlist.")
        return 1

    if not 0.0 <= args.confidence_threshold <= 1.0:
        print("--confidence-threshold must be between 0 and 1.")
        return 2
    if not 0.0 <= args.criticality_threshold <= 1.0:
        print("--criticality-threshold must be between 0 and 1.")
        return 2
    if not 0.0 <= args.min_critical_confidence <= 1.0:
        print("--min-critical-confidence must be between 0 and 1.")
        return 2

    end = date.today()
    start = end - timedelta(days=max(args.history_days, 45))
    run_id = str(uuid.uuid4())
    benchmark = args.benchmark.strip().upper() if args.benchmark else "SPY"
    db_path = _resolve_db_path(
        args.db_path,
        user_id=args.user_id,
        user_root=args.user_root,
    )
    portfolio_holdings = _resolve_portfolio_holdings_path(
        user_id=args.user_id,
        user_root=args.user_root,
        holdings_override=args.portfolio_holdings,
    )
    portfolio_ledger = _resolve_portfolio_ledger_path(
        user_id=args.user_id,
        user_root=args.user_root,
        ledger_override=args.portfolio_ledger,
    )
    if args.portfolio_holdings is None:
        _ensure_portfolio_file(portfolio_holdings, header="symbol,quantity,avg_cost\n")
    if args.portfolio_ledger is None:
        _ensure_portfolio_file(
            portfolio_ledger,
            header="trade_date,action,symbol,shares,price,fee,currency,notes\n",
        )
    db_conn = None
    should_use_portfolio_db = (
        args.use_portfolio_db
        or args.persist_portfolio_holdings
        or not args.no_save
    )
    if should_use_portfolio_db:
        try:
            db_conn = connect_db(db_path)
        except Exception as exc:
            print(f"Unable to open analytics DB '{db_path}': {exc}")
            return 2

    all_symbols = sorted(set(symbols + [benchmark]))
    downloaded = {item.symbol: item.data for item in fetch_history(all_symbols, start=start, end=end)}
    if benchmark not in downloaded:
        print(f"Unable to fetch benchmark '{benchmark}', benchmark-relative analysis skipped.")

    benchmark_series = None
    if benchmark in downloaded:
        benchmark_series = downloaded[benchmark].set_index("Date")["Close"].squeeze()

    decisions: List[AnalysisDecision] = []
    failures = []
    for symbol in symbols:
        data = downloaded.get(symbol)
        if data is None or data.empty:
            failures.append(f"{symbol}: missing price data")
            continue
        try:
            decision = analyze_symbol(
                symbol,
                data,
                benchmark=benchmark_series,
                analysis_intent=args.intent,
                confidence_threshold=args.confidence_threshold,
                risk_profile=args.risk_profile,
            )
            decisions.append(decision)
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")

    if not decisions:
        print("No decisions could be produced due to data issues.")
        for failure in failures:
            print(f"- {failure}")
        if db_conn is not None:
            db_conn.close()
        return 2

    if args.no_rank:
        ranked_by_criticality = [
            (
                RankedDecision(symbol=decision.symbol, criticality="watch", score=0.0, rank=index + 1),
                decision,
            )
            for index, decision in enumerate(decisions)
        ]
    else:
        ranked_by_criticality = [
            (
                RankedDecision(symbol=info.symbol, criticality=info.criticality, score=info.score, rank=index + 1),
                decision,
            )
            for index, (info, decision) in enumerate(
                rank_decisions(
                    decisions,
                    criticality_threshold=args.criticality_threshold,
                    min_critical_confidence=args.min_critical_confidence,
                )
            )
        ]

    portfolio_snapshot = None
    try:
        portfolio_snapshot = _build_portfolio_snapshot(
            decisions=decisions,
            holdings_file=portfolio_holdings,
            ledger_file=portfolio_ledger,
            holdings_conn=db_conn,
            use_holdings_db=args.use_portfolio_db,
        )
    except Exception as exc:
        failures.append(f"portfolio snapshot error: {exc}")

    if args.persist_portfolio_holdings and db_conn is not None:
        file_holdings = []
        if args.portfolio_holdings:
            file_holdings = _load_portfolio_holdings(args.portfolio_holdings)
        elif not args.use_portfolio_db:
            file_holdings = _load_portfolio_holdings(portfolio_holdings)
        if file_holdings:
            try:
                save_portfolio_holdings(db_conn, file_holdings)
            except Exception as exc:
                failures.append(f"portfolio persist error: {exc}")

    run_start = min(decision.week_start for decision in decisions)
    run_end = max(decision.week_end for decision in decisions)
    ranked_payloads = [
        {
            **decision.as_payload(),
            "criticality": ranked_decision.criticality,
            "criticality_score": ranked_decision.score,
            "rank": ranked_decision.rank,
        }
        for ranked_decision, decision in ranked_by_criticality
    ]

    if not args.no_save and db_conn is not None:
        save_run(
            db_conn,
            run_id=run_id,
            benchmark=benchmark,
            analysis_intent=args.intent,
            risk_profile=args.risk_profile,
            confidence_threshold=args.confidence_threshold,
            symbols=symbols,
            week_start=run_start.isoformat(),
            week_end=run_end.isoformat(),
            portfolio_snapshot_json=portfolio_snapshot.as_payload() if portfolio_snapshot else None,
        )
        save_analyses(
            db_conn,
            run_id=run_id,
            analyses=[decision for _, decision in ranked_by_criticality],
            payload_overrides=ranked_payloads,
        )

    if db_conn is not None:
        db_conn.close()

    if args.output_json:
        payload = {
            "run_id": run_id if not args.no_save else None,
            "run_start": run_start.isoformat(),
            "run_end": run_end.isoformat(),
            "benchmark": benchmark,
            "analysis_intent": args.intent,
            "risk_profile": args.risk_profile,
            "confidence_threshold": args.confidence_threshold,
            "criticality_threshold": args.criticality_threshold,
            "min_critical_confidence": args.min_critical_confidence,
            "decisions": ranked_payloads,
            "portfolio_snapshot": portfolio_snapshot.as_payload() if portfolio_snapshot else None,
            "issues": failures,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Run ID: {run_id if not args.no_save else 'n/a'}")
        print(f"Window: {run_start} -> {run_end}")
        print(f"Benchmark: {benchmark}")
        print(f"Intent: {args.intent}")
        print(f"Risk profile: {args.risk_profile}")
        print(f"Confidence threshold: {args.confidence_threshold:.2f}")
        print(f"Criticality threshold: {args.criticality_threshold:.2f}")
        if portfolio_snapshot is not None:
            print("")
            print("Portfolio risk snapshot:")
            print(f"  symbols: {portfolio_snapshot.symbol_count}")
            print(f"  weighted down probability: {portfolio_snapshot.weighted_down_probability:.2%}")
            print(f"  weighted confidence: {portfolio_snapshot.weighted_confidence:.2%}")
            print(f"  weighted composite: {portfolio_snapshot.weighted_composite:+.2f}")
            print(f"  expected week return: {portfolio_snapshot.expected_week_return:.2%}")
            print(f"  concentration risk: {portfolio_snapshot.concentration_risk:.2f}")
            print(f"  hhi: {portfolio_snapshot.hhi:.2f}")
            print(f"  effective breadth: {portfolio_snapshot.effective_breadth:.2f}")
            print(f"  top3 concentration: {portfolio_snapshot.top3_weight:.2%}")
            print(
                "  pressure: "
                f"buy={portfolio_snapshot.buy_pressure:.2%}, "
                f"sell={portfolio_snapshot.sell_pressure:.2%}"
            )
            print(f"  notional gross: {portfolio_snapshot.notional_gross:.2f}")
            print(
                "  unrealized P&L: "
                f"{portfolio_snapshot.unrealized_pnl:.2f} ({portfolio_snapshot.unrealized_pnl_pct:.2%})"
            )
            print(
                "  realized P&L: "
                f"{portfolio_snapshot.realized_pnl:.2f} ({portfolio_snapshot.realized_pnl_pct:.2%}) "
                f"| realized_notional={portfolio_snapshot.realized_notional:.2f} "
                f"| trades={portfolio_snapshot.realized_trade_count}"
            )
            print(
                f"  portfolio recommendation: "
                f"{portfolio_snapshot.recommendation.label} "
                f"({portfolio_snapshot.recommendation.score:.2f})"
            )
            print(f"  rationale: {portfolio_snapshot.recommendation.explanation}")
        print("")
        for ranked_decision, decision in ranked_by_criticality:
            print(
                f"{ranked_decision.rank:>2} | {decision.symbol:>4} | "
                f"{ranked_decision.criticality:>14} | score={ranked_decision.score:.2f}"
            )
            print(f"     intent: {decision.analysis_intent}")
            print(f"     confidence: {decision.confidence:.2f}")
            print(f"     weekly: {decision.pct_change_week:.2%}")
            print(f"     composite score: {decision.composite_score:.2f}")
            print(f"     down probability: {decision.down_probability:.2%}")
            print(f"     recommendation: {decision.recommendation}")
            print(f"     rationale: {decision.rationale}")
            print("")
        if failures:
            print("Issues:")
            for failure in failures:
                print(f"  - {failure}")

    return 0


def run_schedule(args: argparse.Namespace) -> int:
    symbols = _load_symbols(args)
    owned_symbols = _load_symbols_from_args(args.owned_symbols, args.owned_watchlist)
    candidate_symbols = _load_symbols_from_args(args.buy_symbols, args.buy_watchlist)

    portfolio_mode = _is_portfolio_mode(args)
    if portfolio_mode:
        if not owned_symbols:
            owned_symbols = _load_symbols_from_args(args.symbols, None)
        buy_scan_symbols = candidate_symbols or _load_symbols_from_args(None, args.watchlist)
        if not buy_scan_symbols:
            buy_scan_symbols = []
        if not owned_symbols and not buy_scan_symbols:
            print("No symbols provided. Use --owned-symbols/--owned-watchlist and/or --symbols for portfolio mode.")
            return 1
        if not owned_symbols:
            print("No owned symbols provided. Use --owned-symbols/--owned-watchlist for hold intent scan.")
            return 1
    else:
        if not symbols:
            print("No symbols provided. Use --symbols or --watchlist.")
            return 1

    if args.run_now:
        print("Running immediate analysis...")
        if portfolio_mode:
            return _run_portfolio_now(args, owned_symbols, buy_scan_symbols)
        return run_analyze(args)

    if portfolio_mode:
        resolved_holdings = _resolve_portfolio_holdings_path(
            user_id=args.user_id,
            user_root=args.user_root,
            holdings_override=args.portfolio_holdings,
        )
        resolved_ledger = _resolve_portfolio_ledger_path(
            user_id=args.user_id,
            user_root=args.user_root,
            ledger_override=args.portfolio_ledger,
        )
        analyze_command = build_dual_analyze_command(
            python_executable=sys.executable,
            owned_symbols=owned_symbols,
            watchlist_symbols=sorted(set(owned_symbols + buy_scan_symbols)),
            portfolio_holdings=resolved_holdings,
            portfolio_ledger=resolved_ledger,
            benchmark=args.benchmark,
            risk_profile=args.risk_profile,
            confidence_threshold=args.confidence_threshold,
            criticality_threshold=args.criticality_threshold,
            min_critical_confidence=args.min_critical_confidence,
            history_days=args.history_days,
            db_path=_resolve_db_path(
                args.db_path,
                user_id=args.user_id,
                user_root=args.user_root,
            ),
            extra_args=_collect_schedule_extra_args(args),
        )
    else:
        resolved_holdings = _resolve_portfolio_holdings_path(
            user_id=args.user_id,
            user_root=args.user_root,
            holdings_override=args.portfolio_holdings,
        )
        resolved_ledger = _resolve_portfolio_ledger_path(
            user_id=args.user_id,
            user_root=args.user_root,
            ledger_override=args.portfolio_ledger,
        )
        analyze_command = build_analyze_command(
            python_executable=sys.executable,
            symbols=symbols,
            portfolio_holdings=resolved_holdings,
            portfolio_ledger=resolved_ledger,
            benchmark=args.benchmark,
            intent=args.intent,
            risk_profile=args.risk_profile,
            confidence_threshold=args.confidence_threshold,
            criticality_threshold=args.criticality_threshold,
            min_critical_confidence=args.min_critical_confidence,
            history_days=args.history_days,
            db_path=_resolve_db_path(
                args.db_path,
                user_id=args.user_id,
                user_root=args.user_root,
            ),
            extra_args=_collect_schedule_extra_args(args),
        )

    cron_line = build_cron_line(
        cron_time=args.time,
        command=analyze_command,
        working_directory=Path.cwd(),
        log_file=args.cron_log,
    )

    if args.install:
        install_cron_line(cron_line)
        print("Installed daily schedule in user crontab.")
        print(cron_line)
        return 0

    print("Generated daily command:")
    print(analyze_command)
    print("")
    print("Cron line:")
    print(cron_line)
    return 0


def _collect_schedule_extra_args(args: argparse.Namespace) -> List[str]:
    extra: List[str] = []
    if args.no_save:
        extra.append("--no-save")
    if args.no_rank:
        extra.append("--no-rank")
    if args.output_json:
        extra.append("--output-json")
    if args.use_portfolio_db:
        extra.append("--use-portfolio-db")
    if args.persist_portfolio_holdings:
        extra.append("--persist-portfolio-holdings")
    if getattr(args, "user_id", None):
        extra.append(f"--user-id={args.user_id}")
    if getattr(args, "user_root", None):
        extra.append(f"--user-root={args.user_root}")
    return extra


def run_history(args: argparse.Namespace) -> int:
    from market_bot.storage import connect_db, list_recent_runs

    db = connect_db(
        _resolve_db_path(
            args.db_path,
            user_id=getattr(args, "user_id", "me"),
            user_root=getattr(args, "user_root", "data/user"),
        )
    )
    rows = list_recent_runs(db, limit=args.limit)
    db.close()
    if not rows:
        print("No runs found.")
        return 1
    for row in rows:
        print(
            f"{row[0]} | {row[1]} | benchmark={row[2]} | intent={row[3]} | "
            f"risk={row[4]} | threshold={row[5]} | window={row[6]}→{row[7]} | symbols={row[8]}"
        )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Market analysis CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Run analysis for symbol list")
    _add_analysis_arguments(analyze_parser)
    analyze_parser.add_argument("--no-rank", action="store_true", help="Skip criticality ordering")

    history_parser = subparsers.add_parser("history", help="Show most recent saved runs")
    history_parser.add_argument("--user-id", default="me", help="Logical user namespace for local state files.")
    history_parser.add_argument(
        "--user-root",
        default="data/user",
        help="Root directory containing per-user folders.",
    )
    history_parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Storage location for analysis runs. Defaults to <user-root>/<user-id>/analytics.db. "
            "You can also pass a PostgreSQL URL (for example postgresql://...)."
        ),
    )
    history_parser.add_argument("--limit", type=int, default=10)

    schedule_parser = subparsers.add_parser("schedule", help="Generate or install daily analysis trigger")
    _add_analysis_arguments(schedule_parser)
    schedule_parser.add_argument("--time", default="09:30", help="Execution time in local HH:MM")
    schedule_parser.add_argument("--cron-log", default=None, help="Optional cron log file")
    schedule_parser.add_argument(
        "--owned-symbols",
        default=None,
        help="Comma-separated list of symbols you already own.",
    )
    schedule_parser.add_argument(
        "--owned-watchlist",
        default=None,
        help="Path to file with owned symbols (one per line).",
    )
    schedule_parser.add_argument(
        "--buy-symbols",
        default=None,
        help="Optional comma-separated symbols for buy-scan candidates.",
    )
    schedule_parser.add_argument(
        "--buy-watchlist",
        default=None,
        help="Optional path to file with buy watchlist symbols (one per line).",
    )
    schedule_parser.add_argument("--no-rank", action="store_true", help="Skip criticality ordering in generated analysis run")
    schedule_parser.add_argument(
        "--install",
        action="store_true",
        help="Write schedule into current user crontab",
    )
    schedule_parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run analysis once now using the same options",
    )

    return parser


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", default="me", help="Logical user namespace for local state files.")
    parser.add_argument(
        "--user-root",
        default="data/user",
        help="Root directory containing per-user folders.",
    )
    parser.add_argument("--symbols", help="Comma-separated ticker symbols, e.g. AAPL,MSFT")
    parser.add_argument("--watchlist", help="Path to text file with one symbol per line")
    parser.add_argument(
        "--portfolio-holdings",
        default=None,
        help=(
            "Optional CSV/JSON file with owned shares and cost basis for weighted portfolio metrics. "
            "Defaults to <user-root>/<user-id>/portfolio_holdings.csv."
        ),
    )
    parser.add_argument(
        "--portfolio-ledger",
        default=None,
        help=(
            "Optional CSV/JSON transaction ledger used to reconstruct holdings and realized P&L. "
            "Defaults to <user-root>/<user-id>/portfolio_ledger.csv."
        ),
    )
    parser.add_argument(
        "--use-portfolio-db",
        action="store_true",
        help=(
            "Load/merge portfolio holdings from DB instead of file-only input."
        ),
    )
    parser.add_argument(
        "--persist-portfolio-holdings",
        action="store_true",
        help=(
            "Persist loaded CSV/JSON holdings into the configured DB for future scheduled runs."
        ),
    )
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol for relative strength")
    parser.add_argument(
        "--intent",
        choices=["hold", "buy"],
        default="hold",
        help="Analysis intent: 'buy' for pre-purchase checks, 'hold' for existing positions",
    )
    parser.add_argument(
        "--risk-profile",
        choices=["conservative", "moderate", "aggressive"],
        default="moderate",
        dest="risk_profile",
        help="Risk profile for recommendation strictness",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.0,
        help="Only emit high-confidence recommendations (0.0-1.0). Lower values = more signals.",
    )
    parser.add_argument(
        "--criticality-threshold",
        type=float,
        default=0.0,
        help="Filter ranked outputs below this score",
    )
    parser.add_argument(
        "--min-critical-confidence",
        type=float,
        default=0.6,
        help="Minimum confidence required to classify critical actions",
    )
    parser.add_argument("--history-days", type=int, default=90, help="Historical lookback used for indicators")
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Storage location for analysis runs. Defaults to <user-root>/<user-id>/analytics.db. "
            "You can also pass a PostgreSQL URL (for example postgresql://...)."
        ),
    )
    parser.add_argument("--output-json", action="store_true", help="Emit JSON result")
    parser.add_argument("--no-save", action="store_true", help="Skip writing run and decisions to DB")


def _load_symbols(args: argparse.Namespace) -> List[str]:
    symbols: List[str] = []
    if args.symbols:
        symbols.extend([symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()])
    if args.watchlist:
        watchlist_path = Path(args.watchlist)
        if watchlist_path.exists():
            for line in watchlist_path.read_text().splitlines():
                value = line.strip().upper()
                if value:
                    symbols.append(value)
    return sorted(set(symbols))


def _build_portfolio_snapshot(
    decisions: List[AnalysisDecision],
    holdings_file: str | None,
    ledger_file: str | None,
    holdings_conn: sqlite3.Connection | None = None,
    use_holdings_db: bool = False,
) -> Optional[PortfolioAnalysis]:
    holdings: list[PortfolioHolding] = []
    if holdings_file:
        holdings = _load_portfolio_holdings(holdings_file)

    ledger_summary = None
    if ledger_file:
        ledger_trades = _load_portfolio_ledger(ledger_file)
        if ledger_trades:
            ledger_holdings, ledger_summary = aggregate_holdings_from_trades(ledger_trades)
            holdings = _merge_holdings(holdings, ledger_holdings)

    db_holdings: list[PortfolioHolding] = []
    if use_holdings_db and holdings_conn is not None:
        db_holdings = load_portfolio_holdings(holdings_conn)

    holdings = _merge_holdings(holdings, db_holdings)
    return analyze_portfolio(
        decisions=decisions,
        holdings=holdings if holdings else None,
        ledger_summary=ledger_summary,
    )


def _merge_holdings(
    file_holdings: List[PortfolioHolding],
    db_holdings: List[PortfolioHolding],
) -> List[PortfolioHolding]:
    merged: dict[str, PortfolioHolding] = {}
    for holding in db_holdings:
        merged[holding.symbol.upper()] = holding
    for holding in file_holdings:
        merged[holding.symbol.upper()] = holding
    return sorted(list(merged.values()), key=lambda holding: holding.symbol)


def _load_portfolio_holdings(path: str) -> list[PortfolioHolding]:
    holdings_path = Path(path)
    if not holdings_path.exists():
        raise ValueError(f"portfolio holdings file not found: {path}")

    text = holdings_path.read_text()
    if not text.strip():
        return []

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return _parse_holdings_csv(text)

    if isinstance(raw, dict):
        rows = []
        for symbol, value in raw.items():
            if isinstance(value, dict):
                row = {k.lower(): v for k, v in value.items()}
                row["symbol"] = symbol
                rows.append(row)
            else:
                rows.append({"symbol": symbol, "shares": value})
        return _parse_holdings_dicts(rows)

    if isinstance(raw, list):
        normalized = [row if isinstance(row, dict) else {} for row in raw]
        return _parse_holdings_dicts(normalized)

    raise ValueError("Unsupported holdings JSON format. Use list or object.")


def _parse_holdings_dicts(rows: list[dict]) -> list[PortfolioHolding]:
    holdings = []
    for row in rows:
        symbol = _get_value(row, "symbol", "ticker")
        if not symbol:
            continue
        shares = _parse_optional_float(_get_value(row, "shares", "quantity", "qty"))
        cost_basis = _parse_optional_float(
            _get_value(row, "cost_basis", "avg_cost", "cost", "average_cost")
        )
        holdings.append(
            PortfolioHolding(
                symbol=symbol.upper(),
                shares=shares,
                cost_basis=cost_basis,
            )
        )
    return holdings


def _parse_holdings_csv(text: str) -> list[PortfolioHolding]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    reader = csv.reader(lines)
    first_row = next(reader, None)
    if first_row is None:
        return []

    normalized_first = [value.strip().lower() for value in first_row]
    headers_present = any("symbol" in value or "ticker" in value for value in normalized_first)

    holdings = []
    if headers_present:
        for row in csv.DictReader(lines):
            row_dict = {key.lower(): value for key, value in row.items()}
            symbol = _get_value(row_dict, "symbol", "ticker")
            if not symbol:
                continue
            shares = _parse_optional_float(_get_value(row_dict, "shares", "quantity", "qty", "holding"))
            cost_basis = _parse_optional_float(
                _get_value(row_dict, "cost_basis", "avg_cost", "cost", "average_cost")
            )
            holdings.append(
                PortfolioHolding(
                    symbol=symbol.upper(),
                    shares=shares,
                    cost_basis=cost_basis,
                )
            )
        return holdings

    # Fallback: positional format: symbol,shares,cost_basis
    for row in [first_row] + list(reader):
        if not row:
            continue
        symbol = row[0].strip()
        if not symbol:
            continue
        shares = _parse_optional_float(row[1]) if len(row) > 1 else None
        cost = _parse_optional_float(row[2]) if len(row) > 2 else None
        holdings.append(PortfolioHolding(symbol=symbol.upper(), shares=shares, cost_basis=cost))
    return holdings


def _load_portfolio_ledger(path: str) -> list[PortfolioTrade]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        raise ValueError(f"portfolio ledger file not found: {path}")

    text = ledger_path.read_text()
    if not text.strip():
        return []

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return _parse_ledger_csv(text)

    if isinstance(raw, dict):
        rows = []
        for symbol, value in raw.items():
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict):
                        normalized = {k.lower(): v for k, v in row.items()}
                        normalized["symbol"] = normalized.get("symbol") or symbol
                        rows.append(normalized)
            elif isinstance(value, dict):
                normalized = {k.lower(): v for k, v in value.items()}
                normalized["symbol"] = normalized.get("symbol") or symbol
                rows.append(normalized)
        return _parse_ledger_rows(rows)

    if isinstance(raw, list):
        normalized = [row if isinstance(row, dict) else {} for row in raw]
        return _parse_ledger_rows(normalized)

    raise ValueError("Unsupported ledger JSON format. Use list of trades or symbol-indexed object.")


def _parse_ledger_rows(rows: list[dict]) -> list[PortfolioTrade]:
    trades: list[PortfolioTrade] = []
    for row in rows:
        symbol = _get_value(row, "symbol", "ticker")
        action = _get_value(row, "action", "side", "type")
        shares = _parse_optional_float(_get_value(row, "shares", "quantity", "qty"))
        price = _parse_optional_float(_get_value(row, "price", "execution_price", "fill_price"))
        if not symbol or not action or shares is None or price is None:
            continue
        fee = _parse_optional_float(_get_value(row, "fee", "commission", "fees"))
        trade_date = _get_value(row, "trade_date", "date", "timestamp")
        currency = _get_value(row, "currency", "ccy")
        notes = _get_value(row, "notes", "note", "comment")
        trades.append(
            PortfolioTrade(
                symbol=symbol.upper(),
                action=action,
                shares=shares,
                price=price,
                fee=fee,
                trade_date=trade_date,
                currency=currency,
                notes=notes,
            )
        )
    return trades


def _parse_ledger_csv(text: str) -> list[PortfolioTrade]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    reader = csv.reader(lines)
    first_row = next(reader, None)
    if first_row is None:
        return []

    normalized_first = [value.strip().lower() for value in first_row]
    headers_present = any(
        "symbol" in value or "ticker" in value
        for value in normalized_first
    )

    if headers_present:
        trades = []
        for row in csv.DictReader(lines):
            row_dict = {key.lower(): value for key, value in row.items()}
            trades.extend(_parse_ledger_rows([row_dict]))
        return trades

    # Fallback positional format: symbol,action,shares,price,fee
    trades: list[PortfolioTrade] = []
    for row in [first_row] + list(reader):
        if not row or len(row) < 4:
            continue
        symbol = row[0].strip()
        action = row[1].strip()
        shares = _parse_optional_float(row[2])
        price = _parse_optional_float(row[3])
        fee = _parse_optional_float(row[4]) if len(row) > 4 else None
        if not symbol or not action or shares is None or price is None:
            continue
        trades.append(
            PortfolioTrade(
                symbol=symbol.upper(),
                action=action,
                shares=shares,
                price=price,
                fee=fee,
            )
        )
    return trades


def _parse_optional_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _get_value(row: dict, *keys: str) -> Optional[str]:
    for key in keys:
        if key in row and row[key] is not None:
            raw = str(row[key]).strip()
            if raw:
                return raw
    return None


def _load_symbols_from_args(symbols: str | None, watchlist_path: str | None) -> List[str]:
    values: List[str] = []
    if symbols:
        values.extend([symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()])
    if watchlist_path:
        path = Path(watchlist_path)
        if path.exists():
            for line in path.read_text().splitlines():
                value = line.strip().upper()
                if value:
                    values.append(value)
    return sorted(set(values))


def _is_portfolio_mode(args: argparse.Namespace) -> bool:
    return bool(
        args.owned_symbols
        or args.owned_watchlist
        or args.buy_symbols
        or args.buy_watchlist
    )


def _run_portfolio_now(
    args: argparse.Namespace,
    owned_symbols: List[str],
    buy_scan_symbols: List[str],
) -> int:
    hold_symbol_arg = ",".join(owned_symbols)
    buy_union = sorted(set(owned_symbols).union(set(buy_scan_symbols)))
    buy_symbol_arg = ",".join(buy_union)

    hold_args = argparse.Namespace(**{**vars(args)})
    hold_args.symbols = hold_symbol_arg
    hold_args.watchlist = None
    hold_args.intent = "hold"
    hold_result = run_analyze(hold_args)

    buy_args = argparse.Namespace(**{**vars(args)})
    buy_args.symbols = buy_symbol_arg
    buy_args.watchlist = None
    buy_args.intent = "buy"
    buy_result = run_analyze(buy_args)

    return max(hold_result, buy_result)


def _resolve_db_path(
    path: str | None,
    user_id: str = "me",
    user_root: str = "data/user",
) -> str:
    resolved_path = path or os.getenv("SUPABASE_DATABASE_URL") or os.getenv("MARKET_BOT_ANALYTICS_DB_URL")
    if _is_postgres_like_connection(resolved_path):
        return resolved_path

    if path is None:
        normalized = _resolve_user_dir(user_id=user_id, user_root=user_root) / "analytics.db"
    else:
        normalized = Path(path)
    if not normalized.is_absolute():
        normalized = Path.cwd() / normalized
    return str(normalized)


def _is_postgres_like_connection(value: str | None) -> bool:
    if not value:
        return False
    value_lower = value.lower()
    return value_lower.startswith("postgresql://") or value_lower.startswith("postgres://")


def _resolve_user_dir(user_id: str | None, user_root: str | None = None) -> Path:
    root = Path(user_root) if user_root else Path("data/user")
    root.mkdir(parents=True, exist_ok=True)
    user = (user_id or "me").strip() or "me"
    user_dir = root / user
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _resolve_portfolio_holdings_path(
    user_id: str,
    user_root: str,
    holdings_override: str | None,
) -> str:
    if holdings_override:
        holdings_path = Path(holdings_override)
    else:
        holdings_path = _resolve_user_dir(user_id=user_id, user_root=user_root) / "portfolio_holdings.csv"
    if not holdings_path.is_absolute():
        holdings_path = Path.cwd() / holdings_path
    return str(holdings_path)


def _resolve_portfolio_ledger_path(
    user_id: str,
    user_root: str,
    ledger_override: str | None,
) -> str:
    if ledger_override:
        ledger_path = Path(ledger_override)
    else:
        ledger_path = _resolve_user_dir(user_id=user_id, user_root=user_root) / "portfolio_ledger.csv"
    if not ledger_path.is_absolute():
        ledger_path = Path.cwd() / ledger_path
    return str(ledger_path)


def _ensure_portfolio_file(path: str, header: str | None = None) -> None:
    holdings_path = Path(path)
    if not holdings_path.parent.exists():
        holdings_path.parent.mkdir(parents=True, exist_ok=True)
    if not holdings_path.exists():
        if header is None:
            header = "symbol,shares,cost_basis\n"
        holdings_path.write_text(header)


if __name__ == "__main__":
    raise SystemExit(main())
