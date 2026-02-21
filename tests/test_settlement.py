"""Tests for SettlementEngine (src/core/settlement.py)."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.core.settlement import SettlementEngine


@pytest.mark.unit
def test_pnl_buy_win():
    """BUY at 0.50, resolution at 1.0 should profit."""
    from src.core.settlement import SettlementEngine

    engine = SettlementEngine.__new__(SettlementEngine)
    trade = {
        "sim_price": 0.50,
        "sim_investment": 100.0,
        "sim_fee": 1.5,
        "target_side": "BUY",
    }
    pnl, pnl_pct = engine._calculate_pnl(trade, resolution_price=1.0)

    # shares = 100 / 0.50 = 200
    # payout = 200 * 1.0 = 200
    # pnl = 200 - 100 - 1.5 = 98.5
    assert pnl == 98.5
    assert pnl_pct == 98.5


@pytest.mark.unit
def test_pnl_buy_loss():
    """BUY at 0.50, resolution at 0.0 should lose everything."""
    engine = SettlementEngine.__new__(SettlementEngine)
    trade = {
        "sim_price": 0.50,
        "sim_investment": 100.0,
        "sim_fee": 1.5,
        "target_side": "BUY",
    }
    pnl, pnl_pct = engine._calculate_pnl(trade, resolution_price=0.0)

    # payout = 200 * 0 = 0
    # pnl = 0 - 100 - 1.5 = -101.5
    assert pnl == -101.5
    assert pnl_pct == -101.5


@pytest.mark.unit
def test_pnl_sell_win():
    """SELL at 0.50, resolution at 0.0 should profit."""
    engine = SettlementEngine.__new__(SettlementEngine)
    trade = {
        "sim_price": 0.50,
        "sim_investment": 100.0,
        "sim_fee": 1.5,
        "target_side": "SELL",
    }
    pnl, pnl_pct = engine._calculate_pnl(trade, resolution_price=0.0)

    # shares = 100 / 0.50 = 200
    # payout = 200 * (1 - 0) = 200
    # pnl = 200 - 100 - 1.5 = 98.5
    assert pnl == 98.5


@pytest.mark.unit
def test_pnl_sell_loss():
    """SELL at 0.50, resolution at 1.0 should lose."""
    engine = SettlementEngine.__new__(SettlementEngine)
    trade = {
        "sim_price": 0.50,
        "sim_investment": 100.0,
        "sim_fee": 1.5,
        "target_side": "SELL",
    }
    pnl, pnl_pct = engine._calculate_pnl(trade, resolution_price=1.0)

    # payout = 200 * (1 - 1) = 0
    # pnl = 0 - 100 - 1.5 = -101.5
    assert pnl == -101.5


@pytest.mark.unit
def test_pnl_zero_sim_price():
    """Zero sim_price should return 0 PnL (avoid division by zero)."""
    engine = SettlementEngine.__new__(SettlementEngine)
    trade = {
        "sim_price": 0.0,
        "sim_investment": 100.0,
        "sim_fee": 1.5,
        "target_side": "BUY",
    }
    pnl, pnl_pct = engine._calculate_pnl(trade, resolution_price=1.0)
    assert pnl == 0.0
    assert pnl_pct == 0.0


@pytest.mark.unit
def test_pnl_none_sim_price():
    """None sim_price should return 0 PnL."""
    engine = SettlementEngine.__new__(SettlementEngine)
    trade = {
        "sim_price": None,
        "sim_investment": 100.0,
        "sim_fee": 1.5,
        "target_side": "BUY",
    }
    pnl, pnl_pct = engine._calculate_pnl(trade, resolution_price=1.0)
    assert pnl == 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_settle_once_no_open(sample_config, mock_api_client, db):
    """settle_once with no open trades should return 0."""
    engine = SettlementEngine(sample_config, mock_api_client, db)
    count = await engine.settle_once()
    assert count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_settle_once_market_not_resolved(
    sample_config, mock_api_client, db, sample_sim_trade
):
    """Open trade with unresolved market should not be settled."""
    await db.insert_sim_trade(sample_sim_trade)

    # Market not resolved
    mock_api_client.get_market.return_value = {
        "slug": "btc-up-5-minutes",
        "question": "BTC up 5 minutes",
        "closed": False,
        "resolved": False,
        "outcomePrices": ["0.55", "0.45"],
        "endDate": "2025-12-31",
        "eventSlug": "btc-5min-prediction",
    }

    engine = SettlementEngine(sample_config, mock_api_client, db)
    count = await engine.settle_once()
    assert count == 0

    # Trade should still be OPEN
    open_trades = await db.get_open_trades()
    assert len(open_trades) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_settle_once_resolved_market(
    sample_config, mock_api_client, db, sample_sim_trade
):
    """Open trade with resolved market should be settled with PnL."""
    await db.insert_sim_trade(sample_sim_trade)

    # Market resolved: YES wins
    mock_api_client.get_market.return_value = {
        "slug": "btc-up-5-minutes",
        "question": "BTC up 5 minutes",
        "closed": True,
        "resolved": True,
        "outcomePrices": ["1.0", "0.0"],
        "endDate": "2025-02-21",
        "eventSlug": "btc-5min-prediction",
    }

    engine = SettlementEngine(sample_config, mock_api_client, db)
    count = await engine.settle_once()
    assert count == 1

    # Trade should be SETTLED
    open_trades = await db.get_open_trades()
    assert len(open_trades) == 0
