"""Alpha Discovery — find POTENTIAL_SNIPER addresses from market trade history.

Strategy:
  1. Pick recent crypto-related markets (BTC/ETH/SOL Up or Down).
  2. Fetch all trades for those markets.
  3. Find addresses that bought early (price < 0.30) on the WINNING side.
  4. Rank by profit (winning_price × size - entry_price × size).
  5. Profile top addresses via SmartMoneyProfiler.
  6. Output candidates for targets.json.

Usage:
  python -m scripts.discover_alpha [--market-slug SLUG] [--top N]
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.api.rate_limiter import TokenBucketRateLimiter
from src.config.loader import load_config
from src.core.profiler import SmartMoneyProfiler

# ── Data structures ──────────────────────────────────────


class TraderStats:
    """Aggregated stats for a single address across one market."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.buys: list[dict] = []
        self.sells: list[dict] = []
        self.total_buy_usd: float = 0.0
        self.total_sell_usd: float = 0.0
        self.avg_buy_price: float = 0.0
        self.earliest_buy_price: float = 1.0
        self.shares_bought: float = 0.0

    def add_trade(self, trade: dict[str, Any]) -> None:
        price = float(trade.get("price", 0))
        size = float(trade.get("size", 0))
        usd = price * size

        if trade.get("side") == "BUY":
            self.buys.append(trade)
            self.total_buy_usd += usd
            self.shares_bought += size
            if price < self.earliest_buy_price:
                self.earliest_buy_price = price
        else:
            self.sells.append(trade)
            self.total_sell_usd += usd

    @property
    def estimated_profit(self) -> float:
        """Profit assuming winning side resolves to $1."""
        if self.shares_bought <= 0:
            return 0.0
        return self.shares_bought - self.total_buy_usd

    @property
    def trade_count(self) -> int:
        return len(self.buys) + len(self.sells)


# ── Core discovery logic ──────────────────────────────────


