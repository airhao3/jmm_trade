"""Virtual portfolio – tracks open positions and aggregated stats."""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.data.database import Database
from src.data.models import AccountStats


class Portfolio:
    """Read-only virtual portfolio backed by the database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Return all currently open simulated positions."""
        return await self.db.get_open_trades()

    async def get_statistics(self) -> AccountStats:
        """Aggregate statistics across all trades."""
        return await self.db.get_statistics()

    async def get_pnl_summary(self, target_address: str | None = None) -> list[dict[str, Any]]:
        """Grouped PnL breakdown by target account and delay."""
        return await self.db.get_pnl_summary(target_address)

    async def log_portfolio_snapshot(self) -> None:
        """Log a concise summary of the current portfolio state."""
        stats = await self.get_statistics()
        logger.info(
            f"Portfolio: {stats.total_trades} trades | "
            f"{stats.open_positions} open | "
            f"PnL ${stats.total_pnl:+.2f} | "
            f"Win {stats.win_rate:.1f}% | "
            f"Avg slip {stats.avg_slippage:.2f}%"
        )
