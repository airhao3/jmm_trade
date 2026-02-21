"""Trade monitor – discovers new trades from target accounts.

Responsibilities:
    1. Poll / WebSocket: detect new trades for each target.
    2. Market filter: pass only matching markets.
    3. Dispatch: hand qualifying trades to the Simulator.

The monitor does NOT perform any simulation logic itself.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from loguru import logger

from src.api.client import PolymarketClient
from src.config.models import AppConfig, MarketFilterConfig, TargetAccount

# Callback signature: (target_account, trade_dict) -> None
TradeCallback = Callable[
    [TargetAccount, Dict[str, Any]], Coroutine[Any, Any, None]
]


class TradeMonitor:
    """Discovers new trades from monitored accounts."""

    def __init__(
        self,
        config: AppConfig,
        api_client: PolymarketClient,
    ) -> None:
        self.config = config
        self.api = api_client

        # Track already-seen trade hashes per address
        self._seen: Dict[str, Set[str]] = {}
        # External callbacks
        self._callbacks: List[TradeCallback] = []
        # Poll statistics
        self._poll_count: int = 0
        self._total_poll_latency: float = 0.0

        # Precompile market filter regex
        self._asset_pattern: Optional[re.Pattern] = None
        self._time_pattern = re.compile(r"\b(\d+)\s*[-–]?\s*(min|minute)", re.I)
        if config.market_filter.enabled:
            asset_alts = "|".join(
                re.escape(a) for a in config.market_filter.assets
            )
            self._asset_pattern = re.compile(asset_alts, re.I)

    # ── Public API ───────────────────────────────────────

    def on_new_trade(self, callback: TradeCallback) -> None:
        """Register a handler called for every new qualifying trade."""
        self._callbacks.append(callback)

    async def poll_once(self) -> tuple[int, float]:
        """Run a single poll cycle across all active targets.

        Returns (new_trade_count, poll_latency_seconds).
        """
        t0 = time.monotonic()
        targets = self.config.get_active_targets()
        tasks = [self._poll_target(t) for t in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_new = 0
        for target, result in zip(targets, results):
            if isinstance(result, Exception):
                logger.error(f"Poll error for {target.nickname}: {result}")
            else:
                total_new += result

        latency = time.monotonic() - t0
        self._poll_count += 1
        self._total_poll_latency += latency
        return total_new, latency

    async def poll_loop(self) -> None:
        """Continuously poll at the configured interval."""
        interval = self.config.monitoring.poll_interval
        logger.info(
            f"Monitor started: polling every {interval}s for "
            f"{len(self.config.get_active_targets())} targets"
        )
        while True:
            try:
                new_count, latency = await self.poll_once()
                if new_count > 0:
                    logger.info(
                        f"Poll #{self._poll_count}: {new_count} new trades "
                        f"({latency*1000:.0f}ms)"
                    )
                elif self._poll_count % 20 == 0:
                    # Log every 20th poll for heartbeat visibility
                    avg = (self._total_poll_latency / self._poll_count * 1000) if self._poll_count else 0
                    logger.info(
                        f"Poll #{self._poll_count}: no new trades "
                        f"({latency*1000:.0f}ms, avg={avg:.0f}ms)"
                    )
            except Exception:
                logger.exception("Unhandled error in poll cycle")
            await asyncio.sleep(interval)

    # ── Internals ────────────────────────────────────────

    async def _poll_target(self, target: TargetAccount) -> int:
        """Fetch recent trades for one target and dispatch new ones."""
        trades = await self.api.get_trades(target.address, limit=50)

        if target.address not in self._seen:
            # First poll: seed with existing trades, don't dispatch
            self._seen[target.address] = {
                t.get("transactionHash", "") for t in trades
            }
            logger.info(
                f"[{target.nickname}] Seeded {len(trades)} existing trades"
            )
            return 0

        new_count = 0
        for trade in trades:
            tx_hash = trade.get("transactionHash", "")
            if not tx_hash or tx_hash in self._seen[target.address]:
                continue

            self._seen[target.address].add(tx_hash)

            if not self._passes_market_filter(trade):
                logger.debug(
                    f"[{target.nickname}] Filtered out: {trade.get('title', '?')}"
                )
                continue

            logger.info(
                f"[{target.nickname}] NEW TRADE: {trade.get('side')} "
                f"{trade.get('title', '?')} @ {trade.get('price')}"
            )

            # Dispatch to all registered callbacks
            for cb in self._callbacks:
                try:
                    await cb(target, trade)
                except Exception:
                    logger.exception("Trade callback error")

            new_count += 1

        return new_count

    def _passes_market_filter(self, trade: Dict[str, Any]) -> bool:
        """Return True if the trade's market matches configured filters."""
        filt = self.config.market_filter
        if not filt.enabled:
            return True

        title = trade.get("title", "")
        if not title:
            return False

        title_lower = title.lower()

        # Asset match
        if self._asset_pattern and not self._asset_pattern.search(title):
            return False

        # Keyword match
        if not any(kw.lower() in title_lower for kw in filt.keywords):
            return False

        # Exclude keywords
        if any(kw.lower() in title_lower for kw in filt.exclude_keywords):
            return False

        # Duration match
        match = self._time_pattern.search(title)
        if match:
            duration = int(match.group(1))
            if not (filt.min_duration_minutes <= duration <= filt.max_duration_minutes):
                return False

        return True
