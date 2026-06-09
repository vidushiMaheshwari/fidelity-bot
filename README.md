# Market Analytics Core (CLI First)

This repo now contains an analytical module that:
- fetches Yahoo Finance history,
- computes technical features,
- generates a directional decision per symbol,
- stores each run in SQLite,
- provides a CLI for manual weekly checks.

## Install dependencies

```bash
pip install -r requirements.txt
```

## Run a daily (or any time) check

```bash
python3 -m market_bot.cli analyze --symbols AAPL,MSFT,NVDA --benchmark SPY
```

```bash
python3 -m market_bot.cli analyze --symbols NVDA --intent buy
python3 -m market_bot.cli analyze --symbols AAPL --intent hold
```

Options:
- `--watchlist path.txt` — one symbol per line.
- `--intent [hold|buy]` — `hold` for position management, `buy` for pre-entry checks.
- `--risk-profile [conservative|moderate|aggressive]` — adjusts recommendation strictness.
- `--confidence-threshold 0.00-1.00` — suppresses low-confidence calls.
- `--history-days` — lookback used for indicators.
- `--no-save` — calculate without writing to DB.
- `--output-json` — machine-friendly output.
- `--criticality-threshold 0.00-1.00` — hide non-critical symbols from ranked output.
- `--min-critical-confidence 0.00-1.00` — confidence gate for critical buy/sell labels.
- `--no-rank` — keep original analysis order instead of criticality sorting.
- `--portfolio-holdings path.csv|path.json` — optional per-symbol shares/average cost for portfolio weighting. Defaults to `<user-root>/<user-id>/portfolio_holdings.csv` when omitted.
- `--portfolio-ledger path.csv|path.json` — optional transaction ledger to rebuild holdings and realized P&L from buys/sells. Defaults to `<user-root>/<user-id>/portfolio_ledger.csv` when omitted.
- `--use-portfolio-db` — load/merge holdings from the configured DB and include them in portfolio analysis.
- `--persist-portfolio-holdings` — write holdings from `--portfolio-holdings` into the configured DB for future runs.
- `--user-id` — logical user namespace (single-user default: `me`).
- `--user-root` — root directory for per-user state files (default: `data/user`).
- `--db-path` — storage backend path. Defaults to `<user-root>/<user-id>/analytics.db` and also accepts a PostgreSQL URL (Supabase-style `postgresql://...`) for cloud state.

Example calibrated runs:

```bash
python3 -m market_bot.cli analyze --symbols AAPL,MSFT --intent buy --risk-profile conservative --confidence-threshold 0.42
python3 -m market_bot.cli analyze --symbols AAPL --intent hold --risk-profile aggressive --confidence-threshold 0.25
```

### Portfolio snapshot output

With `--output-json`, each run now includes `portfolio_snapshot`:
- aggregate downside/composite exposure
- concentration metrics (`hhi`, `effective_breadth`, `max_weight`, `top3_weight`)
- weighted signal pressure (`buy_pressure`, `sell_pressure`)
- notional and unrealized P&L metrics from `--portfolio-holdings`.
- realized P&L metrics from `--portfolio-ledger` (if present).

### Holdings + ledger examples

CSV:
```csv
symbol,quantity,avg_cost
AAPL,10,180.0
MSFT,8,420.0
```

```csv
# default data/user/me/portfolio_ledger.csv
trade_date,action,symbol,shares,price,fee,currency,notes
2026-05-30,BUY,AAPL,10,185.00,0,USD,"first buy"
2026-06-01,SELL,AAPL,2,190.00,0,USD,"partial trim"
```

JSON:
```json
[{"symbol":"AAPL","shares":10,"avg_cost":180.0}]
```

### Portfolio storage (database-backed holdings)

Once you pass `--persist-portfolio-holdings`, your holdings from
`--portfolio-holdings` are stored in the analytics database referenced by
`--db-path` (defaults to `<user-root>/<user-id>/analytics.db`; also accepts a
PostgreSQL URL), allowing:

- `--use-portfolio-db` to run portfolio-only updates without re-importing files
- easier cron usage for portfolio snapshots that must survive script restarts
- default files are stored under `data/user/me/` for the current single-user setup