async def discover_alpha(
    api: PolymarketClient,
    profiler: SmartMoneyProfiler,
    market_slug: str | None = None,
    top_n: int = 10,
) -> list[dict]:
    """Find the most profitable early entries in recent markets.

    Returns list of candidate dicts with address, profit, profile info.
    """
    # Step 1: Find markets to analyze
    # Use recent trades from existing targets to discover active asset_ids,
    # then fetch ALL trades for those markets (not user-specific).
    from src.config.loader import load_config as _load_config
    _cfg = _load_config()
    asset_ids_to_scan: list[tuple[str, str]] = []  # (asset_id, title)

    if market_slug:
        market = await api.get_market_by_slug(market_slug)
        if market:
            tokens = market.get("clobTokenIds") or []
            if isinstance(tokens, str):
                tokens = [t.strip() for t in tokens.strip("[]").split(",") if t.strip()]
            title = market.get("question", market.get("title", "?"))
            for tid in tokens:
                tid = str(tid).strip('" ')
                if len(tid) > 10:
                    asset_ids_to_scan.append((tid, title))
    else:
        # Discover from existing target's recent trades
        for target in _cfg.get_active_targets():
            trades = await api.get_trades(target.address, limit=50)
            seen = set()
            for t in trades:
                aid = t.get("asset", "")
                title = t.get("title", "")
                if aid and aid not in seen:
                    seen.add(aid)
                    asset_ids_to_scan.append((aid, title))

    if not asset_ids_to_scan:
        logger.warning("No markets found to analyze")
        return []

    logger.info(f"Analyzing {len(asset_ids_to_scan)} market tokens for alpha addresses...")

    # Step 2: For each market token, fetch ALL trades and find profitable early buyers
    all_candidates: dict[str, TraderStats] = {}

    for asset_id, title in asset_ids_to_scan:
        trades = await api.get_market_trades(asset_id, limit=500)
        if not trades:
            continue

        logger.info(f"  {title[:50]}: {len(trades)} trades")

        # Aggregate by address (proxyWallet is the address field)
        addr_stats: dict[str, TraderStats] = {}
        for t in trades:
            addr = t.get("proxyWallet", "")
            if not addr:
                continue
            if addr not in addr_stats:
                addr_stats[addr] = TraderStats(addr)
            addr_stats[addr].add_trade(t)

        # Find early buyers (bought at price < 0.30)
        for addr, stats in addr_stats.items():
            if stats.earliest_buy_price < 0.30 and stats.estimated_profit > 0:
                if addr not in all_candidates:
                    all_candidates[addr] = stats
                else:
                    # Merge stats
                    for b in stats.buys:
                        all_candidates[addr].add_trade(b)

    if not all_candidates:
        logger.warning("No profitable early buyers found")
        return []

    # Step 3: Rank by estimated profit
    ranked = sorted(
        all_candidates.values(),
        key=lambda s: s.estimated_profit,
        reverse=True,
    )[:top_n]

    logger.info(f"\n{'='*60}")
    logger.info(f"TOP {len(ranked)} POTENTIAL ALPHA ADDRESSES")
    logger.info(f"{'='*60}\n")

    # Step 4: Profile each candidate
    results = []
    for i, stats in enumerate(ranked, 1):
        logger.info(
            f"  #{i} {stats.address[:16]}..."
            f"  profit≈${stats.estimated_profit:.2f}"
            f"  entry@${stats.earliest_buy_price:.3f}"
            f"  trades={stats.trade_count}"
        )

        # Quick profile via trades API
        from src.config.models import TargetAccount
        target = TargetAccount(
            address=stats.address,
            nickname=f"Alpha_{i}",
            enabled=True,
        )

        try:
            profile = await profiler.profile(target)
            results.append({
                "rank": i,
                "address": stats.address,
                "estimated_profit": round(stats.estimated_profit, 2),
                "earliest_buy_price": stats.earliest_buy_price,
                "trade_count": stats.trade_count,
                "archetype": profile.archetype.value,
                "follow_score": profile.follow_score,
                "win_rate": profile.win_rate,
                "accumulation": profile.accumulation_score,
                "wash_score": profile.wash_trade_score,
                "poll_interval": profile.poll_interval,
            })
            logger.info(
                f"    → {profile.archetype.value} "
                f"(score={profile.follow_score}/10, WR={profile.win_rate}%)"
            )
        except Exception as exc:
            logger.warning(f"    → Profile failed: {exc}")
            results.append({
                "rank": i,
                "address": stats.address,
                "estimated_profit": round(stats.estimated_profit, 2),
                "earliest_buy_price": stats.earliest_buy_price,
                "trade_count": stats.trade_count,
                "archetype": "UNKNOWN",
                "follow_score": 0,
            })

    # Step 5: Output targets.json snippet
    logger.info(f"\n{'='*60}")
    logger.info("TARGETS.JSON CANDIDATES (copy-paste ready):")
    logger.info(f"{'='*60}\n")

    for r in results:
        if r.get("follow_score", 0) >= 4:
            logger.info(
                f'  {{"address": "{r["address"]}", '
                f'"nickname": "Alpha_{r["rank"]}_{r["archetype"]}", '
                f'"enabled": true}}'
            )

    return results


# ── CLI ──────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="Discover alpha addresses")
    parser.add_argument(
        "--market-slug", type=str, default=None,
        help="Specific market slug to analyze (default: auto-discover)",
    )
    parser.add_argument(
        "--top", type=int, default=10,
        help="Number of top candidates to return (default: 10)",
    )
    args = parser.parse_args()

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
        results = await discover_alpha(
            api, profiler,
            market_slug=args.market_slug,
            top_n=args.top,
        )

        if results:
            snipers = [r for r in results if r.get("follow_score", 0) >= 6]
            logger.info(
                f"\nSummary: {len(results)} candidates analyzed, "
                f"{len(snipers)} high-value (score>=6)"
            )
        else:
            logger.info("\nNo alpha candidates found in analyzed markets.")


if __name__ == "__main__":
    asyncio.run(main())
