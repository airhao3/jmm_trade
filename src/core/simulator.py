"""Shadow execution engine – simulates copy-trades with configurable delay.

v2 — VWAP-based slippage against real orderbook depth:
    1. Wait N seconds after a target trade is detected.
    2. Fetch the FULL orderbook (top-10 levels).
    3. Walk the book to compute VWAP for the desired trade size.
    4. Reject if slippage > 5% (configurable).
    5. Persist the simulated trade record.

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

# Hard slippage ceiling regardless of config
MAX_SLIPPAGE_HARD_LIMIT = 5.0  # percent


class TradeSimulator:
    """Simulates delayed order execution against the live orderbook.

    Uses VWAP (Volume-Weighted Average Price) across top-10 orderbook
    levels instead of just the best bid/ask, producing realistic
    slippage estimates.
    """

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
        """Run simulation for all configured delays and persist results."""
        results: list[SimTrade] = []

        for delay in self.config.simulation.delays:
            trade_id = f"{trade.get('transactionHash', '')}_{delay}s"

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
        """Simulate a single trade at a given delay using VWAP."""
        await asyncio.sleep(delay)

        token_id = trade.get("asset", "")
        orderbook = await self.api.get_orderbook(token_id)
        side = trade.get("side", "BUY")
        target_price = float(trade.get("price", 0))
        target_size = float(trade.get("size", 0))

        # Use investment to determine how many shares we'd buy
        investment = self.config.simulation.investment_per_trade
        fee = round(investment * self.config.simulation.fee_rate, 4)
        total_cost = round(investment + fee, 4)

        # Compute VWAP across the orderbook for our trade size
        vwap_result = self._compute_vwap(orderbook, side, investment)
        sim_price = vwap_result["vwap"]
        fillable = vwap_result["fillable"]
        levels_used = vwap_result["levels_used"]

        # Slippage calculation
        slippage_pct: float | None = None
        if target_price > 0 and sim_price is not None:
            slippage_pct = ((sim_price - target_price) / target_price) * 100

        # Success / failure determination
        sim_success = True
        failure_reason: str | None = None

        if sim_price is None:
            sim_success = False
            failure_reason = "Empty orderbook – no price available"
        elif not fillable:
            sim_success = False
            failure_reason = (
                f"Insufficient liquidity: only {levels_used} levels available"
            )
        elif slippage_pct is not None and abs(slippage_pct) > MAX_SLIPPAGE_HARD_LIMIT:
            sim_success = False
            failure_reason = (
                f"SLIPPAGE_TOO_HIGH: {slippage_pct:.2f}% exceeds "
                f"hard limit {MAX_SLIPPAGE_HARD_LIMIT}%"
            )
        elif (
            self.config.simulation.enable_slippage_check
            and slippage_pct is not None
            and abs(slippage_pct) > self.config.simulation.max_slippage_pct
        ):
            sim_success = False
            failure_reason = (
                f"Slippage {slippage_pct:.2f}% exceeds "
                f"config limit {self.config.simulation.max_slippage_pct}%"
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
            target_size=target_size,
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

    # ── VWAP computation ─────────────────────────────────

    @staticmethod
    def _compute_vwap(
        orderbook: dict[str, Any],
        side: str,
        usd_amount: float,
    ) -> dict:
        """Walk the orderbook to compute VWAP for a given USD amount.

        For BUY: walk asks (ascending price). We spend $usd_amount buying shares.
        For SELL: walk bids (descending price). We sell shares worth $usd_amount.

        Returns dict with:
          vwap: volume-weighted average price (or None if empty)
          fillable: True if enough liquidity exists
          levels_used: number of orderbook levels consumed
          total_shares: total shares that would be acquired
        """
        levels = orderbook.get("asks" if side == "BUY" else "bids", [])

        if not levels:
            return {"vwap": None, "fillable": False, "levels_used": 0, "total_shares": 0}

        remaining_usd = usd_amount
        total_cost = 0.0
        total_shares = 0.0
        levels_used = 0

        for level in levels[:10]:  # Top 10 levels
            try:
                if isinstance(level, dict):
                    price = float(level.get("price", 0))
                    size = float(level.get("size", 0))
                else:
                    price = float(level[0])
                    size = float(level[1])
            except (IndexError, TypeError, ValueError):
                continue

            if price <= 0 or size <= 0:
                continue

            levels_used += 1
            level_usd = price * size  # total USD available at this level

            if level_usd >= remaining_usd:
                # This level has enough liquidity to fill the rest
                shares_needed = remaining_usd / price
                total_cost += remaining_usd
                total_shares += shares_needed
                remaining_usd = 0
                break
            else:
                # Consume entire level and continue
                total_cost += level_usd
                total_shares += size
                remaining_usd -= level_usd

        fillable = remaining_usd <= 0
        vwap = (total_cost / total_shares) if total_shares > 0 else None

        return {
            "vwap": round(vwap, 6) if vwap else None,
            "fillable": fillable,
            "levels_used": levels_used,
            "total_shares": round(total_shares, 2),
        }

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
