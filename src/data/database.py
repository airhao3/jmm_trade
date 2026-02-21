"""SQLite database layer with async support and cache TTL."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from src.config.models import DatabaseConfig

from .models import AccountStats, MarketInfo, SimTrade

# ── Schema ───────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monitored_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    address     TEXT UNIQUE NOT NULL,
    nickname    TEXT,
    weight      REAL DEFAULT 1.0,
    is_active   BOOLEAN DEFAULT 1,
    total_trades INTEGER DEFAULT 0,
    last_trade_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sim_trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id              TEXT UNIQUE NOT NULL,
    target_address        TEXT NOT NULL,
    target_nickname       TEXT,

    -- Market
    market_id             TEXT NOT NULL,
    market_name           TEXT,
    condition_id          TEXT,
    event_slug            TEXT,

    -- Target trade
    target_side           TEXT NOT NULL,
    target_price          REAL NOT NULL,
    target_size           REAL NOT NULL,
    target_timestamp      INTEGER NOT NULL,
    target_execution_time INTEGER,
    target_pnl            REAL,

    -- Simulation
    sim_delay             INTEGER NOT NULL,
    sim_price             REAL,
    sim_delayed_price     REAL,
    sim_investment        REAL DEFAULT 100.0,
    sim_fee               REAL,
    sim_success           BOOLEAN DEFAULT 1,
    sim_failure_reason    TEXT,

    -- Cost
    slippage_pct          REAL,
    total_cost            REAL,

    -- Status
    status                TEXT DEFAULT 'OPEN',
    settlement_price      REAL,
    pnl                   REAL,
    pnl_pct               REAL,

    -- Timestamps
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settled_at            TIMESTAMP,

    FOREIGN KEY (target_address) REFERENCES monitored_accounts(address)
);

CREATE INDEX IF NOT EXISTS idx_sim_trades_target    ON sim_trades(target_address);
CREATE INDEX IF NOT EXISTS idx_sim_trades_market    ON sim_trades(market_id);
CREATE INDEX IF NOT EXISTS idx_sim_trades_status    ON sim_trades(status);
CREATE INDEX IF NOT EXISTS idx_sim_trades_timestamp ON sim_trades(target_timestamp);

CREATE TABLE IF NOT EXISTS market_cache (
    condition_id     TEXT PRIMARY KEY,
    market_id        TEXT,
    market_name      TEXT,
    event_slug       TEXT,
    end_date         TIMESTAMP,
    is_active        BOOLEAN DEFAULT 1,
    is_resolved      BOOLEAN DEFAULT 0,
    resolution_price REAL,
    cache_ttl        INTEGER DEFAULT 3600,
    last_updated     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_market_cache_active ON market_cache(is_active, is_resolved);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metric_type  TEXT NOT NULL,
    metric_value REAL NOT NULL,
    metadata     TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_metrics_type_time ON metrics(metric_type, timestamp);

CREATE TABLE IF NOT EXISTS notification_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT NOT NULL,
    channel       TEXT NOT NULL,
    message       TEXT,
    success       BOOLEAN DEFAULT 1,
    retry_count   INTEGER DEFAULT 0,
    error_message TEXT,
    sent_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """Async SQLite database with cache-aware market lookups."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self._db_path = config.path
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ────────────────────────────────────────

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        if self.config.auto_vacuum:
            await self._db.execute("PRAGMA auto_vacuum = INCREMENTAL")
        logger.info(f"Database connected: {self._db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            logger.info("Database closed")

    # ── Monitored Accounts ───────────────────────────────

    async def upsert_account(self, address: str, nickname: str, weight: float = 1.0) -> None:
        await self._db.execute(
            """
            INSERT INTO monitored_accounts (address, nickname, weight)
            VALUES (?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                nickname = excluded.nickname,
                weight = excluded.weight,
                updated_at = CURRENT_TIMESTAMP
            """,
            (address, nickname, weight),
        )
        await self._db.commit()

    # ── Sim Trades ───────────────────────────────────────

    async def insert_sim_trade(self, trade: SimTrade) -> None:
        d = trade.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        await self._db.execute(
            f"INSERT OR IGNORE INTO sim_trades ({cols}) VALUES ({placeholders})",
            tuple(d.values()),
        )
        await self._db.commit()

        # Bump account trade count
        await self._db.execute(
            """
            UPDATE monitored_accounts
            SET total_trades = total_trades + 1,
                last_trade_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE address = ?
            """,
            (trade.target_address,),
        )
        await self._db.commit()

    async def trade_exists(self, trade_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM sim_trades WHERE trade_id = ?", (trade_id,)
        ) as cur:
            return (await cur.fetchone()) is not None

    async def get_open_trades(self) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM sim_trades WHERE status = 'OPEN' ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_all_trades(self) -> list[dict[str, Any]]:
        """Return all sim trades (OPEN, FAILED, SETTLED)."""
        async with self._db.execute("SELECT * FROM sim_trades ORDER BY created_at") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def settle_trade(
        self,
        trade_id: str,
        settlement_price: float,
        pnl: float,
        pnl_pct: float,
    ) -> None:
        await self._db.execute(
            """
            UPDATE sim_trades
            SET status = 'SETTLED',
                settlement_price = ?,
                pnl = ?,
                pnl_pct = ?,
                settled_at = CURRENT_TIMESTAMP
            WHERE trade_id = ?
            """,
            (settlement_price, pnl, pnl_pct, trade_id),
        )
        await self._db.commit()

    async def update_target_pnl(self, trade_id: str, target_pnl: float) -> None:
        await self._db.execute(
            "UPDATE sim_trades SET target_pnl = ? WHERE trade_id = ?",
            (target_pnl, trade_id),
        )
        await self._db.commit()

    # ── Market Cache ─────────────────────────────────────

    async def get_cached_market(self, condition_id: str) -> dict[str, Any] | None:
        """Return market info if cache is still valid, else None."""
        async with self._db.execute(
            "SELECT * FROM market_cache WHERE condition_id = ?",
            (condition_id,),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None

            row_dict = dict(row)
            last_ts = row_dict.get("last_updated", "")
            ttl = row_dict.get("cache_ttl", self.config.market_cache_ttl)

            # Check TTL
            if last_ts:
                from datetime import datetime

                try:
                    updated = datetime.fromisoformat(last_ts)
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=UTC)
                    age = (datetime.now(UTC) - updated).total_seconds()
                    if age > ttl:
                        return None  # expired
                except (ValueError, TypeError):
                    return None

            return row_dict

    async def upsert_market_cache(self, market: MarketInfo) -> None:
        await self._db.execute(
            """
            INSERT INTO market_cache
                (condition_id, market_id, market_name, event_slug,
                 end_date, is_active, is_resolved, resolution_price,
                 cache_ttl, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(condition_id) DO UPDATE SET
                market_name = excluded.market_name,
                event_slug = excluded.event_slug,
                end_date = excluded.end_date,
                is_active = excluded.is_active,
                is_resolved = excluded.is_resolved,
                resolution_price = excluded.resolution_price,
                last_updated = CURRENT_TIMESTAMP
            """,
            (
                market.condition_id,
                market.market_id,
                market.market_name,
                market.event_slug,
                market.end_date,
                market.is_active,
                market.is_resolved,
                market.resolution_price,
                market.cache_ttl,
            ),
        )
        await self._db.commit()

    async def mark_market_resolved(self, condition_id: str, resolution_price: float) -> None:
        await self._db.execute(
            """
            UPDATE market_cache
            SET is_resolved = 1, is_active = 0,
                resolution_price = ?, last_updated = CURRENT_TIMESTAMP
            WHERE condition_id = ?
            """,
            (resolution_price, condition_id),
        )
        await self._db.commit()

    async def get_active_market_ids(self) -> list[str]:
        async with self._db.execute(
            "SELECT condition_id FROM market_cache WHERE is_active = 1 AND is_resolved = 0"
        ) as cur:
            rows = await cur.fetchall()
            return [r["condition_id"] for r in rows]

    # ── Metrics ──────────────────────────────────────────

    async def insert_metric(
        self, metric_type: str, value: float, metadata: dict | None = None
    ) -> None:
        meta_str = json.dumps(metadata) if metadata else None
        await self._db.execute(
            "INSERT INTO metrics (metric_type, metric_value, metadata) VALUES (?, ?, ?)",
            (metric_type, value, meta_str),
        )
        await self._db.commit()

    # ── Notification History ─────────────────────────────

    async def log_notification(
        self,
        event_type: str,
        channel: str,
        message: str,
        success: bool = True,
        retry_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO notification_history
                (event_type, channel, message, success, retry_count, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_type, channel, message, success, retry_count, error_message),
        )
        await self._db.commit()

    # ── Statistics ───────────────────────────────────────

    async def get_statistics(self) -> AccountStats:
        stats = AccountStats()

        async with self._db.execute("SELECT COUNT(*) as c FROM sim_trades") as cur:
            row = await cur.fetchone()
            stats.total_trades = row["c"]

        async with self._db.execute(
            "SELECT COUNT(*) as c FROM sim_trades WHERE status = 'OPEN'"
        ) as cur:
            row = await cur.fetchone()
            stats.open_positions = row["c"]

        async with self._db.execute(
            "SELECT COUNT(*) as c FROM sim_trades WHERE status = 'SETTLED'"
        ) as cur:
            row = await cur.fetchone()
            stats.settled_trades = row["c"]

        async with self._db.execute(
            "SELECT COUNT(*) as c FROM sim_trades WHERE status = 'FAILED'"
        ) as cur:
            row = await cur.fetchone()
            stats.failed_trades = row["c"]

        async with self._db.execute(
            "SELECT COALESCE(SUM(pnl), 0) as s FROM sim_trades WHERE status = 'SETTLED'"
        ) as cur:
            row = await cur.fetchone()
            stats.total_pnl = row["s"]

        async with self._db.execute(
            """SELECT COALESCE(
                CAST(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS REAL)
                / NULLIF(COUNT(*), 0) * 100, 0
            ) as wr
            FROM sim_trades WHERE status = 'SETTLED'"""
        ) as cur:
            row = await cur.fetchone()
            stats.win_rate = row["wr"]

        async with self._db.execute(
            "SELECT COALESCE(AVG(ABS(slippage_pct)), 0) as a FROM sim_trades WHERE slippage_pct IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
            stats.avg_slippage = row["a"]

        async with self._db.execute("SELECT COALESCE(AVG(sim_fee), 0) as a FROM sim_trades") as cur:
            row = await cur.fetchone()
            stats.avg_fee = row["a"]

        async with self._db.execute(
            "SELECT COALESCE(SUM(sim_investment), 0) as s FROM sim_trades WHERE sim_success = 1"
        ) as cur:
            row = await cur.fetchone()
            stats.total_investment = row["s"]

        async with self._db.execute(
            "SELECT COALESCE(SUM(sim_investment), 0) as s FROM sim_trades"
        ) as cur:
            row = await cur.fetchone()
            stats.total_simulated = row["s"]

        async with self._db.execute(
            "SELECT COALESCE(MAX(pnl), 0) as m FROM sim_trades WHERE status = 'SETTLED'"
        ) as cur:
            row = await cur.fetchone()
            stats.best_trade_pnl = row["m"]

        async with self._db.execute(
            "SELECT COALESCE(MIN(pnl), 0) as m FROM sim_trades WHERE status = 'SETTLED'"
        ) as cur:
            row = await cur.fetchone()
            stats.worst_trade_pnl = row["m"]

        return stats

    # ── PnL Summary Query ────────────────────────────────

    async def get_pnl_summary(self, target_address: str | None = None) -> list[dict[str, Any]]:
        """Grouped PnL summary by target + delay."""
        where = ""
        params: tuple = ()
        if target_address:
            where = "WHERE target_address = ?"
            params = (target_address,)

        query = f"""
            SELECT
                target_nickname,
                sim_delay,
                COUNT(*) as trade_count,
                SUM(CASE WHEN sim_success = 1 THEN 1 ELSE 0 END) as success_count,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(slippage_pct), 0) as avg_slippage,
                COALESCE(AVG(sim_fee), 0) as avg_fee,
                COALESCE(
                    CAST(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS REAL)
                    / NULLIF(SUM(CASE WHEN status = 'SETTLED' THEN 1 ELSE 0 END), 0) * 100,
                    0
                ) as win_rate
            FROM sim_trades
            {where}
            GROUP BY target_nickname, sim_delay
            ORDER BY target_nickname, sim_delay
        """
        async with self._db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
