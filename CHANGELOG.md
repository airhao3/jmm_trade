# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- WebSocket mode for near-instant trade detection (<100ms vs 1s poll)
- Database auto-backup to file
- Daily summary notification (scheduled report)
- Web dashboard (FastAPI + HTMX or Streamlit)
- Multi-delay parallel simulation (run delays concurrently instead of sequentially)
- Historical back-testing mode (replay past trades from DB)
- ML-based slippage prediction (skip known-bad trades)
- Pre-fetch orderbooks for active markets (instant price snapshot)

---

## [0.2.0] - 2026-02-21

### Added
- **Startup connectivity test** — verifies Data API, CLOB orderbook, and Gamma market
  endpoints on boot with latency display (`src/core/app.py:_startup_connectivity_test`)
- **Metrics integration** — `polls_completed` and `avg_api_latency_ms` now correctly
  populated in metrics snapshots
  - API client latency callback wired to `MetricsCollector`
  - Monitor poll count synced via `_poll_loop_with_metrics`
- **`delay=0` support** — instant orderbook snapshot (no wait), enabled in config
  validation (`d >= 0` instead of `d > 0`)
- **`Database.get_all_trades()`** — returns all sim trades (OPEN, FAILED, SETTLED),
  used by export and CLI stats
- **E2E test suite** — `tests/test_e2e_local.py` (19 checks) and
  `tests/test_e2e_pipeline.py` (18 checks) exercise the full pipeline with real API
- **VPS deployment assessment** — `docs/DEPLOYMENT_ASSESSMENT.md` with live test data,
  latency analysis, VPS specs, and optimization roadmap

### Changed
- **Config optimized for VPS** — `poll_interval: 1` (was 3), `delays: [0, 1, 3]`
  (was [1, 3]), `max_slippage_pct: 50.0` (was 5.0), `max_duration_minutes: 60` (was 15)
- **Gamma API endpoints** — switched from path params (`/markets/{id}`) to query params
  (`/markets?condition_id=`) which is the correct Gamma API format
- **Stats display** — `avg_slippage` now includes ALL trades (not just successful),
  added `Success Rate` and `Simulated (all)` fields to CLI output
- **`monitor.poll_once()`** returns `(count, latency)` tuple instead of just `count`
- **Poll heartbeat** — logs every 20th poll with current and average latency

### Fixed
- **Gamma API 422 errors** — was using path param `/markets/{conditionId}`, now uses
  query param `?condition_id=` (correct format)
- **`outcomePrices` parsing crash** — Gamma API returns JSON string `'["0","1"]'` not
  a Python list; added `json.loads()` fallback in settlement
- **404 retry waste** — expired market orderbooks return 404, was retrying 3x with
  exponential backoff (14s wasted per token); now 4xx errors (except 429) return
  immediately with `None`
- **API `None` safety** — all API methods return safe defaults (`[]` or `{}`) when
  server returns 4xx, preventing `AttributeError` downstream
- **`polls_completed` always 0** — monitor poll count was not synced to metrics collector
- **`avg_api_latency_ms` always 0** — API client had no callback to report latency
- **`datetime.utcnow()` deprecation** — replaced with `datetime.now(timezone.utc)`
  in database cache TTL check
- **CSV export empty** — was using `get_open_trades()` which excludes FAILED trades;
  now uses `get_all_trades()`

### Tests
- 63 unit/integration tests passing (pytest + asyncio)
- All test fixtures updated for new config (delays [0,1,3], poll_interval 1,
  max_slippage 50, max_duration 60)
- `test_zero_delay_accepted` replaces `test_zero_delay_rejected`

---

## [0.1.0] - 2026-02-21

### Added

**Project scaffolding**
- Python 3.11 project with `venv`, `requirements.txt`, `.gitignore`
- Unified `config/config.yaml` with all settings in one file
- `.env.example` template for secrets
- `README.md` with quick start guide

**Configuration system (`src/config/`)**
- `AppConfig` Pydantic root model with 10 validated sub-sections
- `loader.py`: YAML + `.env` loading with env-var `${VAR}` resolution
- Field validators: address regex, delay sorting, duration range, log level enum
- `SystemConfig` enforces `FORCE_READ_ONLY` env override

**Async API client (`src/api/`)**
- `PolymarketClient`: fully async `aiohttp` client with context manager
- Endpoints: `get_trades`, `get_activity`, `get_positions`, `get_orderbook`,
  `get_price`, `get_midpoint`, `get_spread`, `get_market`, `get_event`,
  `batch_get_orderbooks`, `search_markets`
