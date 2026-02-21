# Testing Guide

> Polymarket Copy Trading Simulator – testing strategy, test cases, and execution guide.

---

## 1. Testing Strategy

### 1.1 Test Pyramid

```
          ┌──────────┐
          │  E2E     │   Live API smoke tests (manual / CI nightly)
         ─┤          ├─
        ┌─┤Integration├─┐  Database + API mock + full pipeline
       ─┤ │          │ ├─
      ┌─┤ └──────────┘ ├─┐
     ─┤ │   Unit Tests  │ ├─  Pure logic: simulator, filter, PnL, config
      └─┤              ├─┘
        └──────────────┘
```

| Layer | Scope | Mocking | Speed |
|-------|-------|---------|-------|
| **Unit** | Single function/class | All I/O mocked | < 1s per test |
| **Integration** | Module interaction (Monitor → Simulator → DB) | API mocked, real SQLite | < 5s |
| **E2E / Smoke** | Full pipeline against live Polymarket API | Nothing mocked | 30–60s |

### 1.2 What to Test

| Module | Critical paths |
|--------|---------------|
| `config/models.py` | Validation: bad address, negative delay, invalid log level, duration range |
| `config/loader.py` | Missing file, empty file, env-var resolution, `FORCE_READ_ONLY` override |
| `api/rate_limiter.py` | Token refill, burst, concurrent acquire, starvation |
| `api/client.py` | Retry on 500, 429 handling, `_assert_read_only`, `create_order` blocked |
| `core/monitor.py` | First-poll seeding, dedup, market filter (asset, keyword, duration, exclude) |
| `core/simulator.py` | Delay execution, slippage calc, fee calc, empty orderbook, max slippage rejection |
| `core/settlement.py` | PnL BUY/SELL, market not resolved, cache TTL, group-by-market optimization |
| `data/database.py` | Schema creation, upsert, trade_exists, settle_trade, statistics, cache TTL |
| `notifications/manager.py` | Queue drain, aggregation format, retry backoff, channel failure |

---

## 2. Running Tests

### 2.1 Prerequisites

```bash
cd /Users/airhao3/Nextcloud/JMM_trade
source .venv/bin/activate
pip install -r requirements.txt   # includes pytest, pytest-asyncio
```

### 2.2 Commands

```bash
# Run all tests
pytest tests/ -v

# Run specific file
pytest tests/test_config.py -v

# Run specific test
pytest tests/test_simulator.py::test_slippage_calculation -v

# Run with coverage
pip install pytest-cov
pytest tests/ --cov=src --cov-report=term-missing

# Run only unit tests (mark-based)
pytest tests/ -m unit -v

# Run only integration tests
pytest tests/ -m integration -v

# Run with debug output
pytest tests/ -v -s --log-cli-level=DEBUG
```

### 2.3 Smoke Test (Live API)

```bash
# Requires network access, no API key needed for public endpoints
pytest tests/test_smoke.py -v -m smoke --timeout=60
```

---

