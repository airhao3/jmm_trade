"""Trade data enricher – aggregates context for rich notifications.

Fetches whale profile, orderbook depth, position history, and market
metadata to build a comprehensive trade context for notifications.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.config.models import TargetAccount


@dataclass
class WhaleProfile:
    """Cached profile for a tracked address."""

    nickname: str = ""
    address: str = ""
    all_time_profit: float = 0.0
    total_volume: float = 0.0
    win_rate: float = 0.0
    rank: int | None = None
    total_trades: int = 0
    labels: list[str] = field(default_factory=list)
    last_updated: float = 0.0


@dataclass
class OrderbookSnapshot:
    """Orderbook state at time of trade."""

    spread_pct: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    midpoint: float = 0.0


@dataclass
class PositionContext:
    """Target's position in the market."""

    total_shares: float = 0.0
    total_value_usd: float = 0.0
    avg_price: float = 0.0
    trade_count_recent: int = 0  # trades in last N minutes
    is_adding: bool = False  # adding to existing position
    position_change: str = "NEW"  # NEW, ADD, REDUCE, EXIT


@dataclass
class MarketContext:
    """Market-level metadata."""

    title: str = ""
    slug: str = ""
    end_date: str = ""
    minutes_to_close: float | None = None
    volume_24h: float = 0.0
    liquidity: float = 0.0
    outcome: str = ""
    description: str = ""


@dataclass
class EnrichedTrade:
    """Fully enriched trade with all context layers."""

    # Basic
    target: TargetAccount | None = None
    raw_trade: dict = field(default_factory=dict)
    side: str = ""
    price: float = 0.0
    size: float = 0.0
    usd_value: float = 0.0
    implied_probability: float = 0.0
    market_title: str = ""
    outcome: str = ""
    tx_hash: str = ""
    timestamp: int = 0

    # Deep analysis
    whale: WhaleProfile = field(default_factory=WhaleProfile)
    orderbook: OrderbookSnapshot = field(default_factory=OrderbookSnapshot)
    position: PositionContext = field(default_factory=PositionContext)
    market: MarketContext = field(default_factory=MarketContext)

    # External reference
    external_price: float | None = None
    external_source: str = ""
    premium_pct: float | None = None

    # Enrichment metadata
    enrichment_latency_ms: float = 0.0
    enrichment_errors: list[str] = field(default_factory=list)


