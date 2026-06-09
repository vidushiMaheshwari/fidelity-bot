from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List, Sequence

from shlex import quote

MARKET_BOT_CRON_MARKER = "# market-bot-daily"


def build_cron_line(
    cron_time: str,
    command: str,
    working_directory: Path | str,
    log_file: str | None = None,
) -> str:
    hour, minute = _parse_time(cron_time)
    workdir = Path(working_directory).resolve()
    target = command.strip()
    if not target:
        raise ValueError("command cannot be empty for cron scheduling.")
    if log_file is None:
        log_path = ""
    else:
        log_target = Path(log_file).resolve().as_posix()
        log_path = f" >> {quote(log_target)} 2>&1"
    return f"{minute} {hour} * * * (cd {quote(workdir.as_posix())} && {target}){log_path} {MARKET_BOT_CRON_MARKER}"


def build_analyze_command(
    python_executable: str,
    symbols: Sequence[str],
    portfolio_holdings: str | None = None,
    portfolio_ledger: str | None = None,
    *,
    benchmark: str,
    intent: str,
    risk_profile: str,
    confidence_threshold: float,
    criticality_threshold: float,
    min_critical_confidence: float,
    history_days: int,
    db_path: str,
    extra_args: Iterable[str] | None = None,
) -> str:
    args: List[str] = [
        quote(python_executable),
        "-m",
        "market_bot.cli",
        "analyze",
        "--symbols",
        ",".join(symbols),
        "--benchmark",
        quote(benchmark),
        "--intent",
        quote(intent),
        "--risk-profile",
        quote(risk_profile),
        "--confidence-threshold",
        f"{confidence_threshold}",
        "--criticality-threshold",
        f"{criticality_threshold}",
        "--min-critical-confidence",
        f"{min_critical_confidence}",
        "--history-days",
        f"{history_days}",
        "--db-path",
        quote(db_path),
    ]
    if portfolio_holdings is not None:
        args.extend(["--portfolio-holdings", quote(portfolio_holdings)])
    if portfolio_ledger is not None:
        args.extend(["--portfolio-ledger", quote(portfolio_ledger)])
    if extra_args:
        args.extend(extra_args)
    return " ".join(args)


def build_dual_analyze_command(
    python_executable: str,
    owned_symbols: Sequence[str],
    watchlist_symbols: Sequence[str],
    portfolio_holdings: str | None = None,
    portfolio_ledger: str | None = None,
    *,
    benchmark: str,
    risk_profile: str,
    confidence_threshold: float,
    criticality_threshold: float,
    min_critical_confidence: float,
    history_days: int,
    db_path: str,
    extra_args: Iterable[str] | None = None,
) -> str:
    hold_analysis_symbols = sorted(set(owned_symbols))
    buy_analysis_symbols = sorted(set(watchlist_symbols).union(owned_symbols))
    hold_command = build_analyze_command(
        python_executable=python_executable,
        symbols=hold_analysis_symbols,
        portfolio_holdings=portfolio_holdings,
        benchmark=benchmark,
        intent="hold",
        risk_profile=risk_profile,
        confidence_threshold=confidence_threshold,
        criticality_threshold=criticality_threshold,
        min_critical_confidence=min_critical_confidence,
        history_days=history_days,
        db_path=db_path,
        extra_args=extra_args,
        portfolio_ledger=portfolio_ledger,
    )
    buy_command = build_analyze_command(
        python_executable=python_executable,
        symbols=buy_analysis_symbols,
        portfolio_holdings=portfolio_holdings,
        benchmark=benchmark,
        intent="buy",
        risk_profile=risk_profile,
        confidence_threshold=confidence_threshold,
        criticality_threshold=criticality_threshold,
        min_critical_confidence=min_critical_confidence,
        history_days=history_days,
        db_path=db_path,
        extra_args=extra_args,
        portfolio_ledger=portfolio_ledger,
    )
    return f"{hold_command} ; {buy_command}"


def parse_time(cron_time: str) -> tuple[int, int]:
    return _parse_time(cron_time)


def install_cron_line(cron_line: str, marker: str = MARKET_BOT_CRON_MARKER) -> bool:
    current = _read_crontab()
    if any(marker in entry for entry in current):
        filtered = [entry for entry in current if marker not in entry]
    else:
        filtered = current

    filtered.append(cron_line)

    payload = "\n".join([line for line in filtered if line.strip()])
    payload = f"{payload}\n" if payload else ""
    subprocess.run(["crontab", "-"], input=payload, text=True, check=True)
    return True


def _read_crontab() -> List[str]:
    completed = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if completed.returncode != 0:
        return []
    return [line.rstrip() for line in completed.stdout.splitlines()]


def _parse_time(cron_time: str) -> tuple[int, int]:
    if ":" not in cron_time:
        raise ValueError(f"Invalid time format '{cron_time}'. Use HH:MM.")
    parts = cron_time.split(":", 1)
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid cron time '{cron_time}'. Hour must be 0-23 and minute 0-59.")
    return hour, minute
