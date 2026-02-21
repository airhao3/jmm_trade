"""Cross-target risk manager -- detects conflicts and amplifies signals.

Monitors all active trades across targets to detect:
  1. Self-competition: Two alphas on opposite sides of same market
  2. Hot-spot overload: Multiple alphas converging on same market
  3. Noise-reverse confirmation: NOISE target opposes Alpha -> strong signal
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class TradeSignal:
    """A trade signal from one target."""

    address: str
    nickname: str
    condition_id: str
    market_title: str
    side: str
    outcome: str
    price: float
    usd_value: float
    follow_score: int
    archetype: str
    timestamp: float


@dataclass
class RiskVerdict:
    """Risk assessment for a proposed trade."""

    action: str = "PROCEED"
    multiplier: float = 1.0
    reasons: list[str] = field(default_factory=list)
    conflicting_target: str | None = None
    convergence_count: int = 0
    noise_confirmed: bool = False


class RiskManager:
    """Tracks active signals across all targets for conflict detection."""

    SIGNAL_TTL = 600

    def __init__(self) -> None:
        self._signals: dict[str, list[TradeSignal]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def record_signal(self, signal: TradeSignal) -> None:
        """Record a new trade signal from a target."""
        async with self._lock:
            self._prune()
            self._signals[signal.condition_id].append(signal)

    async def assess_risk(
        self,
        target_nickname: str,
        target_score: int,
        condition_id: str,
        side: str,
        outcome: str,
    ) -> RiskVerdict:
        """Assess risk for a proposed trade given existing signals."""
        async with self._lock:
            self._prune()
            verdict = RiskVerdict()
            market_signals = self._signals.get(condition_id, [])

            if not market_signals:
                return verdict

            other_signals = [
                s for s in market_signals if s.nickname != target_nickname
            ]
            if not other_signals:
                return verdict

            # 1. Self-competition detection
            opposing = [
                s for s in other_signals
                if (s.side == "BUY" and side == "BUY" and s.outcome != outcome)
                or (s.side != side and s.outcome == outcome)
            ]

            if opposing:
                best = max(opposing, key=lambda s: s.follow_score)
                if best.follow_score > target_score:
                    verdict.action = "REDUCE"
                    verdict.multiplier = 0.3
                    verdict.conflicting_target = best.nickname
                    verdict.reasons.append(
                        f"CONFLICT: {best.nickname} "
                        f"(score={best.follow_score}) on opposite side"
                    )
                elif best.follow_score == target_score:
                    verdict.action = "SKIP"
                    verdict.multiplier = 0.0
                    verdict.conflicting_target = best.nickname
                    verdict.reasons.append(
                        f"EQUAL CONFLICT: {best.nickname} "
                        f"on opposite side -- skip to avoid fee burn"
                    )
                else:
                    verdict.reasons.append(
                        f"Weaker conflict: {best.nickname} "
                        f"(score={best.follow_score}) -- proceeding"
                    )

            # 2. Hot-spot convergence (same direction)
            aligned = [
                s for s in other_signals
                if s.side == side and s.outcome == outcome
            ]
            verdict.convergence_count = len(aligned) + 1
            if len(aligned) >= 2:
                verdict.action = "AMPLIFY"
                verdict.multiplier = min(1.5, 1.0 + len(aligned) * 0.2)
                names = [s.nickname for s in aligned[:3]]
                verdict.reasons.append(
                    f"CONVERGENCE: {len(aligned)+1} alphas aligned "
                    f"({', '.join(names)} + {target_nickname})"
                )

            # 3. Noise-reverse confirmation
            noise_opposing = [
                s for s in other_signals
                if s.archetype in ("NOISE", "SCALPER")
                and (
                    (s.side == "BUY" and side == "BUY" and s.outcome != outcome)
                    or (s.side != side and s.outcome == outcome)
                )
            ]
            if noise_opposing:
                verdict.noise_confirmed = True
                verdict.multiplier = max(verdict.multiplier, 1.3)
                names = [s.nickname for s in noise_opposing[:2]]
                verdict.reasons.append(
                    f"NOISE REVERSE: {', '.join(names)} on opposite side "
                    f"-- strong confirmation"
                )

            return verdict

    def _prune(self) -> None:
        """Remove stale signals."""
        cutoff = time.monotonic() - self.SIGNAL_TTL
        for cid in list(self._signals):
            self._signals[cid] = [
                s for s in self._signals[cid] if s.timestamp > cutoff
            ]
            if not self._signals[cid]:
                del self._signals[cid]

    @property
    def active_signal_count(self) -> int:
        return sum(len(v) for v in self._signals.values())


def format_risk_verdict(verdict: RiskVerdict) -> str:
    """Format risk verdict for notification."""
    if not verdict.reasons:
        return ""
    lines = ["Risk Assessment:"]
    for r in verdict.reasons:
        lines.append(f"  {r}")
    if verdict.action == "AMPLIFY":
        lines.append(f"  -> Position x{verdict.multiplier:.1f}")
    elif verdict.action == "REDUCE":
        lines.append(f"  -> Position x{verdict.multiplier:.1f}")
    elif verdict.action == "SKIP":
        lines.append("  -> SKIP this trade")
    return "\n".join(lines)
