"""Dynamic position sizing engine.

Computes optimal position size based on:
  - Profiler follow_score (0-10)
  - Profiler confidence (0-1)
  - Whale's trade size (cap at 1% of whale's position)
  - Pre-flight score (signal strength)
  - Decay factor for borderline targets

Formula:
  Position = Base × (Score/10) × Confidence × Clamp(1%, whale_size)

Examples:
  POT_SNIPER score=10, conf=1.0, base=$100 → $100 (full)
  WHALE score=6, conf=0.8, base=$100 → $48
  NOISE score=2, conf=0.5, base=$100 → $10
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.profiler import Archetype, BehaviorProfile


@dataclass
class SizingResult:
    """Output of position sizing calculation."""

    investment: float          # final $ amount to invest
    base_amount: float         # configured base
    score_multiplier: float    # score/10
    confidence_multiplier: float
    whale_cap_applied: bool    # True if capped by whale's size
    whale_cap_amount: float    # cap value if applied
    decay_applied: bool        # True if target is borderline
    reasons: list[str]


# Maximum percentage of whale's trade to follow
WHALE_FOLLOW_PCT = 0.01   # 1% of whale's position
MIN_INVESTMENT = 5.0       # Never go below $5
DECAY_THRESHOLD_SCORE = 3  # Below this → apply 50% decay


def compute_position_size(
    base_amount: float,
    profile: BehaviorProfile | None = None,
    trade: dict[str, Any] | None = None,
    pre_flight_score: int = 0,
) -> SizingResult:
    """Compute dynamic position size.

    Args:
        base_amount: Configured base investment (e.g. $100)
        profile: SmartMoneyProfiler output for this target
        trade: The raw trade dict (for whale's size)
        pre_flight_score: Signal strength from pre-flight check
    """
    result = SizingResult(
        investment=base_amount,
        base_amount=base_amount,
        score_multiplier=1.0,
        confidence_multiplier=1.0,
        whale_cap_applied=False,
        whale_cap_amount=0.0,
        decay_applied=False,
        reasons=[],
    )

    if not profile:
        result.reasons.append("No profile → base amount")
        return result

    # ── Score multiplier: score/10 ──
    score = max(profile.follow_score, 0)
    result.score_multiplier = score / 10.0
    result.investment *= result.score_multiplier

    if score >= 8:
        result.reasons.append(f"🔥 High conviction (score={score}/10)")
    elif score >= 5:
        result.reasons.append(f"📊 Medium conviction (score={score}/10)")
    else:
        result.reasons.append(f"⚠️ Low conviction (score={score}/10)")

    # ── Confidence multiplier ──
    result.confidence_multiplier = max(profile.confidence, 0.2)
    result.investment *= result.confidence_multiplier

    if profile.confidence < 0.5:
        result.reasons.append(f"Low confidence ({profile.confidence:.1f})")

    # ── Pre-flight signal boost ──
    if pre_flight_score >= 4:
        boost = 1.0 + (pre_flight_score - 3) * 0.1  # +10% per point above 3
        result.investment *= boost
        result.reasons.append(f"Signal boost +{(boost-1)*100:.0f}%")

    # ── Whale size cap: never follow more than 1% of whale's position ──
    if trade:
        whale_usd = float(trade.get("price", 0)) * float(trade.get("size", 0))
        if whale_usd > 0:
            cap = whale_usd * WHALE_FOLLOW_PCT
            if cap > MIN_INVESTMENT and result.investment > cap:
                result.whale_cap_applied = True
                result.whale_cap_amount = round(cap, 2)
                result.investment = cap
                result.reasons.append(
                    f"Capped at 1% of whale (${whale_usd:,.0f} → ${cap:.2f})"
                )

    # ── Decay for borderline targets ──
    if score <= DECAY_THRESHOLD_SCORE and profile.archetype not in (
        Archetype.SNIPER, Archetype.POTENTIAL_SNIPER
    ):
        result.investment *= 0.5
        result.decay_applied = True
        result.reasons.append("Decay: borderline target (50%)")

    # ── Floor ──
    result.investment = round(max(result.investment, MIN_INVESTMENT), 2)

    return result


def format_sizing_summary(sizing: SizingResult) -> str:
    """Format sizing result for notification/logging."""
    lines = [
        f"💰 Position: ${sizing.investment:.2f} "
        f"(base=${sizing.base_amount:.0f} "
        f"× {sizing.score_multiplier:.1f} "
        f"× {sizing.confidence_multiplier:.1f})"
    ]
    if sizing.whale_cap_applied:
        lines.append(f"  🐋 Capped at whale 1%: ${sizing.whale_cap_amount:.2f}")
    if sizing.decay_applied:
        lines.append("  📉 Decay applied (borderline target)")
    return "\n".join(lines)
