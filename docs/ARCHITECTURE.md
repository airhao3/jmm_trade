# Architecture & Technical Documentation

> Polymarket Copy Trading Simulator v0.1.0

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        main.py (CLI)                            │
│                    click commands: run / export / stats          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Application (app.py)                          │
│              Orchestrator – wires all subsystems                 │
│                                                                 │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌─────────────┐  │
│  │ Monitor  │→ │ Simulator │→ │ Settlement │  │ Portfolio   │  │
│  │ (detect) │  │ (shadow)  │  │ (resolve)  │  │ (stats)     │  │
│  └────┬─────┘  └─────┬─────┘  └─────┬──────┘  └──────┬──────┘  │
│       │              │              │                │          │
│       ▼              ▼              ▼                ▼          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              PolymarketClient (aiohttp)                 │    │
│  │         TokenBucketRateLimiter  │  WebSocket            │    │
│  └─────────────────────────────────────────────────────────┘    │
│       │              │              │                │          │
│       ▼              ▼              ▼                ▼          │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌─────────────┐  │
│  │ Database │  │  Export   │  │ Notifier   │  │  Metrics    │  │
│  │ (SQLite) │  │  (CSV)   │  │ (Tg/iMsg)  │  │  (loguru)   │  │
│  └──────────┘  └───────────┘  └────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Polymarket API
    │
    ▼
TradeMonitor.poll_once()
    │  GET /trades?user={address}
    │  filter by market keywords/duration
    ▼
TradeSimulator.simulate(target, trade)
    │  await asyncio.sleep(delay)       # 1s, 3s
    │  GET /book?token_id={id}          # orderbook snapshot
    │  compute slippage, fee, cost
    ▼
Database.insert_sim_trade(sim_trade)
    │
    ├──→ NotificationManager.notify()   # queued, batched
    └──→ MetricsCollector.record_*()    # ring buffers
