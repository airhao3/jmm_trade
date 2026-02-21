"""Shadow execution engine – simulates copy-trades with configurable delay.

Responsibilities:
    1. Wait N seconds after a target trade is detected.
    2. Sample the orderbook (best ask / best bid) at that moment.
    3. Compute slippage, fees, and cost.
    4. Persist the simulated trade record.

This module is fully independent and can be unit-tested without a live API
by injecting a mock PolymarketClient.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.config.models import AppConfig, TargetAccount
from src.data.database import Database
from src.data.models import SimTrade


class TradeSimulator:
    """Simulates delayed order execution against the live orderbook."""

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

    async def simulate(
        self,
        target: TargetAccount,
        trade: dict[str, Any],
    ) -> list[SimTrade]:
        """Run simulation for all configured delays and persist results.

        Returns a list of SimTrade records (one per delay).
        """
        results: list[SimTrade] = []

        for delay in self.config.simulation.delays:
            trade_id = f"{trade.get('transactionHash', '')}_{delay}s"

            # Skip duplicates
            if await self.db.trade_exists(trade_id):
                logger.debug(f"Trade {trade_id} already exists, skipping")
                continue

            try:
                sim = await self._simulate_single(target, trade, delay)
                await self.db.insert_sim_trade(sim)
                results.append(sim)

                status = "OK" if sim.sim_success else "FAILED"
                logger.info(
                    f"[{target.nickname}] SIM {delay}s {status}: "
                    f"{sim.market_name} | "
                    f"target={sim.target_price:.4f} sim={sim.sim_price or 0:.4f} "
                    f"slip={sim.slippage_pct or 0:.2f}%"
                )
            except Exception as exc:
                logger.error(f"[{target.nickname}] Simulation error (delay={delay}s): {exc}")
                failed = self._build_failed(target, trade, delay, str(exc))
                await self.db.insert_sim_trade(failed)
                results.append(failed)

        return results

    # ── Private ──────────────────────────────────────────

    async def _simulate_single(
        self,
        target: TargetAccount,
        trade: dict[str, Any],
        delay: int,
    ) -> SimTrade:
        """Simulate a single trade at a given delay."""
        # 1. Wait for the configured delay
        await asyncio.sleep(delay)

        # 2. Fetch the orderbook snapshot
        token_id = trade.get("asset", "")
        orderbook = await self.api.get_orderbook(token_id)

        # 3. Extract best price
        side = trade.get("side", "BUY")
        sim_price = self._extract_best_price(orderbook, side)
        target_price = float(trade.get("price", 0))

        # 4. Slippage calculation
        slippage_pct: float | None = None
        if target_price > 0 and sim_price is not None:
            slippage_pct = ((sim_price - target_price) / target_price) * 100

        # 5. Cost calculation
        investment = self.config.simulation.investment_per_trade
        fee = round(investment * self.config.simulation.fee_rate, 4)
        total_cost = round(investment + fee, 4)

        # 6. Slippage limit check
        sim_success = True
        failure_reason: str | None = None

        if sim_price is None:
            sim_success = False
            failure_reason = "Empty orderbook – no price available"
        elif (
            self.config.simulation.enable_slippage_check
            and slippage_pct is not None
            and abs(slippage_pct) > self.config.simulation.max_slippage_pct
        ):
            sim_success = False
            failure_reason = (
                f"Slippage {slippage_pct:.2f}% exceeds "
                f"limit {self.config.simulation.max_slippage_pct}%"
            )

        return SimTrade(
            trade_id=f"{trade.get('transactionHash', '')}_{delay}s",
            target_address=target.address,
            target_nickname=target.nickname,
            market_id=trade.get("slug", ""),
            market_name=trade.get("title", ""),
            condition_id=trade.get("conditionId", ""),
            event_slug=trade.get("eventSlug", ""),
            target_side=side,
            target_price=target_price,
            target_size=float(trade.get("size", 0)),
            target_timestamp=int(trade.get("timestamp", 0)),
            target_execution_time=int(trade.get("timestamp", 0)),
            sim_delay=delay,
            sim_price=sim_price,
            sim_delayed_price=sim_price,
            sim_investment=investment,
            sim_fee=fee,
            sim_success=sim_success,
            sim_failure_reason=failure_reason,
            slippage_pct=slippage_pct,
            total_cost=total_cost,
            status="OPEN" if sim_success else "FAILED",
        )

    def _extract_best_price(self, orderbook: dict[str, Any], side: str) -> float | None:
        """Get the best executable price from the orderbook.

        For a BUY copy-trade we need the best ask (lowest sell offer).
        For a SELL copy-trade we need the best bid (highest buy offer).
        """
        try:
            if side == "BUY":
                asks = orderbook.get("asks", [])
                if asks:
                    # asks is a list of {"price": "0.55", "size": "100"}
                    if isinstance(asks[0], dict):
                        return float(asks[0].get("price", 0))
                    # or [[price, size], ...]
                    return float(asks[0][0])
            else:
                bids = orderbook.get("bids", [])
                if bids:
                    if isinstance(bids[0], dict):
                        return float(bids[0].get("price", 0))
                    return float(bids[0][0])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            logger.warning(f"Could not parse orderbook price: {exc}")
        return None

    def _build_failed(
        self,
        target: TargetAccount,
        trade: dict[str, Any],
        delay: int,
        reason: str,
    ) -> SimTrade:
        """Create a FAILED SimTrade record."""
        investment = self.config.simulation.investment_per_trade
        fee = round(investment * self.config.simulation.fee_rate, 4)
        return SimTrade(
            trade_id=f"{trade.get('transactionHash', '')}_{delay}s",
            target_address=target.address,
            target_nickname=target.nickname,
            market_id=trade.get("slug", ""),
            market_name=trade.get("title", ""),
            condition_id=trade.get("conditionId", ""),
            event_slug=trade.get("eventSlug", ""),
            target_side=trade.get("side", "BUY"),
            target_price=float(trade.get("price", 0)),
            target_size=float(trade.get("size", 0)),
            target_timestamp=int(trade.get("timestamp", 0)),
            sim_delay=delay,
            sim_price=None,
            sim_delayed_price=None,
            sim_investment=investment,
            sim_fee=fee,
            sim_success=False,
            sim_failure_reason=reason,
            slippage_pct=None,
            total_cost=round(investment + fee, 4),
            status="FAILED",
        )
