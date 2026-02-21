# Contributing & AI Development Guide

> This document serves as the authoritative reference for **human developers**
> and **AI coding assistants** working on this project. Follow it strictly
> when writing code, debugging, or extending functionality.

---

## 1. Project Conventions

### 1.1 Language & Runtime

- **Python 3.11+** (type hints everywhere, `from __future__ import annotations`)
- **Async-first**: all I/O functions are `async def`
- **Virtual env**: `source .venv/bin/activate` before any Python command

### 1.2 Code Style

| Rule | Detail |
|------|--------|
| Formatter | Not enforced yet; prefer `black` defaults (line length 88) |
| Imports | `from __future__ import annotations` at top of every file |
| Type hints | All function signatures typed; use `Optional[]` for nullable |
| Docstrings | Module-level + class-level + public methods (Google style) |
| Comments | Preserve existing comments; do **not** add/remove without explicit request |
| Naming | `snake_case` for functions/vars, `PascalCase` for classes, `UPPER_CASE` for constants |
| Async | Never use `requests` or blocking I/O; always `aiohttp` / `aiosqlite` / `asyncio` |

### 1.3 File Organization

```
src/
├── config/     # Configuration ONLY (Pydantic models, YAML loader)
├── api/        # External communication ONLY (HTTP, WebSocket, rate limit)
├── core/       # Business logic ONLY (monitor, simulator, settlement, portfolio)
├── data/       # Persistence ONLY (database, models, CSV export)
├── notifications/  # Notification ONLY (channels, manager)
├── utils/      # Cross-cutting concerns (logging, metrics)
└── cli/        # CLI entry points ONLY
```

**Rule:** Each directory has a single clear responsibility. If a new feature
doesn't fit cleanly, create a new sub-package rather than bloating an existing one.

### 1.4 Git Conventions

**Branching:**
```
main           ← stable, always deployable
develop        ← integration branch
feat/xxx       ← feature branches
fix/xxx        ← bug fix branches
```

