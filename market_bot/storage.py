from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any, Iterable, List, Optional, Sequence
from urllib.parse import urlencode, parse_qsl, urlparse, urlunparse

from market_bot.models import AnalysisDecision
from market_bot.analysis.portfolio import PortfolioHolding


def connect_db(path: str) -> Any:
    if _is_postgres_url(path):
        return _connect_postgres(path)

    db_path = path
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    _init_schema(conn)
    return conn


def _is_postgres_conn(conn: Any) -> bool:
    module = conn.__class__.__module__
    return module.startswith("psycopg")


def _is_postgres_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"postgresql", "postgres"}


def _ensure_sslmode(url: str) -> str:
    parsed = urlparse(url)
    query = {}
    sslmode_value = None
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() == "sslmode":
            sslmode_value = value
            continue
        query[key] = value
    query["sslmode"] = sslmode_value if sslmode_value is not None else "require"
    normalized = parsed._replace(query=urlencode(query, doseq=True))
    return urlunparse(normalized)


def _connect_postgres(url: str) -> Any:
    try:
        import psycopg
    except Exception as exc:
        raise RuntimeError(
            "Postgres support requires psycopg. Install it with `pip install psycopg[binary]`."
        ) from exc

    normalized_url = _ensure_sslmode(url)
    conn = psycopg.connect(normalized_url)
    _init_schema(conn)
    return conn


def _init_schema(conn: Any) -> None:
    if _is_postgres_conn(conn):
        _init_schema_postgres(conn)
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            benchmark TEXT NOT NULL,
            analysis_intent TEXT NOT NULL DEFAULT 'hold',
            risk_profile TEXT NOT NULL DEFAULT 'moderate',
            confidence_threshold REAL NOT NULL DEFAULT 0.0,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            portfolio_snapshot_json TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            analysis_intent TEXT NOT NULL DEFAULT 'hold',
            confidence_score REAL NOT NULL DEFAULT 0.0,
            close_start REAL NOT NULL,
            close_end REAL NOT NULL,
            pct_change_week REAL NOT NULL,
            trend_score REAL NOT NULL,
            momentum_score REAL NOT NULL,
            relative_strength_score REAL NOT NULL,
            volume_score REAL NOT NULL,
            volatility_score REAL NOT NULL,
            composite_score REAL NOT NULL,
            down_probability REAL NOT NULL,
            recommendation TEXT NOT NULL,
            rationale TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs (id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            symbol TEXT PRIMARY KEY,
            shares REAL NOT NULL DEFAULT 0.0,
            cost_basis REAL,
            updated_at TEXT NOT NULL
        );
        """
    )
    if not _column_exists(conn, "runs", "analysis_intent"):
        conn.execute("ALTER TABLE runs ADD COLUMN analysis_intent TEXT NOT NULL DEFAULT 'hold';")
    if not _column_exists(conn, "runs", "risk_profile"):
        conn.execute("ALTER TABLE runs ADD COLUMN risk_profile TEXT NOT NULL DEFAULT 'moderate';")
    if not _column_exists(conn, "runs", "confidence_threshold"):
        conn.execute("ALTER TABLE runs ADD COLUMN confidence_threshold REAL NOT NULL DEFAULT 0.0;")
    if not _column_exists(conn, "runs", "portfolio_snapshot_json"):
        conn.execute("ALTER TABLE runs ADD COLUMN portfolio_snapshot_json TEXT;")
    if not _column_exists(conn, "analyses", "analysis_intent"):
        conn.execute("ALTER TABLE analyses ADD COLUMN analysis_intent TEXT NOT NULL DEFAULT 'hold';")
    if not _column_exists(conn, "analyses", "confidence_score"):
        conn.execute("ALTER TABLE analyses ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.0;")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_run
            ON analyses (run_id);
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_symbol_run
            ON analyses (run_id, symbol);
        """
    )
    conn.commit()