```

---

## 2. Module Reference

### 2.1 Configuration (`src/config/`)

| File | Class | Purpose |
|------|-------|---------|
| `models.py` | `AppConfig` | Root Pydantic model; validates all sections on load |
| `models.py` | `SystemConfig` | `read_only_mode`, env-var override enforcement |
| `models.py` | `MonitoringConfig` | `mode`, `poll_interval`, `max_concurrent` |
| `models.py` | `SimulationConfig` | `delays`, `investment_per_trade`, `fee_rate`, `max_slippage_pct` |
| `models.py` | `MarketFilterConfig` | `assets`, duration range, keyword include/exclude |
| `models.py` | `TargetAccount` | Wallet address (validated regex), nickname, weight |
| `models.py` | `APIConfig` | Base URLs, WebSocket URLs, timeout, `RateLimitConfig` |
| `models.py` | `NotificationsConfig` | `aggregation_interval`, `retry_backoff`, Telegram/iMessage sub-configs |
| `models.py` | `DatabaseConfig` | DB path, `market_cache_ttl`, backup settings |
| `models.py` | `ExportConfig` | CSV path, auto-export interval |
| `models.py` | `LoggingConfig` | Log level, format (JSON/text), rotation, metrics interval |
| `loader.py` | `load_config()` | Reads YAML → resolves .env → returns validated `AppConfig` |

**Validation highlights:**
- `TargetAccount.address`: regex `^0x[a-fA-F0-9]{40}$`, auto-lowercased
- `SimulationConfig.delays`: auto-sorted, de-duplicated, must be positive
- `MarketFilterConfig`: `min_duration <= max_duration` enforced
- `SystemConfig`: `FORCE_READ_ONLY` env always wins

### 2.2 API Layer (`src/api/`)

| File | Class | Purpose |
|------|-------|---------|
| `client.py` | `PolymarketClient` | Async context-manager HTTP client (`aiohttp`) |
| `rate_limiter.py` | `TokenBucketRateLimiter` | Token-bucket with burst; `async acquire()` |
| `websocket.py` | `PolymarketWebSocket` | Auto-reconnect WebSocket (market/user channels) |

**PolymarketClient methods:**

| Method | Endpoint | Auth | Notes |
|--------|----------|------|-------|
| `get_trades(user, limit)` | `GET data-api/trades` | No | Main polling source |
| `get_activity(user, limit, type)` | `GET data-api/activity` | No | Richer than trades |
| `get_positions(user)` | `GET data-api/positions` | No | Open positions |
| `get_orderbook(token_id)` | `GET clob/book` | No | Used in simulator |
| `get_price(token_id)` | `GET clob/price` | No | |
| `get_midpoint(token_id)` | `GET clob/midpoint` | No | |
| `get_spread(token_id)` | `GET clob/spread` | No | |
| `batch_get_orderbooks(ids)` | parallel `GET /book` | No | `asyncio.gather` |
| `get_market(condition_id)` | `GET gamma/markets/{id}` | No | Market metadata |
| `get_event(event_id)` | `GET gamma/events/{id}` | No | |
| `search_markets(query)` | `GET gamma/markets` | No | |
| `create_order()` | **BLOCKED** | — | `raise RuntimeError` always |
| `cancel_order()` | **BLOCKED** | — | `raise RuntimeError` always |

**Safety:** Every `_request()` call first runs `_assert_read_only()` which checks both
the module-level `READ_ONLY_MODE = True` and the `FORCE_READ_ONLY` env var.

**Retry logic:** Up to 3 retries with exponential backoff (2^attempt seconds).
HTTP 429 responses honour the `Retry-After` header.

**Rate limiter:** Token-bucket algorithm.
- `max_requests / time_window` = sustained rate
- `burst_size` = max stored tokens for short bursts
- `acquire()` is async; blocks the coroutine (not the event loop) when tokens are depleted

**WebSocket:**
- Channels: `market` (public), `user` (authenticated)
- Heartbeat: PING every 10 seconds
- Reconnect: exponential delay (5s × attempt), max 5 consecutive failures then pause
- Dynamic subscribe/unsubscribe

### 2.3 Core Engine (`src/core/`)

| File | Class | Purpose |
|------|-------|---------|
| `monitor.py` | `TradeMonitor` | Discovers new trades, applies market filter, dispatches callbacks |
| `simulator.py` | `TradeSimulator` | Delay → orderbook snapshot → slippage/fee → persist |
| `settlement.py` | `SettlementEngine` | Checks market resolution → calculates PnL → updates status |
| `portfolio.py` | `Portfolio` | Read-only stats: open positions, PnL summary |
| `app.py` | `Application` | Top-level orchestrator; wires and runs everything |

**TradeMonitor**
- `poll_once()` → iterates active targets → `_poll_target()` each
- First poll seeds `_seen` set with existing trade hashes (no dispatch)
- Subsequent polls compare `transactionHash`; new ones go through `_passes_market_filter()`
- Market filter: regex-based asset match, keyword match, duration-range check, exclude keywords
- Dispatches via `on_new_trade(callback)` pattern

**TradeSimulator**
- `simulate(target, trade)` → runs one simulation per configured delay
- Each delay: `asyncio.sleep(delay)` → `get_orderbook()` → extract `best_ask` (BUY) or `best_bid` (SELL)
- Slippage: `(sim_price - target_price) / target_price × 100`
- Fee: `investment × fee_rate`
- If slippage exceeds `max_slippage_pct` → marks `sim_success = False`
- Empty orderbook → marked as FAILED with reason
- Deduplication via `trade_exists(trade_id)` check

**SettlementEngine**
- `settlement_loop(interval=60)` → periodic check of all OPEN trades
- Groups by `condition_id` to minimise API calls
- Fetches market data from Gamma API; caches in `market_cache` table
- PnL calculation:
  - BUY: `payout = (investment / sim_price) × resolution_price`
  - SELL: `payout = (investment / sim_price) × (1 - resolution_price)`
  - `pnl = payout - investment - fee`

**Application**
- Background tasks (all via `asyncio.create_task`):
  1. `poll_loop` or `_run_websocket` (monitoring)
  2. `settlement_loop` (PnL resolution)
  3. `notifier.run` (batched notifications)
  4. `metrics.run` (periodic metrics logging)
  5. `_periodic_portfolio_log` (every 5 min)
  6. `_periodic_export` (CSV at configured interval)
- Graceful shutdown: cancels all tasks, flushes notifier, closes DB

### 2.4 Data Layer (`src/data/`)

| File | Class/Function | Purpose |
|------|----------------|---------|
| `database.py` | `Database` | Async SQLite with schema auto-creation |
| `models.py` | `SimTrade`, `MarketInfo`, `MetricRecord`, `AccountStats` | Dataclass models |
| `export.py` | `export_trades_to_csv()` | Write trade dicts to timestamped CSV |

**Database tables:**

| Table | Rows | Key columns |
|-------|------|-------------|
| `monitored_accounts` | 1 per target | `address`, `nickname`, `total_trades`, `last_trade_at` |
| `sim_trades` | 1 per simulation | 30 columns; see schema in `database.py` |
| `market_cache` | 1 per market | `condition_id`, `is_active`, `is_resolved`, `cache_ttl`, `last_updated` |
| `metrics` | 1 per snapshot | `metric_type`, `metric_value`, JSON `metadata` |
| `notification_history` | 1 per send attempt | `event_type`, `channel`, `success`, `retry_count` |

**Indexes:** `idx_sim_trades_target`, `idx_sim_trades_market`, `idx_sim_trades_status`,
`idx_sim_trades_timestamp`, `idx_market_cache_active`, `idx_metrics_type_time`

**Cache TTL logic:** `get_cached_market()` checks `(now - last_updated) > cache_ttl`;
returns `None` if expired, forcing a re-fetch from API.

**Statistics query:** `get_statistics()` aggregates total trades, open/settled/failed counts,
total PnL, win rate, avg slippage, avg fee, best/worst trade.

**PnL summary:** `get_pnl_summary(target_address?)` groups by `target_nickname, sim_delay`
with trade count, success count, total PnL, avg slippage, win rate.

### 2.5 Notifications (`src/notifications/`)

| File | Class | Purpose |
|------|-------|---------|
| `manager.py` | `NotificationManager` | Queue → aggregate → dispatch with retry |
| `manager.py` | `NotificationChannel` | ABC: `.send(message) -> bool` |
| `manager.py` | `EventType` | Constants: `NEW_TRADE`, `SIM_EXECUTED`, `SIM_FAILED`, `MARKET_SETTLED`, `DAILY_SUMMARY`, `ERROR_ALERT` |
| `telegram.py` | `TelegramNotifier` | `python-telegram-bot` async send |
| `imessage.py` | `IMessageNotifier` | macOS `osascript` AppleScript |

**Aggregation:** Events enqueued via `notify(event_type, data)`.
Every `aggregation_interval` seconds, the manager drains the queue,
groups events by type, formats a single batched message, and sends
through all registered channels.

**Retry:** Each channel retried up to `max_retries` with `retry_backoff` delays
(default: 1s, 2s, 4s exponential). Failures logged to `notification_history` table.

**Telegram:** Truncates to 4000 chars (Telegram limit 4096). Lazy-inits the bot on first send.

**iMessage:** macOS only; uses `osascript` subprocess. Truncates to 2000 chars.

### 2.6 Utilities (`src/utils/`)

| File | Class | Purpose |
|------|-------|---------|
| `logger.py` | `setup_logger()` | Configures loguru: console (human) + file (JSON) + metrics sink |
| `metrics.py` | `MetricsCollector` | Ring-buffer metrics + periodic JSON snapshot to `metrics.log` and DB |

**Log files:**
- `logs/app.log` — all application logs (rotated by size, retained 30 days)
- `logs/metrics.log` — metrics-only entries (filtered by `extra.metrics` flag)

**Metrics snapshot** (emitted every `metrics_interval` seconds):
```json
{
  "ts": 1708531200.0,
  "active_accounts": 1,
  "total_trades": 42,
  "polls_completed": 1200,
  "failed_requests": 3,
  "avg_api_latency_ms": 185.2,
  "avg_slippage_pct": 0.34,
  "recent_pnl_sum": 12.50,
  "notification_success_rate": 100.0
}
```

### 2.7 CLI (`src/cli/`)

| Command | Description |
|---------|-------------|
| `python main.py run` | Start monitoring loop (default: poll mode) |
| `python main.py run --mode ws` | WebSocket mode |
| `python main.py run --dry-run` | Disable all notifications |
| `python main.py export [--output file.csv]` | Export all trades to CSV |
| `python main.py stats [--target addr]` | Print statistics |
| `python main.py check-config` | Validate `config.yaml` without starting |
| `python main.py --config path/to/alt.yaml run` | Use alternate config |

---

## 3. Database Schema (ERD)

```
monitored_accounts          sim_trades                     market_cache
┌─────────────────┐        ┌───────────────────────┐      ┌──────────────────┐
│ id (PK)         │        │ id (PK)               │      │ condition_id (PK)│
│ address (UQ)    │◄──FK───│ target_address         │      │ market_id        │
│ nickname        │        │ target_nickname        │      │ market_name      │
│ weight          │        │ market_id              │      │ event_slug       │
│ is_active       │        │ market_name            │      │ end_date         │
│ total_trades    │        │ condition_id           │      │ is_active        │
│ last_trade_at   │        │ event_slug             │      │ is_resolved      │
│ created_at      │        │ target_side            │      │ resolution_price │
│ updated_at      │        │ target_price           │      │ cache_ttl        │
└─────────────────┘        │ target_size            │      │ last_updated     │
                           │ target_timestamp       │      │ created_at       │
