"""Shared fixtures for all test modules."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

# Ensure read-only safety in tests
os.environ["FORCE_READ_ONLY"] = "true"


# ── Config fixtures ──────────────────────────────────────

@pytest.fixture
def raw_config_dict() -> Dict[str, Any]:
    """Minimal valid config dict for constructing AppConfig."""
    return {
        "system": {"read_only_mode": True, "force_read_only": True},
        "monitoring": {"mode": "poll", "poll_interval": 3, "max_concurrent": 5},
        "simulation": {
            "delays": [1, 3],
            "investment_per_trade": 100.0,
            "fee_rate": 0.015,
            "enable_slippage_check": True,
            "max_slippage_pct": 5.0,
        },
        "market_filter": {
            "enabled": True,
            "assets": ["BTC", "ETH", "Bitcoin", "Ethereum"],
            "min_duration_minutes": 5,
            "max_duration_minutes": 15,
            "keywords": ["up", "down", "higher", "lower"],
            "exclude_keywords": ["week", "month", "year"],
        },
        "targets": [
            {
                "address": "0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db",
                "nickname": "PBot1",
                "active": True,
                "weight": 1.0,
            }
        ],
        "api": {
            "base_urls": {
                "gamma": "https://gamma-api.polymarket.com",
                "clob": "https://clob.polymarket.com",
                "data": "https://data-api.polymarket.com",
            },
            "websocket_urls": {
                "market": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            },
            "timeout": 30,
            "rate_limit": {
                "max_requests": 100,
                "time_window": 60,
                "burst_size": 10,
            },
        },
        "notifications": {"enabled": False},
        "database": {"path": "data/test_trades.db"},
        "export": {"enabled": False, "csv_path": "data/exports/"},
        "logging": {"level": "DEBUG", "format": "text", "metrics_enabled": False},
    }


@pytest.fixture
def sample_config(raw_config_dict):
    """Build a validated AppConfig."""
    from src.config.models import AppConfig

    return AppConfig(**raw_config_dict)


@pytest.fixture
def sample_target():
    """A single TargetAccount instance."""
    from src.config.models import TargetAccount

    return TargetAccount(
        address="0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db",
        nickname="PBot1",
        active=True,
        weight=1.0,
    )


# ── API mock fixtures ────────────────────────────────────

@pytest.fixture
def sample_trade() -> Dict[str, Any]:
    """A realistic Polymarket trade response dict."""
    return {
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
        "transactionHash": "0xabc123def456789012345678901234567890abcd",
    }


@pytest.fixture
def sample_trade_filtered() -> Dict[str, Any]:
    """A trade that should NOT pass market filter (monthly market)."""
    return {
        "proxyWallet": "0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db",
        "side": "BUY",
        "asset": "token_id_2",
        "conditionId": "0xaabbccdd",
        "size": 100,
        "price": 0.60,
        "timestamp": 1708531300,
        "title": "Will BTC be up this month?",
        "slug": "btc-monthly",
        "eventSlug": "btc-monthly-prediction",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "transactionHash": "0xdef456789012345678901234567890abcdef1234",
    }


@pytest.fixture
def sample_orderbook() -> Dict[str, Any]:
    """A realistic orderbook response."""
    return {
        "asks": [
            {"price": "0.57", "size": "100"},
            {"price": "0.58", "size": "200"},
            {"price": "0.60", "size": "500"},
        ],
        "bids": [
            {"price": "0.53", "size": "150"},
            {"price": "0.52", "size": "300"},
            {"price": "0.50", "size": "1000"},
        ],
    }


@pytest.fixture
def empty_orderbook() -> Dict[str, Any]:
    """An empty orderbook."""
    return {"asks": [], "bids": []}


@pytest.fixture
def mock_api_client(sample_orderbook) -> AsyncMock:
    """Mocked PolymarketClient with preset responses."""
    client = AsyncMock()
    client.get_trades.return_value = []
    client.get_orderbook.return_value = sample_orderbook
    client.get_market.return_value = {
        "slug": "btc-up-5-minutes",
        "question": "BTC up 5 minutes",
        "closed": False,
        "resolved": False,
        "outcomePrices": ["0.55", "0.45"],
        "endDate": "2025-12-31",
        "eventSlug": "btc-5min-prediction",
    }
    client.get_positions.return_value = []
    client.get_price.return_value = {"price": "0.55"}
    client.avg_latency = 0.1
    return client


# ── Database fixtures ────────────────────────────────────

@pytest_asyncio.fixture
async def db(tmp_path):
    """Temporary SQLite database for testing."""
    from src.config.models import DatabaseConfig
    from src.data.database import Database

    db_path = str(tmp_path / "test.db")
    config = DatabaseConfig(path=db_path, market_cache_ttl=3600)
    database = Database(config)
    await database.connect()
    yield database
    await database.close()


# ── SimTrade fixture ─────────────────────────────────────

@pytest.fixture
def sample_sim_trade():
    """A populated SimTrade dataclass instance."""
    from src.data.models import SimTrade

    return SimTrade(
        trade_id="0xabc123_1s",
        target_address="0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db",
        target_nickname="PBot1",
        market_id="btc-up-5-minutes",
        market_name="BTC up 5 minutes",
        condition_id="0xdd22472e552920b8438158ea7238bfadfa4f736aa4cee91a6b86c39ead110917",
        event_slug="btc-5min-prediction",
        target_side="BUY",
        target_price=0.55,
        target_size=50.0,
        target_timestamp=1708531200,
        sim_delay=1,
        sim_price=0.57,
        sim_delayed_price=0.57,
        sim_investment=100.0,
        sim_fee=1.5,
        sim_success=True,
        slippage_pct=3.636,
        total_cost=101.5,
        status="OPEN",
    )