def _init_schema_postgres(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            benchmark TEXT NOT NULL,
            analysis_intent TEXT NOT NULL DEFAULT 'hold',
            risk_profile TEXT NOT NULL DEFAULT 'moderate',
            confidence_threshold REAL NOT NULL DEFAULT 0.0,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            portfolio_snapshot_json TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            analysis_intent TEXT NOT NULL DEFAULT 'hold',
            confidence_score REAL NOT NULL DEFAULT 0.0,
            close_start REAL NOT NULL,
            close_end REAL NOT NULL,
            pct_change_week REAL NOT NULL,
            trend_score REAL NOT NULL,
            momentum_score REAL NOT NULL,
            relative_strength_score REAL NOT NULL,
            volume_score REAL NOT NULL,
            volatility_score REAL NOT NULL,
            composite_score REAL NOT NULL,
            down_probability REAL NOT NULL,
            recommendation TEXT NOT NULL,
            rationale TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CONSTRAINT analyses_run_id_fkey FOREIGN KEY (run_id) REFERENCES runs (id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            symbol TEXT PRIMARY KEY,
            shares NUMERIC NOT NULL DEFAULT 0.0,
            cost_basis NUMERIC,
            updated_at TEXT NOT NULL
        );
        """
    )
    if not _column_exists(conn, "runs", "analysis_intent"):
        conn.execute("ALTER TABLE runs ADD COLUMN analysis_intent TEXT NOT NULL DEFAULT 'hold';")
    if not _column_exists(conn, "runs", "risk_profile"):
        conn.execute("ALTER TABLE runs ADD COLUMN risk_profile TEXT NOT NULL DEFAULT 'moderate';")
    if not _column_exists(conn, "runs", "confidence_threshold"):
        conn.execute("ALTER TABLE runs ADD COLUMN confidence_threshold REAL NOT NULL DEFAULT 0.0;")
    if not _column_exists(conn, "runs", "portfolio_snapshot_json"):
        conn.execute("ALTER TABLE runs ADD COLUMN portfolio_snapshot_json TEXT;")
    if not _column_exists(conn, "analyses", "analysis_intent"):
        conn.execute("ALTER TABLE analyses ADD COLUMN analysis_intent TEXT NOT NULL DEFAULT 'hold';")
    if not _column_exists(conn, "analyses", "confidence_score"):
        conn.execute("ALTER TABLE analyses ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.0;")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_run
            ON analyses (run_id);
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_symbol_run
            ON analyses (run_id, symbol);
        """
    )
    conn.commit()