metrics                    │ target_execution_time  │      └──────────────────┘
┌─────────────────┐        │ target_pnl             │
│ id (PK)         │        │ sim_delay              │      notification_history
│ timestamp       │        │ sim_price              │      ┌──────────────────┐
│ metric_type     │        │ sim_delayed_price      │      │ id (PK)          │
│ metric_value    │        │ sim_investment         │      │ event_type       │
│ metadata (JSON) │        │ sim_fee                │      │ channel          │
│ created_at      │        │ sim_success            │      │ message          │
└─────────────────┘        │ sim_failure_reason     │      │ success          │
                           │ slippage_pct           │      │ retry_count      │
                           │ total_cost             │      │ error_message    │
                           │ status                 │      │ sent_at          │
                           │ settlement_price       │      └──────────────────┘
                           │ pnl                    │
                           │ pnl_pct                │
                           │ created_at             │
                           │ settled_at             │
                           └───────────────────────┘
```

---

## 4. Configuration Reference

All configuration lives in **`config/config.yaml`**. Secrets reference env vars via `${VAR}` syntax.

### 4.1 `system`
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `read_only_mode` | bool | `true` | Master safety switch |
| `force_read_only` | bool | `true` | Backup safety; also enforced by `FORCE_READ_ONLY` env |

### 4.2 `monitoring`
| Key | Type | Default | Range | Description |
|-----|------|---------|-------|-------------|
| `mode` | enum | `poll` | `poll` \| `websocket` | Data acquisition strategy |
| `poll_interval` | int | `3` | 1–60 | Seconds between poll cycles |
| `max_concurrent` | int | `5` | 1–50 | Max parallel API requests |
| `retry_on_error` | bool | `true` | | Retry failed polls |
| `max_retries` | int | `3` | 0–10 | Per-request retries |

### 4.3 `simulation`
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `delays` | list[int] | `[1, 3]` | Seconds to wait before sampling orderbook |
| `investment_per_trade` | float | `100.0` | USD per simulated trade |
| `fee_rate` | float | `0.015` | 1.5% transaction fee |
| `enable_slippage_check` | bool | `true` | Reject trades exceeding slippage limit |
| `max_slippage_pct` | float | `5.0` | Maximum tolerated slippage % |

### 4.4 `market_filter`
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable filtering |
| `assets` | list[str] | `[BTC, ETH, ...]` | Asset name keywords |
| `min_duration_minutes` | int | `5` | Minimum market duration |
| `max_duration_minutes` | int | `15` | Maximum market duration |
| `keywords` | list[str] | `[up, down, ...]` | Required keywords in market title |
| `exclude_keywords` | list[str] | `[week, month, ...]` | Exclusion keywords |

### 4.5 `targets`
```yaml
targets:
  - address: "0x88f46b9e..."   # 42-char hex, validated
    nickname: "PBot1"
    active: true                # false = skip
    weight: 1.0                 # 0.0–1.0, future use
