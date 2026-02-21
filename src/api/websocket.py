"""Async WebSocket client for real-time Polymarket data."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

import websockets
from loguru import logger

from src.config.models import APIConfig

# Type alias for message handlers
MessageHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class PolymarketWebSocket:
    """Async WebSocket client with auto-reconnect and heartbeat.

    Supports the Market channel (public, no auth) and subscribes
    to a set of asset IDs to receive real-time orderbook/trade updates.
    """

    HEARTBEAT_INTERVAL = 10  # seconds
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_BASE_DELAY = 5  # seconds

    def __init__(
        self,
        api_config: APIConfig,
        channel: str = "market",
    ) -> None:
        self.config = api_config
        self.channel = channel
        self._url = api_config.websocket_urls.get(
            channel, "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        )
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._handlers: List[MessageHandler] = []
        self._subscribed_assets: List[str] = []
        self._running = False
        self._reconnect_count = 0

    # ── Public API ───────────────────────────────────────

    def on_message(self, handler: MessageHandler) -> None:
        """Register a coroutine to handle incoming messages."""
        self._handlers.append(handler)

    async def subscribe(self, asset_ids: List[str]) -> None:
        """Subscribe to asset IDs (market channel)."""
        self._subscribed_assets = list(set(self._subscribed_assets + asset_ids))
        if self._ws and self._ws.open:
            await self._send_subscription()

    async def unsubscribe(self, asset_ids: List[str]) -> None:
        """Unsubscribe from asset IDs."""
        self._subscribed_assets = [
            a for a in self._subscribed_assets if a not in asset_ids
        ]

    async def run(self) -> None:
        """Main loop: connect, subscribe, listen, reconnect on failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except (
                websockets.ConnectionClosed,
                websockets.ConnectionClosedError,
                ConnectionError,
                OSError,
            ) as exc:
                self._reconnect_count += 1
                if self._reconnect_count > self.MAX_RECONNECT_ATTEMPTS:
                    logger.error(
                        f"WebSocket: {self._reconnect_count} consecutive failures – pausing"
                    )
                    self._running = False
                    break

                delay = self.RECONNECT_BASE_DELAY * self._reconnect_count
                logger.warning(
                    f"WebSocket disconnected ({exc}), "
                    f"reconnecting in {delay}s (attempt {self._reconnect_count})"
                )
                await asyncio.sleep(delay)

    async def stop(self) -> None:
        """Gracefully stop the WebSocket loop."""
        self._running = False
        if self._ws and self._ws.open:
            await self._ws.close()

    # ── Private ──────────────────────────────────────────

    async def _connect_and_listen(self) -> None:
        """Single connection lifecycle."""
        logger.info(f"WebSocket connecting to {self._url}")
        async with websockets.connect(self._url, ping_interval=None) as ws:
            self._ws = ws
            self._reconnect_count = 0  # reset on successful connect
            logger.info("WebSocket connected")

            # Subscribe
            if self._subscribed_assets:
                await self._send_subscription()

            # Run listener and heartbeat concurrently
            listener_task = asyncio.create_task(self._listen())
            heartbeat_task = asyncio.create_task(self._heartbeat())

            try:
                done, pending = await asyncio.wait(
                    {listener_task, heartbeat_task},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in done:
                    task.result()  # propagate exception
            finally:
                for task in [listener_task, heartbeat_task]:
                    if not task.done():
                        task.cancel()

    async def _send_subscription(self) -> None:
        """Send subscription message for the market channel."""
        if not self._ws or not self._ws.open:
            return

        if self.channel == "market":
            msg = {
                "assets_ids": self._subscribed_assets,
                "type": "market",
                "custom_feature_enabled": True,
            }
        elif self.channel == "user":
            msg = {
                "auth": {
                    "apiKey": os.getenv("POLYMARKET_API_KEY", ""),
                    "secret": os.getenv("POLYMARKET_SECRET", ""),
                    "passphrase": os.getenv("POLYMARKET_PASSPHRASE", ""),
                },
                "markets": self._subscribed_assets,
                "type": "user",
            }
        else:
            logger.error(f"Unknown channel: {self.channel}")
            return

        await self._ws.send(json.dumps(msg))
        logger.info(
            f"WebSocket subscribed to {len(self._subscribed_assets)} assets "
            f"on '{self.channel}' channel"
        )

    async def _listen(self) -> None:
        """Read messages and dispatch to handlers."""
        async for raw in self._ws:
            try:
                data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
            except json.JSONDecodeError:
                logger.warning(f"WebSocket: unparseable message: {raw[:200]}")
                continue

            for handler in self._handlers:
                try:
                    await handler(data)
                except Exception:
                    logger.exception("WebSocket handler error")

    async def _heartbeat(self) -> None:
        """Send periodic PING to keep the connection alive."""
        while self._running and self._ws and self._ws.open:
            try:
                await self._ws.ping()
                logger.debug("WebSocket PING sent")
            except Exception:
                break
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
