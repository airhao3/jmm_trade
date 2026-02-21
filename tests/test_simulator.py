"""Tests for TradeSimulator (src/core/simulator.py)."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.core.simulator import TradeSimulator
from src.data.models import SimTrade


@pytest.mark.integration
@pytest.mark.asyncio
async def test_buy_uses_best_ask(sample_config, mock_api_client, db, sample_trade, sample_target):
    """BUY trade should use best ask (lowest sell offer) as sim_price."""
    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    assert len(results) == 2  # delays [1, 3]
    for r in results:
        assert r.sim_price == 0.57  # best ask from sample_orderbook


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sell_uses_best_bid(sample_config, mock_api_client, db, sample_trade, sample_target):
    """SELL trade should use best bid (highest buy offer) as sim_price."""
    trade = deepcopy(sample_trade)
    trade["side"] = "SELL"

    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, trade)

    for r in results:
        assert r.sim_price == 0.53  # best bid from sample_orderbook


@pytest.mark.unit
@pytest.mark.asyncio
async def test_slippage_calculation(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Slippage = (sim_price - target_price) / target_price * 100."""
    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    expected = ((0.57 - 0.55) / 0.55) * 100  # ~3.636%
    assert abs(results[0].slippage_pct - expected) < 0.01


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fee_calculation(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Fee = investment * fee_rate."""
    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    expected_fee = 100.0 * 0.015  # $1.50
    assert results[0].sim_fee == expected_fee


@pytest.mark.unit
@pytest.mark.asyncio
async def test_total_cost(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Total cost = investment + fee."""
    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    assert results[0].total_cost == 100.0 + 1.5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_orderbook_marks_failed(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Empty orderbook should produce a FAILED sim trade."""
    mock_api_client.get_orderbook.return_value = {"asks": [], "bids": []}

    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    for r in results:
        assert r.sim_success is False
        assert r.status == "FAILED"
        assert "Empty orderbook" in (r.sim_failure_reason or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slippage_exceeds_limit(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Slippage exceeding max_slippage_pct should mark trade as FAILED."""
    # Set best ask very high to exceed 5% slippage
    mock_api_client.get_orderbook.return_value = {
        "asks": [{"price": "0.90", "size": "100"}],
        "bids": [{"price": "0.10", "size": "100"}],
    }

    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    for r in results:
        assert r.sim_success is False
        assert "exceeds" in (r.sim_failure_reason or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_delays_produce_two_records(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Config delays [1, 3] should produce exactly 2 SimTrade records."""
    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    assert len(results) == 2
    assert results[0].sim_delay == 1
    assert results[1].sim_delay == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deduplication(sample_config, mock_api_client, db, sample_trade, sample_target):
    """Running simulate twice on same trade should not insert duplicates."""
    simulator = TradeSimulator(sample_config, mock_api_client, db)

    results1 = await simulator.simulate(sample_target, sample_trade)
    results2 = await simulator.simulate(sample_target, sample_trade)

    assert len(results1) == 2
    assert len(results2) == 0  # all skipped as duplicates


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_error_produces_failed_record(sample_config, mock_api_client, db, sample_trade, sample_target):
    """API exception during orderbook fetch should produce FAILED record."""
    mock_api_client.get_orderbook.side_effect = ConnectionError("API down")

    simulator = TradeSimulator(sample_config, mock_api_client, db)
    results = await simulator.simulate(sample_target, sample_trade)

    assert len(results) == 2
    for r in results:
        assert r.sim_success is False
        assert r.status == "FAILED"
