"""Tests for TradeMonitor (src/core/monitor.py)."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.core.monitor import TradeMonitor


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_poll_seeds_without_callback(sample_config, mock_api_client, sample_trade):
    """First poll should seed _seen set but not fire callbacks."""
    mock_api_client.get_trades.return_value = [sample_trade]

    monitor = TradeMonitor(sample_config, mock_api_client)
    callback = AsyncMock()
    monitor.on_new_trade(callback)

    count, latency = await monitor.poll_once()
    assert count == 0
    assert latency >= 0
    callback.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_second_poll_detects_new_trade(sample_config, mock_api_client, sample_trade):
    """Second poll with a new trade should fire the callback."""
    trade1 = deepcopy(sample_trade)
    trade1["transactionHash"] = "0xfirst_trade_hash_1234567890abcdef12345678"
    trade2 = deepcopy(sample_trade)
    trade2["transactionHash"] = "0xsecond_trade_hash_234567890abcdef12345678"

    mock_api_client.get_trades.side_effect = [
        [trade1],  # first poll: seed
        [trade1, trade2],  # second poll: trade2 is new
    ]

    monitor = TradeMonitor(sample_config, mock_api_client)
    callback = AsyncMock()
    monitor.on_new_trade(callback)

    await monitor.poll_once()  # seed
    count, latency = await monitor.poll_once()  # detect new
    assert count == 1
    assert latency >= 0
    callback.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_not_dispatched(sample_config, mock_api_client, sample_trade):
    """Same trade hash should not be dispatched twice."""
    mock_api_client.get_trades.return_value = [sample_trade]

    monitor = TradeMonitor(sample_config, mock_api_client)
    callback = AsyncMock()
    monitor.on_new_trade(callback)

    await monitor.poll_once()  # seed
    count, _ = await monitor.poll_once()  # same trades, nothing new
    assert count == 0
    callback.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_asset_match(sample_config, mock_api_client, sample_trade):
    """Trade with BTC in title should pass filter."""
    monitor = TradeMonitor(sample_config, mock_api_client)
    assert monitor._passes_market_filter(sample_trade) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_asset_miss(sample_config, mock_api_client, sample_trade):
    """Trade without matching asset should be filtered."""
    trade = deepcopy(sample_trade)
    trade["title"] = "DOGE up 5 minutes"
    monitor = TradeMonitor(sample_config, mock_api_client)
    assert monitor._passes_market_filter(trade) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_keyword_miss(sample_config, mock_api_client, sample_trade):
    """Trade without direction keyword should be filtered."""
    trade = deepcopy(sample_trade)
    trade["title"] = "BTC 10 minutes"
    monitor = TradeMonitor(sample_config, mock_api_client)
    assert monitor._passes_market_filter(trade) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_duration_out_of_range(sample_config, mock_api_client, sample_trade):
    """Trade with duration > max should be filtered."""
    trade = deepcopy(sample_trade)
    trade["title"] = "BTC up 90 minutes"
    monitor = TradeMonitor(sample_config, mock_api_client)
    assert monitor._passes_market_filter(trade) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_exclude_keyword(sample_config, mock_api_client, sample_trade_filtered):
    """Trade with exclude keyword (month) should be filtered."""
    monitor = TradeMonitor(sample_config, mock_api_client)
    assert monitor._passes_market_filter(sample_trade_filtered) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_disabled_passes_all(raw_config_dict, mock_api_client, sample_trade):
    """When filter is disabled, all trades should pass."""
    from copy import deepcopy

    from src.config.models import AppConfig

    d = deepcopy(raw_config_dict)
    d["market_filter"]["enabled"] = False
    config = AppConfig(**d)

    trade = deepcopy(sample_trade)
    trade["title"] = "Completely unrelated market about cats"
    monitor = TradeMonitor(config, mock_api_client)
    assert monitor._passes_market_filter(trade) is True