class TradeEnricher:
    """Aggregates context data for trades to produce rich notifications."""

    PROFILE_CACHE_TTL = 300  # 5 minutes

    def __init__(self, api: PolymarketClient) -> None:
        self.api = api
        self._profile_cache: dict[str, WhaleProfile] = {}
        self._recent_trades: dict[str, list[dict]] = {}  # address -> recent trades

    async def enrich(
        self,
        target: TargetAccount,
        trade: dict[str, Any],
    ) -> EnrichedTrade:
        """Build a fully enriched trade context.

        Fetches all data concurrently for minimal latency.
        """
        t0 = time.monotonic()
        enriched = EnrichedTrade(
            target=target,
            raw_trade=trade,
            side=trade.get("side", "BUY"),
            price=float(trade.get("price", 0)),
            size=float(trade.get("size", 0)),
            market_title=trade.get("title", ""),
            outcome=trade.get("outcome", ""),
            tx_hash=trade.get("transactionHash", ""),
            timestamp=int(trade.get("timestamp", 0)),
        )

        # Calculate basics
        enriched.usd_value = round(enriched.price * enriched.size, 2)
        enriched.implied_probability = round(enriched.price * 100, 1)

        # Fetch all context data concurrently
        token_id = trade.get("asset", "")
        condition_id = trade.get("conditionId", "")
        slug = trade.get("slug", "")

        tasks = {
            "profile": self._fetch_profile(target),
            "orderbook": self._fetch_orderbook(token_id),
            "position": self._fetch_position(target, condition_id, trade),
            "market": self._fetch_market(condition_id, slug, trade),
        }

        results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )

        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                enriched.enrichment_errors.append(f"{key}: {result}")
                logger.debug(f"Enrichment error ({key}): {result}")
            else:
                if key == "profile":
                    enriched.whale = result
                elif key == "orderbook":
                    enriched.orderbook = result
                elif key == "position":
                    enriched.position = result
                elif key == "market":
                    enriched.market = result

        # Try external price (non-blocking, best-effort)
        try:
            ext = await self._fetch_external_price(enriched.market_title)
            if ext:
                enriched.external_price = ext["price"]
                enriched.external_source = ext["source"]
                if enriched.price > 0 and ext.get("implied_price"):
                    enriched.premium_pct = round(
                        (enriched.price - ext["implied_price"])
                        / ext["implied_price"] * 100, 2
                    )
        except Exception as e:
            enriched.enrichment_errors.append(f"external_price: {e}")

        enriched.enrichment_latency_ms = (time.monotonic() - t0) * 1000
        return enriched

    # ── Profile ────────────────────────────────────────────

    async def _fetch_profile(self, target: TargetAccount) -> WhaleProfile:
        """Build whale profile from trade history and positions."""
        cached = self._profile_cache.get(target.address)
        if cached and (time.monotonic() - cached.last_updated) < self.PROFILE_CACHE_TTL:
            return cached

        profile = WhaleProfile(
            nickname=target.nickname,
            address=target.address,
        )

        # Fetch recent trades + open positions concurrently
        trades, positions = await asyncio.gather(
            self.api.get_trades(target.address, limit=100),
            self.api.get_positions(target.address),
            return_exceptions=True,
        )

        if isinstance(trades, Exception):
            trades = []
        if isinstance(positions, Exception):
            positions = []

        # Stats from trade history
        if trades:
            profile.total_trades = len(trades)
            total_volume = sum(
                float(t.get("price", 0)) * float(t.get("size", 0))
                for t in trades
            )
            profile.total_volume = round(total_volume, 2)

            # Win-rate heuristic: BUY at low price (<0.5) or SELL at high price (>0.5)
            good_buys = sum(
                1 for t in trades
                if t.get("side") == "BUY" and float(t.get("price", 1)) < 0.5
            )
            good_sells = sum(
                1 for t in trades
                if t.get("side") == "SELL" and float(t.get("price", 0)) > 0.5
            )
            if len(trades) > 0:
                profile.win_rate = round(
                    (good_buys + good_sells) / len(trades) * 100, 1
                )

        # Estimate PnL from open positions
        if positions:
            total_pnl = 0.0
            for pos in positions:
                size = float(pos.get("size", 0) or 0)
                avg_price = float(pos.get("avgPrice", 0) or 0)
                cur_price = float(pos.get("curPrice", pos.get("price", 0)) or 0)
                if size > 0 and avg_price > 0:
                    total_pnl += (cur_price - avg_price) * size
            profile.all_time_profit = round(total_pnl, 2)

        # Auto-label based on activity patterns
        if profile.total_volume > 10_000:
            profile.labels.append("💰 High Volume")
        if profile.total_trades >= 50:
            profile.labels.append("⚡ Active")
        if profile.win_rate > 60:
            profile.labels.append("🎯 High WR")
        if positions and len(positions) > 10:
            profile.labels.append("📈 Diversified")

        profile.last_updated = time.monotonic()
        self._profile_cache[target.address] = profile
        return profile

    # ── Orderbook ──────────────────────────────────────────

    async def _fetch_orderbook(self, token_id: str) -> OrderbookSnapshot:
        """Fetch orderbook and compute spread/depth."""
        if not token_id:
            return OrderbookSnapshot()

        book = await self.api.get_orderbook(token_id)
        asks = book.get("asks", [])
        bids = book.get("bids", [])

        snap = OrderbookSnapshot()

        if asks:
            first_ask = asks[0]
            snap.best_ask = float(
                first_ask.get("price", 0) if isinstance(first_ask, dict)
                else first_ask[0]
            )
            # Sum depth (top 10 levels)
            for a in asks[:10]:
                p = float(a.get("price", 0) if isinstance(a, dict) else a[0])
                s = float(a.get("size", 0) if isinstance(a, dict) else a[1])
                snap.ask_depth_usd += p * s

        if bids:
            first_bid = bids[0]
            snap.best_bid = float(
                first_bid.get("price", 0) if isinstance(first_bid, dict)
                else first_bid[0]
            )
            for b in bids[:10]:
                p = float(b.get("price", 0) if isinstance(b, dict) else b[0])
                s = float(b.get("size", 0) if isinstance(b, dict) else b[1])
                snap.bid_depth_usd += p * s

        if snap.best_ask > 0 and snap.best_bid > 0:
            snap.midpoint = (snap.best_ask + snap.best_bid) / 2
            snap.spread_pct = round(
                (snap.best_ask - snap.best_bid) / snap.midpoint * 100, 2
            )

        snap.ask_depth_usd = round(snap.ask_depth_usd, 2)
        snap.bid_depth_usd = round(snap.bid_depth_usd, 2)
        return snap

    # ── Position context ──────────────────────────────────

    async def _fetch_position(
        self,
        target: TargetAccount,
        condition_id: str,
        current_trade: dict,
    ) -> PositionContext:
        """Analyze target's position in this market."""
        ctx = PositionContext()

        if not condition_id:
            return ctx

        # Get all recent trades for this target in this market
        all_trades = await self.api.get_trades(target.address, limit=50)
        market_trades = [
            t for t in all_trades
            if t.get("conditionId") == condition_id
        ]

        if market_trades:
            # Calculate total shares and value
            total_buy = sum(
                float(t.get("size", 0)) for t in market_trades
                if t.get("side") == "BUY"
            )
            total_sell = sum(
                float(t.get("size", 0)) for t in market_trades
                if t.get("side") == "SELL"
            )
            ctx.total_shares = round(total_buy - total_sell, 2)

            # Average price
            buy_value = sum(
                float(t.get("price", 0)) * float(t.get("size", 0))
                for t in market_trades if t.get("side") == "BUY"
            )
            if total_buy > 0:
                ctx.avg_price = round(buy_value / total_buy, 4)

            ctx.total_value_usd = round(
                ctx.total_shares * float(current_trade.get("price", 0)), 2
            )

            # Recent trade count (last 10 min)
            now_ts = int(current_trade.get("timestamp", 0))
            ctx.trade_count_recent = sum(
                1 for t in market_trades
                if now_ts - int(t.get("timestamp", 0)) < 600
            )

            # Position change classification
            current_side = current_trade.get("side", "BUY")
            existing_before = len(market_trades) > 1  # had prior trades
            if not existing_before:
                ctx.position_change = "NEW"
            elif current_side == "BUY":
                ctx.position_change = "ADD"
                ctx.is_adding = True
            elif current_side == "SELL" and ctx.total_shares <= 0:
                ctx.position_change = "EXIT"
            else:
                ctx.position_change = "REDUCE"

        return ctx

    # ── Market metadata ────────────────────────────────────

    async def _fetch_market(
        self,
        condition_id: str,
        slug: str,
        trade: dict,
    ) -> MarketContext:
        """Fetch market metadata."""
        ctx = MarketContext(
            title=trade.get("title", ""),
            slug=slug,
            outcome=trade.get("outcome", ""),
        )

        market_data = None
        if condition_id:
            market_data = await self.api.get_market(condition_id)
        elif slug:
            market_data = await self.api.get_market_by_slug(slug)

        if market_data:
            ctx.end_date = market_data.get("endDate", "")
            ctx.volume_24h = float(market_data.get("volume24hr", 0) or 0)
            ctx.liquidity = float(market_data.get("liquidity", 0) or 0)
            ctx.description = (market_data.get("description", "") or "")[:200]

            # Calculate minutes to close
            if ctx.end_date:
                try:
                    from datetime import UTC, datetime
                    end = datetime.fromisoformat(
                        ctx.end_date.replace("Z", "+00:00")
                    )
                    now = datetime.now(UTC)
                    delta = (end - now).total_seconds() / 60
                    if delta > 0:
                        ctx.minutes_to_close = round(delta, 1)
                    else:
                        ctx.minutes_to_close = 0  # already settled
                except (ValueError, TypeError):
                    pass

        return ctx

    # ── External price reference ──────────────────────────

    async def _fetch_external_price(
        self,
        market_title: str,
    ) -> dict | None:
        """Best-effort: fetch external price for comparison.

        Currently supports BTC and ETH markets by extracting the asset
        from the market title and querying a public price API.
        """
        title_upper = market_title.upper()

        # Detect crypto asset from title
        asset = None
        if "BITCOIN" in title_upper or "BTC" in title_upper:
            asset = "BTC"
        elif "ETHEREUM" in title_upper or "ETH" in title_upper:
            asset = "ETH"
        elif "SOLANA" in title_upper or "SOL" in title_upper:
            asset = "SOL"

        if not asset:
            return None

        # Use CoinGecko (no geo-restriction) with id mapping
        cg_ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        cg_id = cg_ids.get(asset)
        if not cg_id:
            return None

        try:
            import aiohttp
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                url = (
                    f"https://api.coingecko.com/api/v3/simple/price"
                    f"?ids={cg_id}&vs_currencies=usd"
                )
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float(data.get(cg_id, {}).get("usd", 0))
                        if price > 0:
                            return {
                                "price": price,
                                "source": "CoinGecko",
                                "symbol": f"{asset}/USD",
                                "implied_price": None,
                            }
        except Exception:
            pass

        return None
