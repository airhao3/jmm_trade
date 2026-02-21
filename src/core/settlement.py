"""Settlement engine – resolves open positions when markets close.

Periodically checks whether markets referenced by OPEN sim_trades have
been resolved.  If so, calculates realised PnL and updates the records.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.config.models import AppConfig
from src.data.database import Database
from src.data.models import MarketInfo


class SettlementEngine:
    """Checks market resolution and settles open simulated trades."""

    def __init__(
        self,
        config: AppConfig,
        api_client: PolymarketClient,
        db: Database,
    ) -> None:
        self.config = config
        self.api = api_client
        self.db = db

    # ── Public API ───────────────────────────────────────

    async def settle_once(self) -> int:
        """Run one settlement pass.  Returns the number of trades settled."""
        open_trades = await self.db.get_open_trades()
        if not open_trades:
            return 0

        # Group by condition_id to minimise API calls
        by_market: dict[str, list[dict[str, Any]]] = {}
        for t in open_trades:
            cid = t.get("condition_id", "")
            by_market.setdefault(cid, []).append(t)

        settled = 0
        for condition_id, trades in by_market.items():
            try:
                resolution = await self._check_resolution(condition_id)
                if resolution is None:
                    continue  # market not yet resolved

                for trade in trades:
                    pnl, pnl_pct = self._calculate_pnl(trade, resolution)
                    await self.db.settle_trade(
                        trade_id=trade["trade_id"],
                        settlement_price=resolution,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                    )
                    settled += 1
                    logger.info(
                        f"Settled {trade['trade_id']}: "
                        f"resolution={resolution:.4f} PnL=${pnl:+.2f} ({pnl_pct:+.1f}%)"
                    )

                # Mark market as resolved in cache
                await self.db.mark_market_resolved(condition_id, resolution)

            except Exception:
                logger.exception(f"Settlement error for market {condition_id}")

        return settled

    async def settlement_loop(self, interval: int = 60) -> None:
        """Periodically attempt to settle open trades."""
        logger.info(f"Settlement engine started (interval={interval}s)")
        while True:
            try:
                n = await self.settle_once()
                if n > 0:
                    logger.info(f"Settlement cycle: {n} trades settled")
            except Exception:
                logger.exception("Unhandled error in settlement loop")
            await asyncio.sleep(interval)

    # ── Private ──────────────────────────────────────────

    async def _check_resolution(self, condition_id: str) -> float | None:
        """Return the resolution price if the market is resolved, else None.

        First checks cache; falls back to API.
        """
        if not condition_id:
            return None

        # Check cache
        cached = await self.db.get_cached_market(condition_id)
        if cached and cached.get("is_resolved"):
            return cached.get("resolution_price")

        # Fetch from Gamma API
        try:
            market_data = await self.api.get_market(condition_id)
        except Exception as exc:
            logger.debug(f"Market fetch failed for {condition_id}: {exc}")
            return None

        # Update cache
        is_resolved = market_data.get("closed", False) or market_data.get("resolved", False)
        resolution_price: float | None = None

        if is_resolved:
            # Polymarket resolution: winning outcome resolves to 1.0, losing to 0.0
            # outcomePrices may be a JSON string or a list
            outcome_prices = market_data.get("outcomePrices", [0])
            if isinstance(outcome_prices, str):
                import json

                try:
                    outcome_prices = json.loads(outcome_prices)
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = [0]
            if outcome_prices:
                resolution_price = float(outcome_prices[0])

        await self.db.upsert_market_cache(
            MarketInfo(
                condition_id=condition_id,
                market_id=market_data.get("slug", ""),
                market_name=market_data.get("question", ""),
                event_slug=market_data.get("eventSlug", ""),
                end_date=market_data.get("endDate"),
                is_active=not is_resolved,
                is_resolved=is_resolved,
                resolution_price=resolution_price,
            )
        )

        return resolution_price if is_resolved else None

    def _calculate_pnl(self, trade: dict[str, Any], resolution_price: float) -> tuple[float, float]:
        """Calculate realised PnL for a simulated trade.

        For a BUY on YES token:
            shares = investment / sim_price
            payout = shares * resolution_price
            pnl = payout - investment - fee

        For a SELL we invert the logic.
        """
        sim_price = trade.get("sim_price")
        if not sim_price or sim_price <= 0:
            return 0.0, 0.0

        investment = trade.get("sim_investment", 100.0)
        fee = trade.get("sim_fee", 0.0)
        side = trade.get("target_side", "BUY")

        shares = investment / sim_price

        if side == "BUY":
            payout = shares * resolution_price
        else:
            # SELL: profit when price drops
            payout = shares * (1.0 - resolution_price)

        pnl = round(payout - investment - fee, 4)
        pnl_pct = round((pnl / investment) * 100, 2) if investment > 0 else 0.0
        return pnl, pnl_pct
