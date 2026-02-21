"""Notification manager – aggregated queue with retry logic.

Collects events, batches them at a configurable interval, and dispatches
through all enabled channels (Telegram, iMessage, etc.) with exponential
back-off retries.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from loguru import logger

from src.config.models import NotificationsConfig
from src.data.database import Database

# ── Event types ──────────────────────────────────────────


class EventType:
    NEW_TRADE = "NEW_TRADE_DETECTED"
    SIM_EXECUTED = "SIM_TRADE_EXECUTED"
    SIM_FAILED = "SIM_TRADE_FAILED"
    MARKET_SETTLED = "MARKET_SETTLED"
    DAILY_SUMMARY = "DAILY_SUMMARY"
    ERROR_ALERT = "ERROR_ALERT"


# ── Base channel ─────────────────────────────────────────


class NotificationChannel(ABC):
    """Abstract notification channel."""

    name: str = "base"

    @abstractmethod
    async def send(self, message: str) -> bool:
        """Send a message.  Returns True on success."""
        ...


# ── Manager ──────────────────────────────────────────────


class NotificationManager:
    """Aggregates events and dispatches through registered channels."""

    def __init__(
        self,
        config: NotificationsConfig,
        db: Database | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self._channels: list[NotificationChannel] = []
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._running = False

    # ── Channel registration ─────────────────────────────

    def register_channel(self, channel: NotificationChannel) -> None:
        self._channels.append(channel)
        logger.info(f"Notification channel registered: {channel.name}")

    # ── Enqueue ──────────────────────────────────────────

    async def notify(self, event_type: str, data: dict[str, Any]) -> None:
        """Enqueue an event for batched delivery."""
        if not self.config.enabled:
            return
        await self._queue.put(
            {
                "event_type": event_type,
                "data": data,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    # ── Aggregation loop ─────────────────────────────────

    async def run(self) -> None:
        """Main loop: drain the queue every *aggregation_interval* seconds
        and send batched messages."""
        self._running = True
        interval = self.config.aggregation_interval
        logger.info(
            f"Notification manager started (interval={interval}s, channels={len(self._channels)})"
        )

        while self._running:
            await asyncio.sleep(interval)
            await self._flush()

    async def stop(self) -> None:
        self._running = False
        await self._flush()

    async def _flush(self) -> None:
        """Drain the queue and send aggregated message."""
        events: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not events:
            return

        message = self._format_batch(events)
        for channel in self._channels:
            await self._send_with_retry(channel, message, events)

    # ── Retry ────────────────────────────────────────────

    async def _send_with_retry(
        self,
        channel: NotificationChannel,
        message: str,
        events: list[dict[str, Any]],
    ) -> None:
        backoff = self.config.retry_backoff
        attempts = self.config.max_retries + 1
        last_error: str | None = None

        for attempt in range(attempts):
            try:
                ok = await channel.send(message)
                if ok:
                    if self.db:
                        for ev in events:
                            await self.db.log_notification(
                                event_type=ev["event_type"],
                                channel=channel.name,
                                message=message[:500],
                                success=True,
                                retry_count=attempt,
                            )
                    return
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f"[{channel.name}] send failed (attempt {attempt + 1}): {exc}")

            if attempt < len(backoff):
                await asyncio.sleep(backoff[attempt])
            elif backoff:
                await asyncio.sleep(backoff[-1])

        # All retries exhausted
        logger.error(f"[{channel.name}] all retries exhausted: {last_error}")
        if self.db:
            for ev in events:
                await self.db.log_notification(
                    event_type=ev["event_type"],
                    channel=channel.name,
                    message=message[:500],
                    success=False,
                    retry_count=attempts,
                    error_message=last_error,
                )

    # ── Formatting ───────────────────────────────────────

    def _format_batch(self, events: list[dict[str, Any]]) -> str:
        """Turn a batch of events into a human-readable message."""
        lines = [f"=== Polymarket Copy Trader ({len(events)} events) ==="]

        # Group by type
        by_type: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            by_type.setdefault(ev["event_type"], []).append(ev)

        for etype, evts in by_type.items():
            lines.append(f"\n[{etype}] x{len(evts)}")
            for ev in evts[:5]:  # cap preview at 5
                data = ev.get("data", {})
                if etype == EventType.NEW_TRADE:
                    lines.append(
                        f"  {data.get('nickname', '?')}: "
                        f"{data.get('side', '?')} {data.get('title', '?')} "
                        f"@ {data.get('price', '?')}"
                    )
                elif etype == EventType.SIM_EXECUTED:
                    lines.append(
                        f"  delay={data.get('delay', '?')}s "
                        f"price={data.get('sim_price', '?')} "
                        f"slip={data.get('slippage', '?')}%"
                    )
                elif etype == EventType.MARKET_SETTLED:
                    lines.append(f"  {data.get('market', '?')} -> PnL ${data.get('pnl', '?')}")
                else:
                    lines.append(f"  {data}")

            if len(evts) > 5:
                lines.append(f"  ... and {len(evts) - 5} more")

        return "\n".join(lines)