def save_portfolio_holdings(
    conn: Any,
    holdings: Sequence[PortfolioHolding],
) -> None:
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    rows: List[tuple[str, float, Optional[float], str]] = []

    for holding in holdings:
        shares = float(holding.shares) if holding.shares is not None else 0.0
        rows.append((holding.symbol.upper(), shares, holding.cost_basis, timestamp))

    if _is_postgres_conn(conn):
        conn.executemany(
            """
            INSERT INTO portfolio_holdings(symbol, shares, cost_basis, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
              shares = EXCLUDED.shares,
              cost_basis = EXCLUDED.cost_basis,
              updated_at = EXCLUDED.updated_at
            """,
            rows,
        )
        conn.commit()
        return

    conn.executemany(
        """
        INSERT INTO portfolio_holdings(symbol, shares, cost_basis, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
          shares = excluded.shares,
          cost_basis = excluded.cost_basis,
          updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def load_portfolio_holdings(conn: Any) -> list[PortfolioHolding]:
    cursor = conn.execute(
        """
        SELECT symbol, shares, cost_basis
        FROM portfolio_holdings
        ORDER BY symbol
        """
    )
    holdings: list[PortfolioHolding] = []
    for symbol, shares, cost_basis in cursor.fetchall():
        if shares is None:
            continue
        holdings.append(
            PortfolioHolding(
                symbol=symbol.upper(),
                shares=float(shares),
                cost_basis=float(cost_basis) if cost_basis is not None else None,
            )
        )
    return holdings


def save_run(
    conn: Any,
    run_id: str,
    benchmark: str,
    analysis_intent: str,
    risk_profile: str,
    confidence_threshold: float,
    symbols: Iterable[str],
    week_start: str,
    week_end: str,
    portfolio_snapshot_json: str | dict | None = None,
) -> None:
    if isinstance(portfolio_snapshot_json, dict):
        portfolio_snapshot_payload = json.dumps(portfolio_snapshot_json)
    else:
        portfolio_snapshot_payload = portfolio_snapshot_json

    if _is_postgres_conn(conn):
        conn.execute(
            """
            INSERT INTO runs
            (id, created_at, benchmark, analysis_intent, risk_profile, confidence_threshold, week_start, week_end, symbols_json, portfolio_snapshot_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              created_at = EXCLUDED.created_at,
              benchmark = EXCLUDED.benchmark,
              analysis_intent = EXCLUDED.analysis_intent,
              risk_profile = EXCLUDED.risk_profile,
              confidence_threshold = EXCLUDED.confidence_threshold,
              week_start = EXCLUDED.week_start,
              week_end = EXCLUDED.week_end,
              symbols_json = EXCLUDED.symbols_json,
              portfolio_snapshot_json = EXCLUDED.portfolio_snapshot_json
            """,
            (
                run_id,
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
                benchmark.upper(),
                analysis_intent,
                risk_profile,
                confidence_threshold,
                week_start,
                week_end,
                json.dumps(sorted(list(set(s.upper() for s in symbols)))),
                portfolio_snapshot_payload,
            ),
        )
        conn.commit()
        return

    conn.execute(
        """
        INSERT OR REPLACE INTO runs
        (id, created_at, benchmark, analysis_intent, risk_profile, confidence_threshold, week_start, week_end, symbols_json, portfolio_snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
            benchmark.upper(),
            analysis_intent,
            risk_profile,
            confidence_threshold,
            week_start,
            week_end,
            json.dumps(sorted(list(set(s.upper() for s in symbols)))),
            portfolio_snapshot_payload,
        ),
    )
    conn.commit()


def save_analyses(
    conn: Any,
    run_id: str,
    analyses: List[AnalysisDecision],
    payload_overrides: Optional[List[dict]] = None,
) -> None:
    if payload_overrides is not None and len(payload_overrides) != len(analyses):
        raise ValueError("payload_overrides must match analyses length.")

    rows = []
    for index, decision in enumerate(analyses):
        payload = asdict(decision)
        if payload_overrides is not None:
            payload = payload_overrides[index]
        if _is_postgres_conn(conn):
            rows.append(
                (
                    run_id,
                    decision.symbol,
                    decision.analysis_intent,
                    decision.confidence,
                    decision.close_start,
                    decision.close_end,
                    decision.pct_change_week,
                    decision.trend_score,
                    decision.momentum_score,
                    decision.relative_strength_score,
                    decision.volume_score,
                    decision.volatility_score,
                    decision.composite_score,
                    decision.down_probability,
                    decision.recommendation,
                    decision.rationale,
                    json.dumps(payload, default=str),
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                )
            )
        else:
            rows.append(
                (
                    run_id,
                    decision.symbol,
                    decision.analysis_intent,
                    decision.confidence,
                    decision.close_start,
                    decision.close_end,
                    decision.pct_change_week,
                    decision.trend_score,
                    decision.momentum_score,
                    decision.relative_strength_score,
                    decision.volume_score,
                    decision.volatility_score,
                    decision.composite_score,
                    decision.down_probability,
                    decision.recommendation,
                    decision.rationale,
                    json.dumps(payload, default=str),
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                )
            )

    if _is_postgres_conn(conn):
        conn.executemany(
            """
            INSERT INTO analyses
            (run_id, symbol, analysis_intent, confidence_score, close_start, close_end, pct_change_week,
             trend_score, momentum_score, relative_strength_score,
             volume_score, volatility_score, composite_score, down_probability,
             recommendation, rationale, payload_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, symbol) DO UPDATE SET
                analysis_intent = EXCLUDED.analysis_intent,
                confidence_score = EXCLUDED.confidence_score,
                close_start = EXCLUDED.close_start,
                close_end = EXCLUDED.close_end,
                pct_change_week = EXCLUDED.pct_change_week,
                trend_score = EXCLUDED.trend_score,
                momentum_score = EXCLUDED.momentum_score,
                relative_strength_score = EXCLUDED.relative_strength_score,
                volume_score = EXCLUDED.volume_score,
                volatility_score = EXCLUDED.volatility_score,
                composite_score = EXCLUDED.composite_score,
                down_probability = EXCLUDED.down_probability,
                recommendation = EXCLUDED.recommendation,
                rationale = EXCLUDED.rationale,
                payload_json = EXCLUDED.payload_json,
                created_at = EXCLUDED.created_at
            """,
            rows,
        )
        conn.commit()
        return

    conn.executemany(
        """
        INSERT OR REPLACE INTO analyses
        (run_id, symbol, analysis_intent, confidence_score, close_start, close_end, pct_change_week,
         trend_score, momentum_score, relative_strength_score,
         volume_score, volatility_score, composite_score, down_probability,
         recommendation, rationale, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def list_recent_runs(conn: Any, limit: int = 10):
    cursor = conn.execute(
        """
        SELECT id, created_at, benchmark, analysis_intent, risk_profile, confidence_threshold, week_start, week_end, symbols_json
        FROM runs
        ORDER BY created_at DESC
        LIMIT %s
        """
        if _is_postgres_conn(conn)
        else """
        SELECT id, created_at, benchmark, analysis_intent, risk_profile, confidence_threshold, week_start, week_end, symbols_json
        FROM runs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()


def _column_exists(conn: Any, table: str, column: str) -> bool:
    if _is_postgres_conn(conn):
        cursor = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s
              AND column_name = %s
              AND table_schema = 'public'
            LIMIT 1
            """,
            (table, column),
        )
        return bool(cursor.fetchone())

    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())
