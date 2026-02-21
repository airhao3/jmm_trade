"""Check shadow tracking results after 8 hours.

Usage:
  python -m scripts.check_shadow_results
"""

import asyncio
import json
from pathlib import Path

from loguru import logger

from src.api.client import PolymarketClient
from src.api.rate_limiter import TokenBucketRateLimiter
from src.config.loader import load_config
from src.core.profiler import SmartMoneyProfiler
from src.core.shadow import ShadowTracker

CANDIDATES_PATH = Path("config/candidates.json")


async def main() -> None:
    """Load shadow tracker and display results."""
    if not CANDIDATES_PATH.exists():
        logger.error("No candidates.json found")
        return

    config = load_config()
    async with PolymarketClient(
        config.api,
        config.system,
        TokenBucketRateLimiter(
            max_requests=config.api.rate_limit.max_requests,
            time_window=config.api.rate_limit.time_window,
            burst_size=config.api.rate_limit.burst_size,
        ),
    ) as api:
        profiler = SmartMoneyProfiler(api)
        shadow = ShadowTracker(api, profiler)
        loaded = shadow.load_candidates()

        logger.info("=" * 60)
        logger.info(f"SHADOW TRACKING RESULTS ({loaded} candidates)")
        logger.info("=" * 60)

        # Sort by shadow_score descending
        scorecards = sorted(
            shadow.scorecards.values(),
            key=lambda s: -s.shadow_score,
        )

        for i, sc in enumerate(scorecards, 1):
            logger.info(
                f"\n#{i} {sc.nickname} ({sc.address[:16]}...)"
            )
            logger.info(f"  Status: {sc.status.value}")
            logger.info(f"  Shadow Score: {sc.shadow_score:.2f}/10")
            logger.info(f"  Virtual Trades: {sc.total_virtual_trades}")
            logger.info(f"  vWR: {sc.vWR:.1f}%")
            logger.info(f"  vProfitFactor: {sc.vProfitFactor:.2f}")
            logger.info(f"  Consistency: {sc.consistency:.2f}")
            logger.info(f"  Total vProfit: ${sc.total_v_profit:.2f}")
            logger.info(f"  Total vLoss: ${sc.total_v_loss:.2f}")
            logger.info(f"  Hours in pool: {sc.hours_in_pool:.1f}h")
            logger.info(f"  Open positions: {len(sc.open_positions)}")
            logger.info(f"  Closed trades: {len(sc.closed_trades)}")
            logger.info(
                f"  Promotion eligible: "
                f"{'YES' if sc.is_promotion_eligible else 'NO'}"
            )

        # Show promotion candidates
        logger.info("\n" + "=" * 60)
        logger.info("PROMOTION CANDIDATES (verified + eligible)")
        logger.info("=" * 60)
        promotable = shadow.get_promotion_candidates(n=5)
        if promotable:
            for i, sc in enumerate(promotable, 1):
                logger.info(
                    f"  #{i} {sc.nickname}: score={sc.shadow_score:.2f}, "
                    f"vWR={sc.vWR:.1f}%, trades={sc.total_virtual_trades}"
                )
        else:
            logger.info("  No candidates ready for promotion yet")

        # Summary stats
        logger.info("\n" + "=" * 60)
        logger.info("SUMMARY STATISTICS")
        logger.info("=" * 60)
        verified = sum(1 for sc in scorecards if sc.status.value == "SHADOW_VERIFIED")
        total_trades = sum(sc.total_virtual_trades for sc in scorecards)
        total_profit = sum(sc.total_v_profit for sc in scorecards)
        total_loss = sum(sc.total_v_loss for sc in scorecards)
        logger.info(f"  Total candidates: {loaded}")
        logger.info(f"  Verified: {verified}")
        logger.info(f"  Total virtual trades: {total_trades}")
        logger.info(f"  Total vProfit: ${total_profit:.2f}")
        logger.info(f"  Total vLoss: ${total_loss:.2f}")
        logger.info(
            f"  Net vPnL: ${total_profit - total_loss:.2f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
