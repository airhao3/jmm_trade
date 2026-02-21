"""Trade data enricher – aggregates context for rich notifications.

Architecture (v2 – optimised for <500ms):
  - Single shared trades fetch reused by profile + position analysis
  - TTL caches for market metadata (15min) and whale profiles (5min)
  - All fetches (orderbook, market, external price) run in parallel
  - WebSocket price cache used when available to skip REST orderbook call
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiohttp
from loguru import logger

from src.api.client import PolymarketClient
from src.api.price_feed import PriceFeed
from src.config.models import TargetAccount

# ── Data models ──────────────────────────────────────────


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
    resolved: bool = False


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


# ── Pre-flight scoring ────────────────────────────────────


@dataclass
class PreFlightResult:
    """Result of pre-flight quality check."""

    passed: bool = True
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    skip_reasons: list[str] = field(default_factory=list)
    skip_simulation: bool = False  # True → don't even run simulator (toxic spread)
    adverse_momentum: bool = False  # True → OKX price moving against position


# ── Enricher ──────────────────────────────────────────────


class TradeEnricher:
    """Aggregates context data for trades to produce rich notifications.

    Performance targets: <500ms enrichment via:
      - Shared trades fetch (profile + position reuse same data)
      - TTL caches (market 15min, profile 5min, external price 60s)
      - All REST calls in parallel via asyncio.gather
      - WebSocket price cache bypass for orderbook
    """

    PROFILE_CACHE_TTL = 300   # 5 minutes
    MARKET_CACHE_TTL = 900    # 15 minutes
    EXT_PRICE_CACHE_TTL = 60  # 1 minute

    def __init__(
        self,
        api: PolymarketClient,
        ws_price_cache: dict[str, float] | None = None,
        price_feed: PriceFeed | None = None,
    ) -> None:
        self.api = api
        self._ws_prices = ws_price_cache or {}
        self._price_feed = price_feed  # OKX real-time prices

        # Caches
        self._profile_cache: dict[str, WhaleProfile] = {}
        self._market_cache: dict[str, tuple[float, MarketContext]] = {}
        self._trades_cache: dict[str, tuple[float, list[dict]]] = {}
        self._ext_price_cache: dict[str, tuple[float, dict]] = {}

    # ── Pre-flight check ─────────────────────────────────

    def pre_flight(
        self,
        trade: dict[str, Any],
        whale: WhaleProfile | None = None,
        orderbook: OrderbookSnapshot | None = None,
        market: MarketContext | None = None,
        min_usd: float = 100.0,
        max_spread_pct: float = 5.0,
    ) -> PreFlightResult:
        """Quality gate with momentum correlation scoring.

        Hard filters gate entry; scoring measures signal strength.
        Momentum correlation: if external price moved >0.1% in 1s in
        the same direction as the whale's trade, score gets a bonus.
        """
        result = PreFlightResult()
        price = float(trade.get("price", 0))
        size = float(trade.get("size", 0))
        usd = price * size

        # ── Hard filters (skip if failed) ──
        if usd < min_usd:
            result.passed = False
            result.skip_reasons.append(f"USD {usd:.0f} < {min_usd:.0f}")

        if market and market.resolved:
            result.passed = False
            result.skip_reasons.append("Market already resolved")

        if market and market.minutes_to_close is not None and market.minutes_to_close <= 0:
            result.passed = False
            result.skip_reasons.append("Market already settled")

        # Spread > 10%: hard circuit breaker — skip simulation entirely
        if orderbook and orderbook.spread_pct > 10.0:
            result.passed = False
            result.skip_simulation = True
            result.skip_reasons.append(
                f"🔴 SPREAD BREAKER: {orderbook.spread_pct:.1f}% > 10% "
                f"(untradeable, simulation skipped)"
            )
        elif orderbook and orderbook.spread_pct > max_spread_pct:
            result.passed = False
            result.skip_reasons.append(
                f"Spread {orderbook.spread_pct:.1f}% > {max_spread_pct:.0f}%"
            )

        if whale and whale.all_time_profit < 0:
            result.passed = False
            result.skip_reasons.append(
                f"Whale PnL negative (${whale.all_time_profit:,.0f})"
            )

        # ── Scoring (for signal strength) ──
        if usd > 500:
            result.score += 1
            result.reasons.append("Large trade")
        if usd > 2000:
            result.score += 1
            result.reasons.append("Very large trade")
        if whale and whale.win_rate > 55:
            result.score += 1
            result.reasons.append("High win rate")
        if whale and whale.all_time_profit > 10_000:
            result.score += 1
            result.reasons.append("Profitable whale")
        if orderbook and 0 < orderbook.spread_pct < 1.0:
            result.score += 1
            result.reasons.append("Tight spread")

        # ── Momentum correlation (OKX real-time) ──
        corr = self._check_momentum_correlation(trade)
        if corr is not None:
            if corr > 0:
                result.score += 2
                result.reasons.append("✅ Momentum aligned with trade")
            else:
                result.score -= 1
                result.reasons.append("⚠️ Momentum against trade")

        # ── Adverse momentum exit trigger (>0.5% against position) ──
        adverse = self._check_adverse_momentum(trade)
        if adverse:
            result.adverse_momentum = True
            result.reasons.append(
                "🚨 ADVERSE MOMENTUM: OKX price moving >0.5% against position"
            )

        return result

    def _check_momentum_correlation(
        self, trade: dict[str, Any]
    ) -> int | None:
        """Check if external price momentum aligns with trade direction.

        Returns:
          +1 if momentum aligns (e.g. BTC rising + whale buys YES on BTC up)
          -1 if momentum opposes
          None if no data available
        """
        if not self._price_feed:
            return None

        title_upper = (trade.get("title", "")).upper()
        asset = None
        if "BITCOIN" in title_upper or "BTC" in title_upper:
            asset = "BTC"
        elif "ETHEREUM" in title_upper or "ETH" in title_upper:
            asset = "ETH"
        elif "SOLANA" in title_upper or "SOL" in title_upper:
            asset = "SOL"

        if not asset or not self._price_feed.is_fresh(asset, max_age_s=10):
            return None

        momentum = self._price_feed.momentum(asset, seconds=1.0)
        if momentum is None or abs(momentum) < 0.1:
            return None  # not significant

        # Determine expected direction from market title
        side = trade.get("side", "BUY")
        is_up_market = any(
            kw in title_upper
            for kw in ["UP", "ABOVE", "HIGHER", "RISE"]
        )
        is_down_market = any(
            kw in title_upper
            for kw in ["DOWN", "BELOW", "LOWER", "FALL"]
        )

        # BUY on "Up" market + price rising = aligned
        # BUY on "Down" market + price falling = aligned
        if side == "BUY":
            if is_up_market and momentum > 0:
                return 1
            if is_down_market and momentum < 0:
                return 1
            if is_up_market and momentum < 0:
                return -1
            if is_down_market and momentum > 0:
                return -1
        elif side == "SELL":
            # SELL on "Up" market + price falling = aligned
            if is_up_market and momentum < 0:
                return 1
            if is_down_market and momentum > 0:
                return 1

        return None  # can't determine

    def _check_adverse_momentum(self, trade: dict[str, Any]) -> bool:
        """Check if OKX price is moving >0.5% AGAINST the trade direction.

        Used to trigger exit/cancel signals for existing positions.
        Uses a 5-second momentum window for more reliable detection.
        """
        if not self._price_feed:
            return False

        title_upper = (trade.get("title", "")).upper()
        asset = None
        if "BITCOIN" in title_upper or "BTC" in title_upper:
            asset = "BTC"
        elif "ETHEREUM" in title_upper or "ETH" in title_upper:
            asset = "ETH"
        elif "SOLANA" in title_upper or "SOL" in title_upper:
            asset = "SOL"

        if not asset or not self._price_feed.is_fresh(asset, max_age_s=10):
            return False

        momentum = self._price_feed.momentum(asset, seconds=5.0)
        if momentum is None or abs(momentum) < 0.5:
            return False  # not significant enough

        side = trade.get("side", "BUY")
        is_up_market = any(
            kw in title_upper for kw in ["UP", "ABOVE", "HIGHER", "RISE"]
        )
        is_down_market = any(
            kw in title_upper for kw in ["DOWN", "BELOW", "LOWER", "FALL"]
        )

        # Adverse = bought UP but price falling, or bought DOWN but price rising
        if side == "BUY":
            if is_up_market and momentum < -0.5:
                return True
            if is_down_market and momentum > 0.5:
                return True
        return False

    # ── Main enrich ──────────────────────────────────────

    async def enrich(
        self,
        target: TargetAccount,
        trade: dict[str, Any],
    ) -> EnrichedTrade:
        """Build enriched trade context with all data fetched in parallel.

        Architecture: ONE gather call for all independent data sources.
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
        enriched.usd_value = round(enriched.price * enriched.size, 2)
        enriched.implied_probability = round(enriched.price * 100, 1)

        token_id = trade.get("asset", "")
        condition_id = trade.get("conditionId", "")
        slug = trade.get("slug", "")

        # ── Single parallel gather for ALL data ──
        results = await asyncio.gather(
            self._fetch_trades_cached(target.address),
            self._fetch_orderbook_fast(token_id),
            self._fetch_market_cached(condition_id, slug, trade),
            self._fetch_external_cached(enriched.market_title),
            return_exceptions=True,
        )

        trades_data: list[dict] = []
        for key, result in zip(
            ["trades", "orderbook", "market", "external"], results
        ):
            if isinstance(result, Exception):
                enriched.enrichment_errors.append(f"{key}: {result}")
                logger.debug(f"Enrichment error ({key}): {result}")
            elif key == "trades":
                trades_data = result or []
            elif key == "orderbook":
                enriched.orderbook = result
            elif key == "market":
                enriched.market = result
            elif key == "external" and result:
                enriched.external_price = result.get("price")
                enriched.external_source = result.get("source", "")
                if result.get("momentum_1s") is not None:
                    enriched.raw_trade["_ext_momentum_1s"] = result["momentum_1s"]

        # ── Derive profile + position from shared trades data (zero API) ──
        enriched.whale = self._build_profile(target, trades_data)
        enriched.position = self._build_position(
            condition_id, trade, trades_data
        )

        enriched.enrichment_latency_ms = (time.monotonic() - t0) * 1000
        return enriched

    # ── Cached trades fetch (shared by profile + position) ──

    async def _fetch_trades_cached(
        self, address: str
    ) -> list[dict]:
        """Fetch trades with 30s TTL cache to avoid duplicate calls."""
        cached = self._trades_cache.get(address)
        if cached and (time.monotonic() - cached[0]) < 30:
            return cached[1]

        trades = await self.api.get_trades(address, limit=100)
        self._trades_cache[address] = (time.monotonic(), trades)
        return trades

    # ── Fast orderbook (WS cache fallback) ────────────────

    async def _fetch_orderbook_fast(
        self, token_id: str
    ) -> OrderbookSnapshot:
        """Fetch orderbook; use WS price cache if fresh."""
        if not token_id:
            return OrderbookSnapshot()

        # Try WS cache for quick spread estimate
        ws_price = self._ws_prices.get(token_id)

        # Always fetch real orderbook for depth data
        book = await self.api.get_orderbook(token_id)
        return self._parse_orderbook(book, ws_price)

    @staticmethod
    def _parse_orderbook(
        book: dict, ws_price: float | None = None
    ) -> OrderbookSnapshot:
        """Parse orderbook into snapshot."""
        asks = book.get("asks", [])
        bids = book.get("bids", [])
        snap = OrderbookSnapshot()

        if asks:
            first = asks[0]
            snap.best_ask = float(
                first.get("price", 0) if isinstance(first, dict) else first[0]
            )
            for a in asks[:10]:
                p = float(a.get("price", 0) if isinstance(a, dict) else a[0])
                s = float(a.get("size", 0) if isinstance(a, dict) else a[1])
                snap.ask_depth_usd += p * s

        if bids:
            first = bids[0]
            snap.best_bid = float(
                first.get("price", 0) if isinstance(first, dict) else first[0]
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

    # ── Profile from shared trades (zero API calls) ──────

    def _build_profile(
        self, target: TargetAccount, trades: list[dict]
    ) -> WhaleProfile:
        """Build whale profile from already-fetched trades. No API calls."""
        cached = self._profile_cache.get(target.address)
        if cached and (time.monotonic() - cached.last_updated) < self.PROFILE_CACHE_TTL:
            return cached

        profile = WhaleProfile(nickname=target.nickname, address=target.address)

        if trades:
            profile.total_trades = len(trades)
            profile.total_volume = round(
                sum(
                    float(t.get("price", 0)) * float(t.get("size", 0))
                    for t in trades
                ),
                2,
            )

            good = sum(
                1 for t in trades
                if (t.get("side") == "BUY" and float(t.get("price", 1)) < 0.5)
                or (t.get("side") == "SELL" and float(t.get("price", 0)) > 0.5)
            )
            profile.win_rate = round(good / len(trades) * 100, 1) if trades else 0

        # Labels
        if profile.total_volume > 10_000:
            profile.labels.append("💰 High Volume")
        if profile.total_trades >= 50:
            profile.labels.append("⚡ Active")
        if profile.win_rate > 60:
            profile.labels.append("🎯 High WR")

        profile.last_updated = time.monotonic()
        self._profile_cache[target.address] = profile
        return profile

    # ── Position from shared trades (zero API calls) ─────

    @staticmethod
    def _build_position(
        condition_id: str,
        current_trade: dict,
        all_trades: list[dict],
    ) -> PositionContext:
        """Analyse position from already-fetched trades. No API calls."""
        ctx = PositionContext()
        if not condition_id:
            return ctx

        market_trades = [
            t for t in all_trades if t.get("conditionId") == condition_id
        ]
        if not market_trades:
            return ctx

        total_buy = sum(
            float(t.get("size", 0)) for t in market_trades
            if t.get("side") == "BUY"
        )
        total_sell = sum(
            float(t.get("size", 0)) for t in market_trades
            if t.get("side") == "SELL"
        )
        ctx.total_shares = round(total_buy - total_sell, 2)

        buy_value = sum(
            float(t.get("price", 0)) * float(t.get("size", 0))
            for t in market_trades if t.get("side") == "BUY"
        )
        if total_buy > 0:
            ctx.avg_price = round(buy_value / total_buy, 4)

        ctx.total_value_usd = round(
            ctx.total_shares * float(current_trade.get("price", 0)), 2
        )

        now_ts = int(current_trade.get("timestamp", 0))
        ctx.trade_count_recent = sum(
            1 for t in market_trades
            if now_ts - int(t.get("timestamp", 0)) < 600
        )

        current_side = current_trade.get("side", "BUY")
        existing_before = len(market_trades) > 1
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

    # ── Market metadata (TTL cached) ─────────────────────

    async def _fetch_market_cached(
        self,
        condition_id: str,
        slug: str,
        trade: dict,
    ) -> MarketContext:
        """Fetch market metadata with 15-minute TTL cache."""
        cache_key = condition_id or slug
        if cache_key:
            cached = self._market_cache.get(cache_key)
            if cached and (time.monotonic() - cached[0]) < self.MARKET_CACHE_TTL:
                return cached[1]

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
            ctx.resolved = bool(market_data.get("resolved", False))

            if ctx.end_date:
                try:
                    end = datetime.fromisoformat(
                        ctx.end_date.replace("Z", "+00:00")
                    )
                    delta = (end - datetime.now(UTC)).total_seconds() / 60
                    ctx.minutes_to_close = round(max(delta, 0), 1)
                    if delta <= 0:
                        ctx.resolved = True
                except (ValueError, TypeError):
                    pass

        if cache_key:
            self._market_cache[cache_key] = (time.monotonic(), ctx)
        return ctx

    # ── External price (PriceFeed → CoinGecko fallback) ──

    async def _fetch_external_cached(
        self,
        market_title: str,
    ) -> dict | None:
        """Get external price: OKX WSS (instant) → CoinGecko REST (fallback)."""
        title_upper = market_title.upper()
        asset = None
        if "BITCOIN" in title_upper or "BTC" in title_upper:
            asset = "BTC"
        elif "ETHEREUM" in title_upper or "ETH" in title_upper:
            asset = "ETH"
        elif "SOLANA" in title_upper or "SOL" in title_upper:
            asset = "SOL"

        if not asset:
            return None

        # ── Priority 1: OKX WebSocket (ms-level, zero latency) ──
        if self._price_feed and self._price_feed.is_fresh(asset, max_age_s=10):
            price = self._price_feed.get(asset)
            if price and price > 0:
                momentum = self._price_feed.momentum(asset, seconds=1.0)
                return {
                    "price": price,
                    "source": "OKX",
                    "symbol": f"{asset}/USDT",
                    "momentum_1s": momentum,
                }

        # ── Priority 2: TTL cache ──
        cached = self._ext_price_cache.get(asset)
        if cached and (time.monotonic() - cached[0]) < self.EXT_PRICE_CACHE_TTL:
            return cached[1]

        # ── Priority 3: CoinGecko REST (slow fallback) ──
        cg_ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        cg_id = cg_ids.get(asset)
        if not cg_id:
            return None

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3)
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
                            result = {
                                "price": price,
                                "source": "CoinGecko",
                                "symbol": f"{asset}/USD",
                            }
                            self._ext_price_cache[asset] = (
                                time.monotonic(), result
                            )
                            return result
        except Exception:
            pass

        return None