```

### 4.6 `api`
| Key | Type | Default |
|-----|------|---------|
| `base_urls.gamma` | str | `https://gamma-api.polymarket.com` |
| `base_urls.clob` | str | `https://clob.polymarket.com` |
| `base_urls.data` | str | `https://data-api.polymarket.com` |
| `websocket_urls.market` | str | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| `timeout` | int | `30` (5–120) |
| `rate_limit.max_requests` | int | `100` |
| `rate_limit.time_window` | int | `60` |
| `rate_limit.burst_size` | int | `10` |

### 4.7 `notifications`
| Key | Type | Default |
|-----|------|---------|
| `enabled` | bool | `true` |
| `aggregation_interval` | int | `30` seconds |
| `max_retries` | int | `3` |
| `retry_backoff` | list[int] | `[1, 2, 4]` |
| `telegram.enabled` | bool | `false` |
| `telegram.bot_token` | str | `${TELEGRAM_BOT_TOKEN}` |
| `telegram.chat_id` | str | `${TELEGRAM_CHAT_ID}` |
| `imessage.enabled` | bool | `false` |
| `imessage.phone_number` | str | `${IMESSAGE_PHONE}` |

### 4.8 `database`
| Key | Type | Default |
|-----|------|---------|
| `path` | str | `data/trades.db` |
| `backup_enabled` | bool | `true` |
| `backup_interval` | int | `3600` |
| `market_cache_ttl` | int | `3600` |
| `auto_vacuum` | bool | `true` |