If both file and DB exist, file values are merged with DB values, and file values win.

## View saved runs

```bash
python3 -m market_bot.cli history --limit 5
```

By default the local SQLite DB is `data/user/<user-id>/analytics.db` (for default `user-id=me`, that is `data/user/me/analytics.db`).
Set `SUPABASE_DATABASE_URL` in your shell or `--db-path` to switch to cloud Postgres.

## What is stored

Each run stores:
- timestamp + selected symbols + window,
- per-symbol close levels,
- component scores (trend/momentum/relative/volume/volatility),
- composite score,
- down-probability estimate,
- recommendation:
  - hold mode: `trim_or_exit`, `monitor_closely`, `reduce_or_pause`, `hold_watch`, `hold_with_lower_risk`,
  - buy mode: `buy_now`, `buy_on_pullback`, `watch_for_entry_signal`, `watch`, `do_not_buy`,
- rationale and payload JSON for future extension.
- run metadata including `analysis_intent`, `risk_profile`, `confidence_threshold`.
## Daily schedule trigger

Use the new `schedule` command to print or install a daily automation rule.

```bash
python3 -m market_bot.cli schedule \
  --symbols AAPL,MSFT,NVDA \
  --intent hold \
  --risk-profile conservative \
  --time 09:15 \
  --criticality-threshold 0.10 \
  --min-critical-confidence 0.60
```

### Portfolio-style schedule (owned + watchlist)

You can run two analyses each day in one cron schedule:
1. held symbols with `intent=hold`
2. owned + watchlist symbols with `intent=buy`

```bash
python3 -m market_bot.cli schedule \
  --owned-symbols AAPL,MSFT \
  --portfolio-holdings /path/to/portfolio_holdings.csv \
  --watchlist /path/to/watchlist.txt \
  --risk-profile moderate \
  --time 09:30 \
  --history-days 90 \
  --install
```

In this example:
- `AAPL,MSFT` are treated as your existing positions (sell/hold check)
- `/path/to/watchlist.txt` is combined with owned positions for the buy check
- `--owned-symbols` / `--owned-watchlist` define the held set (portfolio scan)
- `--buy-watchlist` or `--watchlist` defines additional buy-scan candidates

Run once immediately with `--run-now` using the same arguments.

To install the same command into the current user `crontab`, append `--install`.

```bash
python3 -m market_bot.cli schedule --symbols AAPL,MSFT --intent buy --time 09:00 --install
```

Add `--run-now` to execute one pass immediately with the same settings before scheduling.

## GitHub Actions daily schedule

The repository includes `.github/workflows/market-bot-schedule.yml`, which runs:
1. the portfolio-style two-pass scan (held + buy) by default,
2. saves JSON run output as an artifact,
3. uploads the current local `analytics.db` for inspection when `ANALYTICS_DB_PATH` is file-based.
4. keeps user state files (`portfolio_holdings.csv`, `portfolio_ledger.csv`, local `analytics.db`) out of source control.

### Required repo configuration

- Enable **Actions** on the repository (default for GitHub repositories).
- Add these repository settings (Settings → Secrets and variables → Actions):
  - `SUPABASE_DATABASE_URL` (required for remote DB, Secrets)
    - `postgresql://postgres:YOUR_PASSWORD@db.zxwkhxsiwxmkhxbivdud.supabase.co:5432/postgres`
    - use the password-embedded Supabase connection URL from your dashboard.
- Also set optional run-time variables:
  - `MARKET_BOT_OWNED_SYMBOLS` (optional, e.g. `AAPL,MSFT`),
  - `MARKET_BOT_BUY_SYMBOLS` (optional, e.g. `NVDA,TSLA`),
  - `MARKET_BOT_OWNED_WATCHLIST_PATH` (optional, e.g. `data/user/me/owned_watchlist.txt`),
  - `MARKET_BOT_BUY_WATCHLIST_PATH` (optional, e.g. `data/user/me/watchlist.txt`),
  - `MARKET_BOT_RISK_PROFILE` (optional),
  - `MARKET_BOT_HISTORY_DAYS` (optional),
  - `MARKET_BOT_CONFIDENCE_THRESHOLD` (optional),
  - `MARKET_BOT_CRITICALITY_THRESHOLD` (optional),
  - `MARKET_BOT_MIN_CRITICAL_CONFIDENCE` (optional),
  - `MARKET_BOT_PORTFOLIO_HOLDINGS_PATH` (optional, default `data/user/me/portfolio_holdings.csv`),
  - `MARKET_BOT_PORTFOLIO_LEDGER_PATH` (optional, default `data/user/me/portfolio_ledger.csv`),
  - `MARKET_BOT_ANALYTICS_DB_URL` (optional alternative to secret; set to a Postgres URL),
  - `MARKET_BOT_ANALYTICS_DB_PATH` (optional file fallback, default `data/user/me/analytics.db`).

