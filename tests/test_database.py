"""Tests for Database (src/data/database.py)."""

from __future__ import annotations

import pytest

from src.data.models import MarketInfo


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schema_created(db):
    """All 5 tables should exist after connect()."""
    async with db._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        rows = await cur.fetchall()
        names = {r["name"] for r in rows}

    assert "monitored_accounts" in names
    assert "sim_trades" in names
    assert "market_cache" in names
    assert "metrics" in names
    assert "notification_history" in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_and_exists(db, sample_sim_trade):
    """Insert a sim trade and verify trade_exists returns True."""
    await db.insert_sim_trade(sample_sim_trade)
    assert await db.trade_exists(sample_sim_trade.trade_id) is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_ignored(db, sample_sim_trade):
    """Inserting the same trade_id twice should not raise."""
    await db.insert_sim_trade(sample_sim_trade)
    await db.insert_sim_trade(sample_sim_trade)  # no error
    assert await db.trade_exists(sample_sim_trade.trade_id) is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_open_trades(db, sample_sim_trade):
    """get_open_trades should return only OPEN trades."""
    await db.insert_sim_trade(sample_sim_trade)
    open_trades = await db.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0]["status"] == "OPEN"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_settle_trade(db, sample_sim_trade):
    """settle_trade should update status, PnL, settled_at."""
    await db.insert_sim_trade(sample_sim_trade)
    await db.settle_trade(
        trade_id=sample_sim_trade.trade_id,
        settlement_price=1.0,
        pnl=73.04,
        pnl_pct=73.04,
    )

    open_trades = await db.get_open_trades()
    assert len(open_trades) == 0  # no longer open

    async with db._db.execute(
        "SELECT * FROM sim_trades WHERE trade_id = ?",
        (sample_sim_trade.trade_id,),
    ) as cur:
        row = await cur.fetchone()
        assert dict(row)["status"] == "SETTLED"
        assert dict(row)["pnl"] == 73.04
        assert dict(row)["settled_at"] is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_statistics_empty(db):
    """Statistics on empty DB should return zeros."""
    stats = await db.get_statistics()
    assert stats.total_trades == 0
    assert stats.total_pnl == 0.0
    assert stats.win_rate == 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_statistics_with_data(db, sample_sim_trade):
    """Statistics after inserting a trade."""
    await db.insert_sim_trade(sample_sim_trade)
    stats = await db.get_statistics()
    assert stats.total_trades == 1
    assert stats.open_positions == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_account(db):
    """upsert_account should insert new and update existing."""
    addr = "0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db"
    await db.upsert_account(addr, "PBot1", 1.0)
    await db.upsert_account(addr, "PBot1_Updated", 0.8)

    async with db._db.execute("SELECT * FROM monitored_accounts WHERE address = ?", (addr,)) as cur:
        row = await cur.fetchone()
        assert dict(row)["nickname"] == "PBot1_Updated"
        assert dict(row)["weight"] == 0.8


@pytest.mark.integration
@pytest.mark.asyncio
async def test_market_cache_upsert_and_fetch(db):
    """Market cache insert and fetch within TTL."""
    market = MarketInfo(
        condition_id="0xtest_condition_id",
        market_id="test-market",
        market_name="Test Market",
        event_slug="test-event",
        is_active=True,
        is_resolved=False,
        cache_ttl=3600,
    )
    await db.upsert_market_cache(market)

    cached = await db.get_cached_market("0xtest_condition_id")
    assert cached is not None
    assert cached["market_name"] == "Test Market"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_market_cache_miss(db):
    """Fetching non-existent market should return None."""
    cached = await db.get_cached_market("0xnonexistent")
    assert cached is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mark_market_resolved(db):
    """Resolved market should set is_resolved=1, is_active=0."""
    market = MarketInfo(
        condition_id="0xresolve_test",
        market_id="resolve-market",
        market_name="Resolve Me",
        is_active=True,
        is_resolved=False,
    )
    await db.upsert_market_cache(market)
    await db.mark_market_resolved("0xresolve_test", 1.0)

    async with db._db.execute(
        "SELECT * FROM market_cache WHERE condition_id = ?",
        ("0xresolve_test",),
    ) as cur:
        row = await cur.fetchone()
        d = dict(row)
        assert d["is_resolved"] == 1
        assert d["is_active"] == 0
        assert d["resolution_price"] == 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insert_metric(db):
    """Metrics insertion should not raise."""
    await db.insert_metric("api_latency", 0.185, {"endpoint": "/trades"})

    async with db._db.execute("SELECT COUNT(*) as c FROM metrics") as cur:
        row = await cur.fetchone()
        assert row["c"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_log_notification(db):
    """Notification log insertion."""
    await db.log_notification(
        event_type="NEW_TRADE",
        channel="telegram",
        message="Test message",
        success=True,
    )

    async with db._db.execute("SELECT COUNT(*) as c FROM notification_history") as cur:
        row = await cur.fetchone()
        assert row["c"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pnl_summary_empty(db):
    """PnL summary on empty DB should return empty list."""
    summary = await db.get_pnl_summary()
    assert summary == []
