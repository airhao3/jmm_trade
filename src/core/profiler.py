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

    SNIPER = "SNIPER"      # Low freq, large size, high WR → follow aggressively
    WHALE = "WHALE"        # Large size, medium freq → follow with caution
    SCALPER = "SCALPER"    # High freq, small size → likely market maker, ignore
    NOISE = "NOISE"        # Low volume, poor WR → skip entirely
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

    # Confidence
    confidence: float = 0.0  # 0-1, based on sample size
    sample_size: int = 0

    # Follow recommendation
    follow_score: int = 0     # 0-10
    follow_reason: str = ""

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

        # ── Classify archetype ──
        p.archetype = self._classify(p)
        p.follow_score, p.follow_reason = self._score(p)
        p.last_updated = time.monotonic()

        logger.info(
            f"[{target.nickname}] Profile: {p.archetype.value} "
            f"(score={p.follow_score}/10, WR={p.win_rate}%, "
            f"avg=${p.avg_trade_usd:.0f}, freq={p.trades_per_hour:.1f}/h, "
            f"markets={p.unique_markets})"
        )
        return p

    @staticmethod
    def _classify(p: BehaviorProfile) -> Archetype:
        """Classify into archetype based on behavioral signals."""
        # SNIPER: low frequency, large trades, high win rate
        if (
            p.trades_per_hour < 2
            and p.avg_trade_usd > 200
            and p.win_rate > 55
            and p.unique_markets >= 3
        ):
            return Archetype.SNIPER

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

        score = max(0, min(10, score))
        return score, " | ".join(reasons) if reasons else "Neutral"
