"""Periodic metrics collector – logs key performance indicators."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque

from loguru import logger

from src.config.models import LoggingConfig
from src.data.database import Database


class MetricsCollector:
    """Collects runtime metrics and logs them at a fixed interval.

    Metrics are written to the ``metrics.log`` sink and optionally
    persisted to the ``metrics`` table in the database.
    """

    def __init__(
        self,
        config: LoggingConfig,
        db: Database | None = None,
    ) -> None:
        self.config = config
        self.db = db

        # Ring buffers for recent values
        self._api_latency: deque[float] = deque(maxlen=1000)
        self._slippage: deque[float] = deque(maxlen=1000)
        self._pnl: deque[float] = deque(maxlen=1000)
        self._notif_success: deque[int] = deque(maxlen=200)

        # Simple counters
        self.active_accounts: int = 0
        self.total_trades: int = 0
        self.failed_requests: int = 0
        self.polls_completed: int = 0

    # ── Recording ────────────────────────────────────────

    def record_api_latency(self, seconds: float) -> None:
        self._api_latency.append(seconds)

    def record_slippage(self, pct: float) -> None:
        self._slippage.append(pct)

    def record_pnl(self, value: float) -> None:
        self._pnl.append(value)

    def record_notification(self, success: bool) -> None:
        self._notif_success.append(1 if success else 0)

    def increment_failed_requests(self) -> None:
        self.failed_requests += 1

    # ── Periodic logging ─────────────────────────────────

    async def run(self) -> None:
        """Log metrics at the configured interval."""
        interval = self.config.metrics_interval
        logger.info(f"Metrics collector started (interval={interval}s)")
        while True:
            await asyncio.sleep(interval)
            await self._emit()

    async def _emit(self) -> None:
        snapshot = {
            "ts": time.time(),
            "active_accounts": self.active_accounts,
            "total_trades": self.total_trades,
            "polls_completed": self.polls_completed,
            "failed_requests": self.failed_requests,
            "avg_api_latency_ms": round(self._avg(self._api_latency) * 1000, 1),
            "avg_slippage_pct": round(self._avg(self._slippage), 3),
            "recent_pnl_sum": round(sum(self._pnl), 2),
            "notification_success_rate": round(self._avg(self._notif_success) * 100, 1),
        }

        # Emit to metrics log via loguru
        logger.bind(metrics=True).info(json.dumps(snapshot))

        # Persist to DB
        if self.db:
            try:
                await self.db.insert_metric("system_snapshot", 0, snapshot)
            except Exception:
                logger.debug("Failed to persist metrics snapshot")

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _avg(buf: deque[float]) -> float:
        return sum(buf) / len(buf) if buf else 0.0