These file-based state paths should remain local and are ignored from commits by `.gitignore`:
- `data/user/*/portfolio_holdings.csv`
- `data/user/*/portfolio_ledger.csv`
- `data/user/*/analytics.db`

When running against Supabase, the app reads/writes run records and holdings directly to Postgres and does not rely on committed local DB files.

### Manual run

Use **Run workflow** to dispatch a one-off test with optional overrides:
- `owned_symbols`, `owned_watchlist_path`, `buy_symbols`, `buy_watchlist_path`.

The workflow runs as a local command equivalent to:

```bash
python3 -m market_bot.cli schedule --run-now --output-json ...
```

Examples:

```bash
# file-backed mode (current defaults)
python3 -m market_bot.cli analyze --symbols AAPL,MSFT --db-path data/user/me/analytics.db

# Supabase mode
python3 -m market_bot.cli analyze \
  --symbols AAPL,MSFT \
  --db-path postgresql://postgres:YOUR_PASSWORD@db.zxwkhxsiwxmkhxbivdud.supabase.co:5432/postgres
```

### Permissions note

For this repository-local workflow, I only need write access to:
- push `.github/workflows/market-bot-schedule.yml`,
- edit repository-level Action variables (if desired),
- optionally update your user files (`data/user/me/...`) when you change lists.
 
Runtime job permissions stay read-only by default (`contents: read`), since all secrets are optional and no commit is performed during execution.

## Non-core operations layer

Operational helpers are kept out of the core analysis path in `market_bot/ops/`:
- `market_bot/ops/ranking.py` for urgency scoring and criticality labels.
- `market_bot/ops/scheduler.py` for daily trigger/cron generation.

## Analysis module layout

- `market_bot/analysis/stocks/` contains per-symbol analytics and indicator derivation.
  - `market_bot/analysis/stocks/scorer.py` (`analyze_symbol`, `confidence_score`)
  - `market_bot/analysis/stocks/indicators.py` (`add_derived_features`)
- `market_bot/analysis/portfolio/` contains portfolio-level analysis built on
  `AnalysisDecision` outputs from stock runs.
  - `market_bot/analysis/portfolio/scorer.py` (`analyze_portfolio`)

Example programmatic portfolio call:
```python
from market_bot.analysis.portfolio import PortfolioHolding, analyze_portfolio

holdings = [
    PortfolioHolding(symbol="AAPL", shares=10, cost_basis=180.0),
    PortfolioHolding(symbol="MSFT", shares=8, cost_basis=420.0),
]
# decisions = [AnalysisDecision(...), ...] from analyze_symbol
portfolio = analyze_portfolio(decisions=decisions, holdings=holdings)
print(portfolio.as_payload())
```

Portfolio metrics returned include:
- weighted downside probability
- concentration risk (`hhi`, `effective_breadth`, max/top3 concentration)
- signal pressure (`buy_pressure`, `sell_pressure`)
- unrealized P&L (when shares + cost basis are provided)
- portfolio recommendation label/score

## Ranking output

The analyzer now ranks symbols by urgency:

- `critical_sell` — strongest sell-side recommendations (`trim_or_exit`, `reduce_or_pause` with high confidence).
- `critical_buy` — strongest buy-side recommendations (`buy_now`).
- `important` — medium-confidence signals worth monitoring closely.
- `watch` — lower urgency.

The ranked payload includes:
- `criticality`: category label
- `criticality_score`: confidence-weighted urgency score
- `rank`: order index in current run