- `create_order()` and `cancel_order()` permanently blocked (raise RuntimeError)
- Retry logic: 3 attempts, exponential backoff, 429 rate-limit handling
- `TokenBucketRateLimiter`: burst-capable async rate limiter
- `PolymarketWebSocket`: auto-reconnect, heartbeat, dynamic subscribe/unsubscribe

**Core engine (`src/core/`)**
- `TradeMonitor`: poll-based trade discovery with market filtering
  - Regex asset matching, keyword include/exclude, duration range check
  - First-poll seeding (no false positives on startup)
  - Callback-based dispatch pattern
- `TradeSimulator`: independent shadow execution engine
  - Configurable delay (default 1s, 3s)
  - Orderbook snapshot at delay point (best ask for BUY, best bid for SELL)
  - Slippage calculation and limit enforcement
  - Fee computation (default 1.5%)
  - Deduplication via `trade_exists()` check
- `SettlementEngine`: periodic market resolution checker
  - Groups open trades by condition_id to minimise API calls
  - PnL calculation for BUY and SELL sides
  - Market cache integration with TTL
- `Portfolio`: read-only stats aggregator
- `Application`: top-level orchestrator wiring all subsystems
  - 6 concurrent async tasks via `asyncio.create_task`
  - Graceful shutdown with task cancellation

**Data layer (`src/data/`)**
- `Database`: async SQLite via `aiosqlite`
  - 5 tables: `monitored_accounts`, `sim_trades` (30 cols), `market_cache`,
    `metrics`, `notification_history`
  - 6 indexes for query performance
  - Cache TTL: `get_cached_market()` auto-expires stale entries
  - `get_statistics()`: aggregated stats (total PnL, win rate, avg slippage)
  - `get_pnl_summary()`: grouped by target + delay
- `SimTrade`, `MarketInfo`, `MetricRecord`, `AccountStats` dataclasses
- `export_trades_to_csv()`, `export_pnl_summary_to_csv()`

**Notifications (`src/notifications/`)**
- `NotificationManager`: async queue with batched aggregation
  - Configurable aggregation interval (default 30s)
  - Exponential backoff retry (default 3 retries, [1, 2, 4]s)
  - All attempts logged to `notification_history` table
- `TelegramNotifier`: `python-telegram-bot` async, 4000-char truncation
- `IMessageNotifier`: macOS `osascript` AppleScript, 2000-char truncation
- 6 event types: `NEW_TRADE`, `SIM_EXECUTED`, `SIM_FAILED`,
  `MARKET_SETTLED`, `DAILY_SUMMARY`, `ERROR_ALERT`

**Logging & metrics (`src/utils/`)**
- `setup_logger()`: loguru with console (coloured) + file (JSON or text)
  - Rotation by size, retention by age
  - Separate `metrics.log` sink filtered by `extra.metrics` flag
- `MetricsCollector`: ring-buffer metrics with periodic JSON snapshots
  - Tracks: API latency, slippage, PnL, notification success rate
  - Writes to both `metrics.log` and `metrics` DB table

**CLI (`src/cli/`)**
- Click-based command group: `run`, `export`, `stats`, `check-config`
- `run`: `--mode poll|ws`, `--dry-run` flag
- `export`: `--output` custom filename
- `stats`: `--target` filter by address
- `check-config`: validate without starting

**Deployment**
- `Dockerfile`: Python 3.11-slim, health check, `FORCE_READ_ONLY=true`
- `docker-compose.yml`: volume mounts for data/logs/config, JSON logging

**Safety**
- Triple-layer read-only: config flag + env var + code constant
- `_assert_read_only()` called before every HTTP request
- `create_order()` / `cancel_order()` unconditionally raise RuntimeError
- No private keys used for transaction signing

### Known Limitations
- WebSocket mode (`--mode ws`) is scaffolded but not fully implemented
  for trade detection (only orderbook/price updates)
- Market filter regex may not match all Polymarket naming conventions;
  needs tuning with real data
- Database backup logic is configured but not yet implemented
- 63 automated tests covering config, monitor, simulator, settlement, database, rate limiter
- `batch_get_orderbooks` runs sequentially per-token (gather planned)
- iMessage notification requires macOS with Messages.app configured

---

## Version History Template

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New feature description

### Changed
- Modification to existing functionality

### Fixed
- Bug fix description

### Deprecated
- Feature that will be removed in future

### Removed
- Feature that was removed

### Security
- Security-related change
```

---

## Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(monitor): add WebSocket-based trade detection
fix(simulator): handle empty orderbook without crash
refactor(api): switch from requests to aiohttp
docs: update ARCHITECTURE.md with new module
test(simulator): add slippage calculation tests
chore: update dependencies
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `style`
Scopes: `monitor`, `simulator`, `settlement`, `api`, `db`, `notify`, `config`, `cli`