### 4.9 `export`
| Key | Type | Default |
|-----|------|---------|
| `enabled` | bool | `true` |
| `csv_path` | str | `data/exports/` |
| `auto_export_interval` | int | `3600` |

### 4.10 `logging`
| Key | Type | Default |
|-----|------|---------|
| `level` | str | `INFO` |
| `format` | enum | `json` (`json` \| `text`) |
| `rotation` | str | `100 MB` |
| `retention` | str | `30 days` |
| `metrics_enabled` | bool | `true` |
| `metrics_interval` | int | `60` |

---

## 5. Polymarket API Reference (Used Endpoints)

| API | Endpoint | Method | Auth | Description |
|-----|----------|--------|------|-------------|
| Data | `/trades?user={addr}` | GET | No | Trade history for a wallet |
| Data | `/activity?user={addr}` | GET | No | Full activity log |
| Data | `/positions?user={addr}` | GET | No | Open positions |
| CLOB | `/book?token_id={id}` | GET | No | Order book (bids/asks) |
| CLOB | `/price?token_id={id}` | GET | No | Current price |
| CLOB | `/midpoint?token_id={id}` | GET | No | Mid price |
| CLOB | `/spread?token_id={id}` | GET | No | Bid-ask spread |
| Gamma | `/markets/{condition_id}` | GET | No | Market metadata |
| Gamma | `/events/{event_id}` | GET | No | Event metadata |
| Gamma | `/markets` | GET | No | List/search markets |
| WSS | `ws/market` | WS | No | Real-time orderbook/trades |
| WSS | `ws/user` | WS | Yes | Personal order updates |

---

## 6. Safety Architecture

### 6.1 Triple-layer read-only protection

```
Layer 1: config.yaml          →  system.read_only_mode: true
Layer 2: .env                 →  FORCE_READ_ONLY=true
Layer 3: Code (client.py)     →  READ_ONLY_MODE = True (module constant)
```

- `SystemConfig.enforce_read_only()` — Pydantic validator overrides config if env says `true`
- `Application._assert_safety()` — startup check, forces env if missing
- `PolymarketClient._assert_read_only()` — called before **every** HTTP request
- `create_order()` / `cancel_order()` — **unconditionally raise RuntimeError**

### 6.2 Secrets management

- All secrets in `.env` (git-ignored)
- YAML references via `${VAR}` resolved at config load time
- No private keys used for transaction signing
- API key used only for read-scope headers

---

## 7. Deployment

### 7.1 Local

```bash
source .venv/bin/activate
python main.py run
```

### 7.2 Docker

```bash
docker-compose up -d
docker-compose logs -f
```

Volumes: `./data` (DB + CSV), `./logs`, `./config` (read-only mount).

### 7.3 VPS / Production

```bash
# Pull + rebuild
git pull origin main
docker-compose up -d --build

# Or systemd
# See docs/CICD.md for full automation
```
