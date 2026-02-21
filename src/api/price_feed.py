"""Real-time crypto price feed via OKX WebSocket.

Maintains a persistent WSS connection to OKX public ticker stream,
providing millisecond-level price updates for BTC, ETH, SOL etc.

Replaces CoinGecko REST API (minutes-stale) with live data suitable
for correlation scoring against Polymarket whale trades.

Usage::

    feed = PriceFeed()
    feed.start()  # launches background task
    price = feed.get("BTC")          # latest price
    change = feed.momentum("ETH", 1) # 1-second momentum %
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field

import aiohttp
from loguru import logger

# ── Data models ──────────────────────────────────────────


@dataclass
class PriceTick:
    """Single price observation."""

    price: float
    timestamp: float  # monotonic
    source: str = "OKX"


@dataclass
class PriceState:
    """Current state for one symbol."""

    latest: float = 0.0
    updated_at: float = 0.0
    history: list[tuple[float, float]] = field(default_factory=list)  # (ts, price)

    def record(self, price: float) -> None:
        now = time.monotonic()
        self.latest = price
        self.updated_at = now
        self.history.append((now, price))
        # Keep last 5 minutes only
        cutoff = now - 300
        if len(self.history) > 500:
            self.history = [(t, p) for t, p in self.history if t > cutoff]


# Symbol mapping: internal name -> OKX instId
OKX_SYMBOLS = {
    "BTC": "BTC-USDT",
    "ETH": "ETH-USDT",
    "SOL": "SOL-USDT",
}

OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"


class PriceFeed:
    """Persistent WebSocket price feed from OKX.

    Provides:
      - get(symbol): latest price
      - momentum(symbol, seconds): price change % in window
      - is_fresh(symbol, max_age_s): whether data is recent enough
    """

    def __init__(self, symbols: list[str] | None = None) -> None:
        self._symbols = symbols or list(OKX_SYMBOLS.keys())
        self._state: dict[str, PriceState] = defaultdict(PriceState)
        self._running = False
        self._task: asyncio.Task | None = None
        self._connected = False

    # ── Public API ────────────────────────────────────────

    def get(self, symbol: str) -> float | None:
        """Get latest price for symbol (e.g. 'BTC'). None if no data."""
        state = self._state.get(symbol)
        if state and state.latest > 0:
            return state.latest
        return None

    def get_all(self) -> dict[str, float]:
        """Get all latest prices."""
        return {
            sym: st.latest
            for sym, st in self._state.items()
            if st.latest > 0
        }

    def momentum(self, symbol: str, seconds: float = 1.0) -> float | None:
        """Price change % over the last N seconds.

        Returns None if insufficient data.
        Example: 0.15 means +0.15% price increase.
        """
        state = self._state.get(symbol)
        if not state or not state.history or state.latest <= 0:
            return None

        now = time.monotonic()
        cutoff = now - seconds

        # Find oldest price in the window
        old_price = None
        for ts, price in state.history:
            if ts >= cutoff:
                old_price = price
                break

        if old_price is None or old_price <= 0:
            return None

        return round((state.latest - old_price) / old_price * 100, 4)

    def is_fresh(self, symbol: str, max_age_s: float = 5.0) -> bool:
        """Check if price data is recent enough."""
        state = self._state.get(symbol)
        if not state or state.updated_at == 0:
            return False
        return (time.monotonic() - state.updated_at) < max_age_s

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Lifecycle ─────────────────────────────────────────

    def start(self) -> asyncio.Task:
        """Start the WebSocket feed as a background task."""
        self._running = True
        self._task = asyncio.create_task(self._run_forever(), name="price_feed")
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    # ── WebSocket loop ────────────────────────────────────

    async def _run_forever(self) -> None:
        """Reconnecting WebSocket loop."""
        logger.info(
            f"PriceFeed starting: {self._symbols} via OKX WSS"
        )
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                logger.warning(f"PriceFeed disconnected: {e}, reconnecting in 3s")
                await asyncio.sleep(3)

    async def _connect_and_listen(self) -> None:
        """Single WebSocket session."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.ws_connect(
                OKX_WS_URL,
                heartbeat=15,
                receive_timeout=30,
            ) as ws:
                self._connected = True
                logger.info("PriceFeed connected to OKX WSS")

                # Subscribe to tickers
                args = [
                    {"channel": "tickers", "instId": OKX_SYMBOLS[s]}
                    for s in self._symbols
                    if s in OKX_SYMBOLS
                ]
                await ws.send_str(json.dumps({
                    "op": "subscribe",
                    "args": args,
                }))
                logger.info(
                    f"PriceFeed subscribed to {len(args)} symbols"
                )

                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_message(msg.data)
                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break

                self._connected = False

    def _handle_message(self, raw: str) -> None:
        """Parse OKX ticker message and update state."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # OKX format: {"arg":{"channel":"tickers","instId":"BTC-USDT"},"data":[{...}]}
        if "data" not in data:
            return

        inst_id = data.get("arg", {}).get("instId", "")

        # Reverse lookup: OKX instId -> our symbol
        symbol = None
        for sym, oid in OKX_SYMBOLS.items():
            if oid == inst_id:
                symbol = sym
                break

        if not symbol:
            return

        for tick in data.get("data", []):
            try:
                price = float(tick.get("last", 0))
                if price > 0:
                    self._state[symbol].record(price)
            except (TypeError, ValueError):
                pass