**Commits:** Use [Conventional Commits](https://www.conventionalcommits.org/):
```
feat(monitor): add WebSocket-based trade detection
fix(simulator): handle empty orderbook gracefully
refactor(api): migrate rate limiter to token bucket
test(settlement): add PnL edge case tests
docs: update ARCHITECTURE.md
chore: bump aiohttp to 3.10
```

**Scopes:** `config`, `api`, `monitor`, `simulator`, `settlement`, `db`, `notify`, `cli`, `metrics`, `export`

---

## 2. Development Workflow

### 2.1 Adding a New Feature

```
1. Read docs/ARCHITECTURE.md to understand the module you're touching
2. Check docs/TESTING.md for existing test cases related to your area
3. Write or update tests FIRST (test-driven when possible)
4. Implement the feature
5. Run `pytest tests/ -v` — all tests must pass
6. Run `python main.py check-config` — config must validate
7. Update CHANGELOG.md under [Unreleased]
8. Commit with conventional commit message
```

### 2.2 Fixing a Bug

```
1. Reproduce the bug — find the exact input and expected vs actual output
2. Locate the root cause (prefer minimal upstream fix over downstream workaround)
3. Write a regression test that fails BEFORE the fix
4. Apply the minimal fix
5. Verify: pytest passes, check-config passes
6. Add to CHANGELOG.md under ### Fixed
```

### 2.3 Modifying Configuration

If you add a new config field:

1. Add the field to the appropriate Pydantic model in `src/config/models.py`
2. Add a default value and validation
3. Add the field to `config/config.yaml` with a comment
4. Update `docs/ARCHITECTURE.md` Section 4 (Configuration Reference)
5. If the field is a secret, use `${ENV_VAR}` pattern and add to `.env.example`

### 2.4 Adding a New API Endpoint

1. Add the async method to `src/api/client.py`
2. It **must** call `self._request()` (which enforces rate limiting + read-only check)
3. Document the endpoint in `docs/ARCHITECTURE.md` Section 5
4. Add a mocked test in `tests/test_api_client.py`

### 2.5 Adding a New Notification Channel

1. Create `src/notifications/new_channel.py`
2. Implement `NotificationChannel` ABC: `name` attribute + `async send(message) -> bool`
3. Register in `src/core/app.py` `Application.run()` alongside Telegram/iMessage
4. Add config sub-model to `NotificationsConfig` in `models.py`
5. Add to `config/config.yaml`

---

## 3. AI Coding Assistant Guide

> **This section is specifically for AI assistants (Cascade, Copilot, etc.)
> that will continue development on this project.**

### 3.1 Before You Start

```
ALWAYS do these steps first:
1. Read this file (CONTRIBUTING.md)
2. Read docs/ARCHITECTURE.md for full system understanding
3. Read CHANGELOG.md to understand what's been done and what's planned
4. Run `python main.py check-config` to verify the project is healthy
5. Check the current state of tests: `pytest tests/ -v`
```

### 3.2 Critical Rules

| # | Rule | Reason |
|---|------|--------|
| 1 | **NEVER remove or weaken `READ_ONLY_MODE`** | Safety: prevents real money loss |
| 2 | **NEVER add `create_order` / `cancel_order` logic** | Must remain permanently blocked |
| 3 | **NEVER hardcode API keys, private keys, or secrets** | Use `.env` + `${VAR}` pattern |
| 4 | **NEVER use blocking I/O** (`requests`, `sqlite3`, `time.sleep`) | Use async equivalents |
| 5 | **NEVER delete existing tests** | Add new tests; modify only if spec changed |
| 6 | **ALWAYS update CHANGELOG.md** | Under `[Unreleased]` section |
| 7 | **ALWAYS run `check-config`** after config changes | Validates Pydantic models |
| 8 | **ALWAYS preserve existing comments** | Unless user explicitly asks to change them |
| 9 | **Prefer minimal edits** | Single-line fix over large refactor when possible |
| 10 | **Imports at top of file** | Never import inside a function body |

### 3.3 Understanding the Data Flow

When debugging, trace the data flow:

```
1. API Response (raw JSON from Polymarket)
   ↓
2. TradeMonitor._poll_target() — filters and deduplicates
   ↓
3. TradeMonitor._passes_market_filter() — keyword/duration/asset check
   ↓
4. Application._on_new_trade() callback — dispatches to simulator + notifier
   ↓
5. TradeSimulator._simulate_single() — delay → orderbook → slippage → fee
   ↓
6. Database.insert_sim_trade() — persists SimTrade dataclass
   ↓
7. SettlementEngine.settle_once() — checks resolution → updates PnL
```

### 3.4 Common Debugging Scenarios

**"No trades detected"**
```
1. Check logs for "Seeded N existing trades" — first poll is silent
2. Verify target address is correct: `python main.py check-config`
3. Test API manually:
   curl "https://data-api.polymarket.com/trades?user=0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db&limit=5"
4. If API returns data but monitor filters it:
   - Set logging.level: DEBUG in config.yaml
   - Look for "Filtered out:" log lines
   - Review market_filter keywords/assets
5. If market_filter.enabled: false still shows nothing:
   - Check _seen set — maybe all trades already seeded
   - Restart the application to clear in-memory state
```

**"Orderbook empty / sim_price is None"**
```
1. The token_id (asset field) may be wrong — log the trade dict
2. Test: curl "https://clob.polymarket.com/book?token_id={TOKEN_ID}"
3. Market may have ended — check is_resolved in market_cache
4. Short-lived markets (5-min) may close before delay completes
```

**"Database errors"**
```
1. Check data/trades.db exists and is not locked
2. Multiple processes using same DB → use separate DB files
3. Schema migration needed? Compare SCHEMA_SQL with actual table:
   sqlite3 data/trades.db ".schema sim_trades"
4. To reset: rm data/trades.db && python main.py run
```

**"Notifications not sending"**
```
1. Check notifications.enabled: true in config.yaml
2. Check channel-specific enabled flag (telegram.enabled: true)
3. Check .env has valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
4. Check notification_history table for error_message:
   sqlite3 data/trades.db "SELECT * FROM notification_history WHERE success=0"
5. --dry-run flag disables all notifications
```

**"Rate limited by API"**
```
1. Check logs for "Rate limited, waiting Xs"
2. Increase rate_limit.time_window or decrease max_requests in config.yaml
3. Check concurrent request count — reduce monitoring.max_concurrent
4. If persistent, may need API key with higher tier
```

### 3.5 Adding Schema Migrations

Currently there's no migration framework. When adding columns:

```python
# In database.py, add to SCHEMA_SQL for new installations.
# For existing DBs, add a migration function:

async def migrate_v0_2(db: aiosqlite.Connection):
    """Add new_column to sim_trades."""
    try:
        await db.execute("ALTER TABLE sim_trades ADD COLUMN new_column REAL")
        await db.commit()
    except Exception:
        pass  # Column already exists
```

Call migrations in `Database.connect()` after schema creation.

### 3.6 Testing with Mock Data

```python
# Realistic trade dict from Polymarket API
SAMPLE_TRADE = {
    "proxyWallet": "0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db",
    "side": "BUY",
    "asset": "21742633143463906290569050155826241533067272736897614950488156847949938836455",
    "conditionId": "0xdd22472e552920b8438158ea7238bfadfa4f736aa4cee91a6b86c39ead110917",
    "size": 50,
    "price": 0.55,
    "timestamp": 1708531200,
    "title": "BTC up 5 minutes",
    "slug": "btc-up-5-minutes",
    "eventSlug": "btc-5min-prediction",
    "outcome": "Yes",
    "outcomeIndex": 0,
    "transactionHash": "0xabc123def456...",
}

# Realistic orderbook response
SAMPLE_ORDERBOOK = {
    "asks": [
        {"price": "0.57", "size": "100"},
        {"price": "0.58", "size": "200"},
    ],
    "bids": [
        {"price": "0.53", "size": "150"},
        {"price": "0.52", "size": "300"},
    ],
}
```

---

## 4. Key File Quick Reference

| When you need to... | Edit this file |
|---------------------|---------------|
| Add a config field | `src/config/models.py` + `config/config.yaml` |
| Add an API endpoint | `src/api/client.py` |
| Change trade detection logic | `src/core/monitor.py` |
| Change simulation logic | `src/core/simulator.py` |
| Change PnL calculation | `src/core/settlement.py` |
| Add a DB column | `src/data/database.py` (SCHEMA_SQL) + `src/data/models.py` |
| Add a notification channel | `src/notifications/` + register in `src/core/app.py` |
| Add a CLI command | `src/cli/commands.py` |
| Change logging format | `src/utils/logger.py` |
| Add a new background task | `src/core/app.py` `Application.run()` |
| Add a dependency | `requirements.txt` |
| Document a change | `CHANGELOG.md` + `docs/ARCHITECTURE.md` |

---

## 5. Dependency Management

### Current dependencies and their purpose:

| Package | Purpose | Can replace with |
|---------|---------|-----------------|
| `pydantic` | Config validation | dataclass + manual validation |
| `pyyaml` | YAML config parsing | `tomllib` (TOML) |
| `python-dotenv` | .env loading | manual `os.getenv` |
| `aiohttp` | Async HTTP client | `httpx` (async mode) |
| `aiofiles` | Async file I/O | `asyncio` built-in (3.12+) |
| `websockets` | WebSocket client | `aiohttp.ws` |
| `aiosqlite` | Async SQLite | `sqlalchemy[async]` |
| `loguru` | Structured logging | `structlog` |
| `click` | CLI framework | `typer`, `argparse` |
| `python-telegram-bot` | Telegram API | `aiogram` |
| `pandas` | Data analysis | Not required for core; used in export |
| `aiolimiter` | Rate limiting | Custom `TokenBucketRateLimiter` (already built) |

### Adding a dependency

1. Add to `requirements.txt` with minimum version: `package>=X.Y.Z`
2. Run `pip install -r requirements.txt`
3. Document why in this table
4. Update `Dockerfile` if system dependencies are needed

---

## 6. Error Handling Policy

| Severity | Action | Example |
|----------|--------|---------|
| **Fatal** | Log + raise + exit | Config validation failure |
| **Error** | Log + continue loop | Single API request failure |
| **Warning** | Log only | Cache miss, notification send failure |
| **Debug** | Log only | Normal flow tracing |

```python
# Pattern: never crash the main loop
async def poll_loop(self):
    while True:
        try:
            await self.poll_once()
        except Exception:
            logger.exception("Unhandled error in poll cycle")
        await asyncio.sleep(interval)
```

---

## 7. Security Checklist

Before any PR or deployment:

- [ ] `FORCE_READ_ONLY=true` in `.env`
- [ ] `system.read_only_mode: true` in `config.yaml`
- [ ] No `create_order` / `cancel_order` implementation exists
- [ ] No private keys in committed files
- [ ] `.env` is in `.gitignore`
- [ ] No hardcoded secrets in source code
- [ ] API key scope is read-only
