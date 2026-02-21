"""Smart money profiler – behavioral analysis for target addresses.

Classifies tracked addresses into behavioral archetypes based on:
  - Trade frequency and size distribution
  - Win rate and PnL trajectory
  - Position holding duration
  - Cancel rate (order-to-trade ratio, if available)

Archetypes:
  SNIPER:     Low frequency, large size, high win rate → "insider" / informed
  SCALPER:    High frequency, small size → noise / market maker
  WHALE:      Large size, medium frequency → institutional
  NOISE:      Low volume, low win rate → uninformed retail
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.config.models import TargetAccount


class Archetype(StrEnum):
    """Behavioral archetype for a tracked address."""

    SNIPER = "SNIPER"                # Low freq, large size, high WR → follow aggressively
    POTENTIAL_SNIPER = "POT_SNIPER"  # High score but doesn't fit strict SNIPER template
    WHALE = "WHALE"                  # Large size, medium freq → follow with caution
    SCALPER = "SCALPER"              # High freq, small size → likely market maker, ignore
    NOISE = "NOISE"                  # Low volume, poor WR → skip entirely
    UNKNOWN = "UNKNOWN"


@dataclass
class BehaviorProfile:
    """Full behavioral analysis of a target address."""

    address: str = ""
    nickname: str = ""
    archetype: Archetype = Archetype.UNKNOWN

    # Trade statistics
    total_trades: int = 0
    avg_trade_usd: float = 0.0
    median_trade_usd: float = 0.0
    max_trade_usd: float = 0.0
    trades_per_hour: float = 0.0

    # Performance
    win_rate: float = 0.0
    estimated_pnl: float = 0.0

    # Behavior signals
    avg_hold_minutes: float = 0.0
    unique_markets: int = 0
    concentration_pct: float = 0.0  # % of volume in top market

    # Advanced signals
    accumulation_score: float = 0.0   # 0-1, drip-buying pattern strength
    wash_trade_score: float = 0.0     # 0-1, opposing trades in correlated mkts
    is_accumulating: bool = False     # active drip-buy detected

    # Confidence
    confidence: float = 0.0  # 0-1, based on sample size
    sample_size: int = 0

    # Follow recommendation
    follow_score: int = 0     # 0-10
    follow_reason: str = ""

    # Adaptive polling
    poll_interval: float = 2.0  # recommended seconds between polls

    last_updated: float = 0.0


class SmartMoneyProfiler:
    """Analyzes target addresses to determine if they're worth following.

    Uses only trade history (the one reliable Polymarket API) to build
    behavioral profiles without depending on broken leaderboard endpoints.
    """

    CACHE_TTL = 600  # 10 minutes

    def __init__(self, api: PolymarketClient) -> None:
        self.api = api
        self._cache: dict[str, BehaviorProfile] = {}

    async def profile(self, target: TargetAccount) -> BehaviorProfile:
        """Build or return cached behavioral profile."""
        cached = self._cache.get(target.address)
        if cached and (time.monotonic() - cached.last_updated) < self.CACHE_TTL:
            return cached

        trades = await self.api.get_trades(target.address, limit=200)
        profile = self._analyze(target, trades)

        self._cache[target.address] = profile
        return profile

    def get_cached(self, address: str) -> BehaviorProfile | None:
        """Return cached profile without API call (None if not cached)."""
        return self._cache.get(address)

    def _analyze(
        self, target: TargetAccount, trades: list[dict[str, Any]]
    ) -> BehaviorProfile:
        """Run full behavioral analysis on trade history."""
        p = BehaviorProfile(
            address=target.address,
            nickname=target.nickname,
            sample_size=len(trades),
        )

        if not trades:
            p.archetype = Archetype.UNKNOWN
            p.confidence = 0.0
            p.last_updated = time.monotonic()
            return p

        # ── Basic stats ──
        p.total_trades = len(trades)
        usd_values = []
        for t in trades:
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
            usd_values.append(price * size)

        usd_values.sort()
        p.avg_trade_usd = round(sum(usd_values) / len(usd_values), 2) if usd_values else 0
        p.median_trade_usd = round(usd_values[len(usd_values) // 2], 2) if usd_values else 0
        p.max_trade_usd = round(max(usd_values), 2) if usd_values else 0

        # ── Frequency ──
        timestamps = sorted(int(t.get("timestamp", 0)) for t in trades if t.get("timestamp"))
        if len(timestamps) >= 2:
            span_hours = max((timestamps[-1] - timestamps[0]) / 3600, 1)
            p.trades_per_hour = round(len(timestamps) / span_hours, 2)

        # ── Win rate ──
        good = sum(
            1 for t in trades
            if (t.get("side") == "BUY" and float(t.get("price", 1)) < 0.5)
            or (t.get("side") == "SELL" and float(t.get("price", 0)) > 0.5)
        )
        p.win_rate = round(good / len(trades) * 100, 1) if trades else 0

        # ── Market concentration ──
        market_volumes: dict[str, float] = {}
        for t in trades:
            cid = t.get("conditionId", "unknown")
            usd = float(t.get("price", 0)) * float(t.get("size", 0))
            market_volumes[cid] = market_volumes.get(cid, 0) + usd

        p.unique_markets = len(market_volumes)
        total_vol = sum(market_volumes.values())
        if total_vol > 0 and market_volumes:
            top_market_vol = max(market_volumes.values())
            p.concentration_pct = round(top_market_vol / total_vol * 100, 1)

        p.estimated_pnl = 0  # Cannot reliably calculate from trades alone

        # ── Confidence ──
        if p.total_trades >= 100:
            p.confidence = 1.0
        elif p.total_trades >= 50:
            p.confidence = 0.8
        elif p.total_trades >= 20:
            p.confidence = 0.5
        else:
            p.confidence = 0.2

        # ── Advanced signals ──
        p.accumulation_score, p.is_accumulating = self._detect_accumulation(trades)
        p.wash_trade_score = self._detect_wash_trading(trades)

        # ── Classify archetype ──
        p.archetype = self._classify(p)
        p.follow_score, p.follow_reason = self._score(p)
        p.poll_interval = self._assign_poll_interval(p)
        p.last_updated = time.monotonic()

        logger.info(
            f"[{target.nickname}] Profile: {p.archetype.value} "
            f"(score={p.follow_score}/10, WR={p.win_rate}%, "
            f"avg=${p.avg_trade_usd:.0f}, freq={p.trades_per_hour:.1f}/h, "
            f"markets={p.unique_markets}, "
            f"accum={p.accumulation_score:.2f}, wash={p.wash_trade_score:.2f}, "
            f"poll={p.poll_interval}s)"
        )
        return p

    @staticmethod
    def _classify(p: BehaviorProfile) -> Archetype:
        """Classify into archetype based on behavioral signals."""
        # SNIPER: low frequency, large trades, high win rate
        if (
            p.trades_per_hour < 5
            and p.avg_trade_usd > 100
            and p.win_rate > 55
            and p.unique_markets >= 3
        ):
            return Archetype.SNIPER

        # POTENTIAL_SNIPER: high score signals that don't fit strict SNIPER
        # (e.g. high WR + accumulation pattern, or high profit but mixed frequency)
        if (
            p.win_rate > 50
            and (p.accumulation_score > 0.4 or p.avg_trade_usd > 80)
            and p.wash_trade_score < 0.5
            and p.unique_markets >= 3
        ):
            return Archetype.POTENTIAL_SNIPER

        # WHALE: large trades, medium frequency
        if p.avg_trade_usd > 500 and p.total_trades >= 20:
            return Archetype.WHALE

        # SCALPER: high frequency, small trades (likely market maker)
        if p.trades_per_hour > 10 and p.avg_trade_usd < 50:
            return Archetype.SCALPER

        # NOISE: low volume, poor performance
        if p.avg_trade_usd < 20 or (p.win_rate < 45 and p.total_trades >= 30):
            return Archetype.NOISE

        return Archetype.UNKNOWN

    @staticmethod
    def _score(p: BehaviorProfile) -> tuple[int, str]:
        """Generate a 0-10 follow score with reason."""
        score = 5  # baseline
        reasons = []

        # Win rate
        if p.win_rate >= 65:
            score += 2
            reasons.append(f"High WR {p.win_rate}%")
        elif p.win_rate >= 55:
            score += 1
            reasons.append(f"Good WR {p.win_rate}%")
        elif p.win_rate < 45:
            score -= 2
            reasons.append(f"Low WR {p.win_rate}%")

        # Trade size
        if p.avg_trade_usd > 1000:
            score += 1
            reasons.append(f"Large avg ${p.avg_trade_usd:.0f}")
        elif p.avg_trade_usd < 20:
            score -= 2
            reasons.append("Micro trades")

        # Archetype bonus/penalty
        if p.archetype == Archetype.SNIPER:
            score += 2
            reasons.append("Sniper pattern")
        elif p.archetype == Archetype.POTENTIAL_SNIPER:
            score += 1
            reasons.append("🎯 Potential sniper")
        elif p.archetype == Archetype.SCALPER:
            score -= 3
            reasons.append("Scalper/MM pattern")
        elif p.archetype == Archetype.NOISE:
            score -= 3
            reasons.append("Noise trader")

        # Confidence penalty
        if p.confidence < 0.5:
            score -= 1
            reasons.append("Low confidence")

        # Market diversity (not concentrated in one market)
        if p.unique_markets >= 5:
            score += 1
            reasons.append("Diversified")

        # Accumulation bonus
        if p.is_accumulating:
            score += 2
            reasons.append("🎯 Active accumulation")
        elif p.accumulation_score > 0.5:
            score += 1
            reasons.append("Drip-buy pattern")

        # Wash trading penalty
        if p.wash_trade_score > 0.5:
            score -= 2
            reasons.append("⚠️ Wash trade suspect")
        elif p.wash_trade_score > 0.3:
            score -= 1
            reasons.append("Possible rebate farming")

        score = max(0, min(10, score))
        return score, " | ".join(reasons) if reasons else "Neutral"

    # ── Accumulation fingerprint ─────────────────────────

    @staticmethod
    def _detect_accumulation(
        trades: list[dict[str, Any]],
    ) -> tuple[float, bool]:
        """Detect drip-buying pattern (steady small buys over time).

        A WHALE splitting $2000 into 10x$200 over 10 minutes is MORE
        informative than a single $2000 buy — they're deliberately
        avoiding slippage because they expect further price movement.

        Returns (score 0-1, is_currently_accumulating).
        """
        if len(trades) < 5:
            return 0.0, False

        # Group BUY trades by conditionId in recent window (last 30 min)
        now_ts = max(int(t.get("timestamp", 0)) for t in trades)
        window = 30 * 60  # 30 minutes
        recent_buys: dict[str, list[dict]] = {}

        for t in trades:
            ts = int(t.get("timestamp", 0))
            if t.get("side") == "BUY" and (now_ts - ts) < window:
                cid = t.get("conditionId", "")
                if cid:
                    recent_buys.setdefault(cid, []).append(t)

        best_score = 0.0
        is_active = False

        for _cid, buys in recent_buys.items():
            if len(buys) < 3:
                continue

            # Check regularity: are the buys roughly evenly spaced?
            timestamps = sorted(int(b.get("timestamp", 0)) for b in buys)
            intervals = [
                timestamps[i + 1] - timestamps[i]
                for i in range(len(timestamps) - 1)
            ]

            if not intervals:
                continue

            avg_interval = sum(intervals) / len(intervals)
            if avg_interval <= 0:
                continue

            # Coefficient of variation of intervals (lower = more regular)
            variance = sum((iv - avg_interval) ** 2 for iv in intervals) / len(intervals)
            cv = (variance ** 0.5) / avg_interval if avg_interval > 0 else 999

            # Check size consistency
            sizes = [
                float(b.get("price", 0)) * float(b.get("size", 0))
                for b in buys
            ]
            avg_size = sum(sizes) / len(sizes) if sizes else 0
            size_cv = 0.0
            if avg_size > 0 and len(sizes) > 1:
                s_var = sum((s - avg_size) ** 2 for s in sizes) / len(sizes)
                size_cv = (s_var ** 0.5) / avg_size

            # Score: regular intervals + consistent sizes + enough buys
            regularity = max(0, 1 - cv)  # 1.0 = perfectly regular
            consistency = max(0, 1 - size_cv)  # 1.0 = all same size
            count_factor = min(len(buys) / 10, 1.0)  # caps at 10 buys

            score = round(regularity * 0.4 + consistency * 0.3 + count_factor * 0.3, 3)

            # Is it very recent? (last 5 min)
            last_buy_age = now_ts - timestamps[-1]
            if score > 0.4 and last_buy_age < 300:
                is_active = True

            best_score = max(best_score, score)

        return round(best_score, 3), is_active

    # ── Wash trading / rebate farming detection ──────────

    @staticmethod
    def _detect_wash_trading(trades: list[dict[str, Any]]) -> float:
        """Detect opposing trades in correlated/mutual-exclusive markets.

        If an address buys YES on "BTC Up" AND buys YES on "BTC Down"
        within a short window, they're likely wash trading for volume
        rebates rather than expressing a directional view.

        Returns score 0-1 (0 = clean, 1 = definite wash).
        """
        if len(trades) < 10:
            return 0.0

        # Group trades by base market (strip outcome from conditionId)
        # Use event slug or title prefix to match correlated markets
        window = 15 * 60  # 15 minutes

        # Build (market_key, side, outcome, timestamp) tuples
        entries: list[tuple[str, str, str, int]] = []
        for t in trades:
            title = t.get("title", "")
            side = t.get("side", "")
            outcome = t.get("outcome", "")
            ts = int(t.get("timestamp", 0))
            # Use first 30 chars of title as market key (strips outcome suffix)
            market_key = title[:30].strip() if title else ""
            if market_key and side:
                entries.append((market_key, side, outcome, ts))

        if len(entries) < 4:
            return 0.0

        # Find opposing entries in same market within window
        opposing_count = 0
        total_pairs = 0

        for i, (mk1, side1, out1, ts1) in enumerate(entries):
            for j in range(i + 1, len(entries)):
                mk2, side2, out2, ts2 = entries[j]
                if mk1 != mk2:
                    continue
                if abs(ts2 - ts1) > window:
                    continue

                total_pairs += 1
                # Same market, different outcomes, both BUY = hedge/wash
                if side1 == "BUY" and side2 == "BUY" and out1 != out2:
                    opposing_count += 1
                # Same market, opposite sides on same outcome = flip
                elif out1 == out2 and side1 != side2:
                    opposing_count += 1

        if total_pairs == 0:
            return 0.0

        return round(min(opposing_count / max(total_pairs, 1), 1.0), 3)

    # ── Adaptive poll interval ───────────────────────────

    @staticmethod
    def _assign_poll_interval(p: BehaviorProfile) -> float:
        """Assign poll interval based on archetype and activity level.

        SNIPER:  0.5s (high value, must catch immediately)
        WHALE:   1.0s (important but less urgent)
        UNKNOWN: 2.0s (default)
        SCALPER: 5.0s (low value, save rate limit)
        NOISE:  10.0s (waste of resources)
        """
        intervals = {
            Archetype.SNIPER: 0.5,
            Archetype.POTENTIAL_SNIPER: 0.5,
            Archetype.WHALE: 1.0,
            Archetype.UNKNOWN: 2.0,
            Archetype.SCALPER: 5.0,
            Archetype.NOISE: 10.0,
        }
        base = intervals.get(p.archetype, 2.0)

        # Boost if actively accumulating
        if p.is_accumulating:
            base = min(base, 0.5)

        return base