## 3. Test File Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_config.py           # Config validation tests
├── test_rate_limiter.py     # Token bucket tests
├── test_api_client.py       # API client (mocked HTTP)
├── test_monitor.py          # Trade discovery + market filter
├── test_simulator.py        # Shadow execution logic
├── test_settlement.py       # PnL calculation
├── test_database.py         # SQLite operations
├── test_notifications.py    # Notification manager
├── test_export.py           # CSV export
├── test_cli.py              # CLI command tests
└── test_smoke.py            # Live API smoke tests
```

---

## 4. Test Cases Catalog

### 4.1 Config Validation (`test_config.py`)

| ID | Test | Input | Expected |
|----|------|-------|----------|
| C01 | Valid config loads | `config/config.yaml` | `AppConfig` returned, no error |
| C02 | Invalid address rejected | `address: "0xZZZ"` | `ValidationError` |
| C03 | Negative delay rejected | `delays: [-1, 3]` | `ValidationError` |
| C04 | Delays auto-sorted | `delays: [3, 1, 1]` | `[1, 3]` |
| C05 | Duration range enforced | `min: 15, max: 5` | `ValidationError` |
| C06 | Invalid log level | `level: "VERBOSE"` | `ValidationError` |
| C07 | FORCE_READ_ONLY env | `read_only_mode: false` + env=true | `read_only_mode == True` |
| C08 | No active targets | all `active: false` | `ValidationError` |
| C09 | Missing config file | nonexistent path | `FileNotFoundError` |
| C10 | Empty config file | empty YAML | `ValueError` |
| C11 | Env var resolution | `${TELEGRAM_BOT_TOKEN}` | Resolved from os.environ |

### 4.2 Rate Limiter (`test_rate_limiter.py`)

| ID | Test | Expected |
|----|------|----------|
| R01 | Acquire within burst | Immediate return, no wait |
| R02 | Acquire exceeds burst | Blocks until tokens refill |
| R03 | Refill rate correct | After 1s, `max_requests / time_window` tokens added |
| R04 | Concurrent acquire | Multiple coroutines share tokens fairly |
| R05 | Burst size cap | Tokens never exceed `burst_size` |

### 4.3 API Client (`test_api_client.py`)

| ID | Test | Expected |
|----|------|----------|
| A01 | Successful GET | Returns parsed JSON |
| A02 | Retry on 500 | Retries 3 times, exponential backoff |
| A03 | 429 rate limit | Sleeps `Retry-After`, then retries |
| A04 | Timeout handling | Retries, then raises `ConnectionError` |
| A05 | `create_order` blocked | `RuntimeError("PERMANENTLY DISABLED")` |
| A06 | `cancel_order` blocked | `RuntimeError("PERMANENTLY DISABLED")` |
| A07 | `_assert_read_only` | No error when `FORCE_READ_ONLY=true` |
| A08 | API key in header | `Authorization: Bearer {key}` present |
| A09 | `avg_latency` property | Correct running average |

### 4.4 Monitor (`test_monitor.py`)

| ID | Test | Expected |
|----|------|----------|
| M01 | First poll seeds | No callbacks fired, `_seen` populated |
| M02 | Second poll detects new | Callback fired for new `transactionHash` |
| M03 | Duplicate ignored | Same hash not dispatched twice |
| M04 | Filter: asset match | "BTC up 5 min" → passes |
| M05 | Filter: asset miss | "DOGE up 5 min" → filtered |
| M06 | Filter: keyword match | "ETH higher 10 min" → passes |
| M07 | Filter: keyword miss | "ETH 10 min" (no direction) → filtered |
| M08 | Filter: duration in range | "BTC up 10 min" → passes (5–15) |
| M09 | Filter: duration out of range | "BTC up 30 min" → filtered |
| M10 | Filter: exclude keyword | "BTC up this month" → filtered |
| M11 | Filter disabled | `enabled: false` → all trades pass |
| M12 | Multiple targets | Both polled, independent `_seen` sets |

### 4.5 Simulator (`test_simulator.py`)

| ID | Test | Expected |
|----|------|----------|
| S01 | BUY: best ask used | `sim_price = asks[0].price` |
| S02 | SELL: best bid used | `sim_price = bids[0].price` |
| S03 | Slippage calculated | `(sim - target) / target × 100` |
| S04 | Fee calculated | `investment × fee_rate` |
| S05 | Total cost | `investment + fee` |
| S06 | Slippage exceeds limit | `sim_success=False`, status=FAILED |
| S07 | Empty orderbook | `sim_success=False`, reason="Empty orderbook" |
| S08 | Multiple delays | 2 records created (1s + 3s) |
| S09 | Deduplication | Same trade_id not inserted twice |
| S10 | Delay timing | `asyncio.sleep` called with correct seconds |

### 4.6 Settlement (`test_settlement.py`)

| ID | Test | Expected |
|----|------|----------|
| T01 | BUY PnL win | `shares=100/0.5=200`, resolution=1.0, `pnl=200-100-1.5=98.5` |
| T02 | BUY PnL loss | resolution=0.0, `pnl=0-100-1.5=-101.5` |
| T03 | SELL PnL win | resolution=0.0, `pnl = shares×1.0 - inv - fee` |
| T04 | Market not resolved | `settle_once()` returns 0, no updates |
| T05 | Cache hit (fresh) | No API call made |
| T06 | Cache expired | API called, cache refreshed |
| T07 | Group by condition_id | 3 trades same market = 1 API call |
| T08 | Status updated | `OPEN` → `SETTLED`, `settled_at` set |

### 4.7 Database (`test_database.py`)

| ID | Test | Expected |
|----|------|----------|
| D01 | Schema created | All 5 tables exist after `connect()` |
| D02 | Insert sim trade | Row inserted, `trade_exists()` returns True |
| D03 | Duplicate rejected | `INSERT OR IGNORE` — no error, no duplicate |
| D04 | `get_open_trades` | Only `status='OPEN'` returned |
| D05 | `settle_trade` | Status, PnL, settled_at updated |
| D06 | `get_statistics` | Correct counts, sums, averages |
| D07 | `get_pnl_summary` | Grouped by target + delay |
| D08 | Market cache TTL | Expired entry returns None |
| D09 | `mark_market_resolved` | `is_resolved=1, is_active=0` |
| D10 | Upsert account | Insert new / update existing |

### 4.8 Notifications (`test_notifications.py`)

| ID | Test | Expected |
|----|------|----------|
| N01 | Queue + flush | Events batched, single message sent |
| N02 | Aggregation format | Multi-event grouped by type |
| N03 | Retry on failure | 3 retries with backoff |
| N04 | All retries exhausted | Error logged to `notification_history` |
| N05 | Disabled config | `notify()` is a no-op |
| N06 | Telegram truncation | Message capped at 4000 chars |

---

## 5. Fixtures Reference (`conftest.py`)

```python
# Key fixtures to implement:

@pytest.fixture
def sample_config() -> AppConfig:
    """Minimal valid AppConfig for testing."""

@pytest.fixture
async def db(tmp_path) -> Database:
    """In-memory or tmp SQLite database."""

@pytest.fixture
def mock_api_client() -> AsyncMock:
    """Mocked PolymarketClient with preset responses."""

@pytest.fixture
def sample_trade() -> dict:
    """A realistic Polymarket trade dict."""

@pytest.fixture
def sample_orderbook() -> dict:
    """A realistic orderbook response."""

@pytest.fixture
def sample_sim_trade() -> SimTrade:
    """A populated SimTrade dataclass instance."""
```

---

## 6. Writing New Tests

### 6.1 Naming Convention

```
test_{module}_{scenario}_{expected_outcome}

Examples:
  test_simulator_buy_slippage_exceeds_limit_marks_failed
  test_monitor_first_poll_seeds_without_callback
  test_config_invalid_address_raises_validation_error
```

### 6.2 Markers

```python
import pytest

@pytest.mark.unit
def test_something_fast():
    ...

@pytest.mark.integration
async def test_full_pipeline():
    ...

@pytest.mark.smoke
async def test_live_api():
    ...
```

Register in `pyproject.toml` or `pytest.ini`:
```ini
[tool:pytest]
markers =
    unit: Pure logic tests, no I/O
    integration: Tests with DB or mocked API
    smoke: Tests against live Polymarket API
asyncio_mode = auto
```

### 6.3 Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_call()
    assert result is not None
```

### 6.4 Mocking API Responses

```python
from unittest.mock import AsyncMock, patch

async def test_simulator_with_mock():
    mock_client = AsyncMock()
    mock_client.get_orderbook.return_value = {
        "asks": [{"price": "0.55", "size": "100"}],
        "bids": [{"price": "0.45", "size": "200"}],
    }

    simulator = TradeSimulator(config, mock_client, db)
    results = await simulator.simulate(target, trade)

    assert len(results) == 2  # 1s + 3s
    assert results[0].sim_price == 0.55
```

---

## 7. Coverage Goals

| Module | Target | Priority |
|--------|--------|----------|
| `config/models.py` | 95% | High |
| `core/simulator.py` | 90% | High |
| `core/monitor.py` | 85% | High |
| `core/settlement.py` | 85% | High |
| `data/database.py` | 80% | High |
| `api/rate_limiter.py` | 90% | Medium |
| `api/client.py` | 75% | Medium |
| `notifications/manager.py` | 70% | Medium |
| `utils/` | 50% | Low |
| `cli/` | 50% | Low |

---

## 8. Debugging Tests

```bash
# Verbose output with print statements
pytest tests/test_simulator.py -v -s

# Stop on first failure
pytest tests/ -x

# Re-run only failed tests
pytest tests/ --lf

# Show local variables on failure
pytest tests/ --tb=long

# Debug with pdb on failure
pytest tests/ --pdb
```
