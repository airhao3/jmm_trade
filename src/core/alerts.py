"""Advanced alert engine – arbitrage & momentum shift detection.

Runs as a background task alongside the main polling loop:
  1. Pair-cost arbitrage: YES + NO < threshold → risk-free profit opportunity
  2. Momentum shift: rapid price movement in a short window
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient

# ── Data models ──────────────────────────────────────────


@dataclass
class ArbitrageOpportunity:
    """Detected when YES + NO price < threshold."""

    market_title: str = ""
    condition_id: str = ""
    yes_price: float = 0.0
    no_price: float = 0.0
    combined: float = 0.0
    profit_pct: float = 0.0
    yes_token: str = ""
    no_token: str = ""


@dataclass
class MomentumShift:
    """Detected when price moves significantly in short time."""

    market_title: str = ""
    token_id: str = ""
    price_before: float = 0.0
    price_after: float = 0.0
    change_pct: float = 0.0
    window_seconds: int = 60
    direction: str = ""  # SURGE or DUMP


# ── Alert formatter ──────────────────────────────────────


def format_arbitrage_alert(arb: ArbitrageOpportunity) -> str:
    """Format arbitrage opportunity as notification text."""
    return (
        f"💎 无风险套利机会 (Pair Arbitrage)\n"
        f"\n"
        f"📊 市场: {arb.market_title}\n"
        f"🟢 YES: ${arb.yes_price:.4f}\n"
        f"🔴 NO:  ${arb.no_price:.4f}\n"
        f"📐 YES + NO = ${arb.combined:.4f}\n"
        f"💰 理论利润: {arb.profit_pct:.2f}%\n"
        f"\n"
        f"⚡ 买入 YES + NO 后结算必得 $1.00，锁定利润"
    )


def format_momentum_alert(shift: MomentumShift) -> str:
    """Format momentum shift as notification text."""
    emoji = "🚀" if shift.direction == "SURGE" else "📉"
    return (
        f"{emoji} 情绪突变预警 (Momentum Shift)\n"
        f"\n"
        f"📊 市场: {shift.market_title}\n"
        f"💲 价格变化: ${shift.price_before:.4f} → ${shift.price_after:.4f}"
        f" ({shift.change_pct:+.1f}%)\n"
        f"⏱ 时间窗口: {shift.window_seconds}s\n"
        f"\n"
        f"⚠️ 可能有突发新闻或大额交易驱动"
    )


# ── Alert Engine ─────────────────────────────────────────


class AlertEngine:
    """Background engine for advanced market alerts."""

    def __init__(
        self,
        api: PolymarketClient,
        arb_threshold: float = 0.98,
        momentum_pct: float = 15.0,
        momentum_window: int = 60,
        scan_interval: int = 30,
    ) -> None:
        self.api = api
        self.arb_threshold = arb_threshold
        self.momentum_pct = momentum_pct
        self.momentum_window = momentum_window
        self.scan_interval = scan_interval

        # Price history: token_id -> [(timestamp, price), ...]
        self._price_history: dict[str, list[tuple[float, float]]] = defaultdict(list)
        # Track already-alerted to avoid spam
        self._alerted_arbs: set[str] = set()
        self._alerted_momentum: set[str] = set()
        # Callbacks
        self._on_arbitrage: list[Any] = []
        self._on_momentum: list[Any] = []
        # Tracked token pairs: condition_id -> (yes_token, no_token, title)
        self._tracked_pairs: dict[str, tuple[str, str, str]] = {}

    def on_arbitrage(self, callback: Any) -> None:
        """Register callback for arbitrage opportunities."""
        self._on_arbitrage.append(callback)

    def on_momentum(self, callback: Any) -> None:
        """Register callback for momentum shifts."""
        self._on_momentum.append(callback)

    def track_market(
        self,
        condition_id: str,
        yes_token: str,
        no_token: str,
        title: str,
    ) -> None:
        """Add a market pair for arbitrage/momentum scanning."""
        self._tracked_pairs[condition_id] = (yes_token, no_token, title)

    def update_price(self, token_id: str, price: float) -> None:
        """Record a price observation (called from WebSocket or poll)."""
        now = time.monotonic()
        history = self._price_history[token_id]
        history.append((now, price))
        # Keep only last 5 minutes
        cutoff = now - 300
        self._price_history[token_id] = [
            (t, p) for t, p in history if t > cutoff
        ]

    async def run(self) -> None:
        """Main scanning loop."""
        logger.info(
            f"Alert engine started: arb_threshold={self.arb_threshold}, "
            f"momentum={self.momentum_pct}%, interval={self.scan_interval}s"
        )
        while True:
            try:
                await self._scan_cycle()
            except Exception:
                logger.exception("Alert engine scan error")
            await asyncio.sleep(self.scan_interval)

    async def _scan_cycle(self) -> None:
        """Run one scan for arbitrage and momentum."""
        # ── Arbitrage scan ────────────────────────────
        for cid, (yes_tok, no_tok, title) in self._tracked_pairs.items():
            try:
                await self._check_arbitrage(cid, yes_tok, no_tok, title)
            except Exception as e:
                logger.debug(f"Arb check error for {cid}: {e}")

        # ── Momentum scan ─────────────────────────────
        for token_id, history in self._price_history.items():
            if len(history) < 2:
                continue
            try:
                self._check_momentum(token_id, history)
            except Exception as e:
                logger.debug(f"Momentum check error for {token_id}: {e}")

    async def _check_arbitrage(
        self,
        condition_id: str,
        yes_token: str,
        no_token: str,
        title: str,
    ) -> None:
        """Check if YES + NO < threshold for a market."""
        if condition_id in self._alerted_arbs:
            return

        # Fetch both prices concurrently
        yes_book, no_book = await asyncio.gather(
            self.api.get_orderbook(yes_token),
            self.api.get_orderbook(no_token),
            return_exceptions=True,
        )

        if isinstance(yes_book, Exception) or isinstance(no_book, Exception):
            return

        yes_price = self._best_ask(yes_book)
        no_price = self._best_ask(no_book)

        if yes_price is None or no_price is None:
            return

        combined = yes_price + no_price
        if combined < self.arb_threshold:
            profit_pct = round((1.0 - combined) / combined * 100, 2)
            arb = ArbitrageOpportunity(
                market_title=title,
                condition_id=condition_id,
                yes_price=yes_price,
                no_price=no_price,
                combined=combined,
                profit_pct=profit_pct,
                yes_token=yes_token,
                no_token=no_token,
            )
            self._alerted_arbs.add(condition_id)
            logger.info(
                f"ARBITRAGE: {title} YES+NO={combined:.4f} "
                f"profit={profit_pct:.2f}%"
            )
            for cb in self._on_arbitrage:
                try:
                    await cb(arb)
                except Exception:
                    logger.exception("Arbitrage callback error")

    def _check_momentum(
        self,
        token_id: str,
        history: list[tuple[float, float]],
    ) -> None:
        """Check for significant price movement in window."""
        now = time.monotonic()
        cutoff = now - self.momentum_window

        # Find oldest price within the window
        window_prices = [(t, p) for t, p in history if t >= cutoff]
        if len(window_prices) < 2:
            return

        oldest_price = window_prices[0][1]
        newest_price = window_prices[-1][1]

        if oldest_price <= 0:
            return

        change_pct = ((newest_price - oldest_price) / oldest_price) * 100

        if abs(change_pct) < self.momentum_pct:
            return

        # Deduplicate: one alert per token per 5 minutes
        alert_key = f"{token_id}_{int(now // 300)}"
        if alert_key in self._alerted_momentum:
            return
        self._alerted_momentum.add(alert_key)

        direction = "SURGE" if change_pct > 0 else "DUMP"

        # Find market title for this token
        title = ""
        for _, (yt, nt, t) in self._tracked_pairs.items():
            if yt == token_id or nt == token_id:
                title = t
                break

        shift = MomentumShift(
            market_title=title or token_id[:20],
            token_id=token_id,
            price_before=oldest_price,
            price_after=newest_price,
            change_pct=round(change_pct, 1),
            window_seconds=self.momentum_window,
            direction=direction,
        )

        logger.info(
            f"MOMENTUM {direction}: {title or token_id[:20]} "
            f"${oldest_price:.4f}→${newest_price:.4f} ({change_pct:+.1f}%)"
        )

        for cb in self._on_momentum:
            try:
                asyncio.get_event_loop().create_task(cb(shift))
            except Exception:
                logger.exception("Momentum callback error")

    @staticmethod
    def _best_ask(book: dict) -> float | None:
        """Extract best ask price from orderbook."""
        asks = book.get("asks", [])
        if not asks:
            return None
        first = asks[0]
        if isinstance(first, dict):
            return float(first.get("price", 0))
        return float(first[0])
