"""Shadow Tracking Engine -- zero-risk virtual execution for candidates.

Lifecycle: CANDIDATE -> SHADOW_VERIFIED (5+ trades or 12h) -> Promoted
Evicted real targets -> DEMOTED -> re-observation
Inactive / underperforming -> auto-cleaned
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.core.profiler import SmartMoneyProfiler

CANDIDATES_PATH = Path("config/candidates.json")
SHADOW_POLL_INTERVAL = 5.0
MIN_TRADES_VERIFIED = 5
MIN_HOURS_VERIFIED = 12.0
FEE_RATE = 0.015
INACTIVE_HOURS = 48.0
BASELINE_VWR = 38.0


class ShadowStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    SHADOW_VERIFIED = "SHADOW_VERIFIED"
    DEMOTED = "DEMOTED"


@dataclass
class VirtualTrade:
    """A single virtual (paper) trade."""

    timestamp: float = 0.0
    condition_id: str = ""
    market_title: str = ""
    side: str = "BUY"
    outcome: str = ""
    entry_price: float = 0.0
    size: float = 0.0
    usd_value: float = 0.0
    fee: float = 0.0
    exit_price: float | None = None
    pnl: float | None = None
    status: str = "OPEN"


@dataclass
class ShadowScorecard:
    """Dynamic evaluation metrics for a shadow address."""

    address: str = ""
    nickname: str = ""
    status: ShadowStatus = ShadowStatus.CANDIDATE
    added_at: float = 0.0
    added_at_iso: str = ""
    last_trade_at: float = 0.0

    open_positions: dict[str, VirtualTrade] = field(default_factory=dict)
    closed_trades: list[VirtualTrade] = field(default_factory=list)

    total_virtual_trades: int = 0
    virtual_wins: int = 0
    virtual_losses: int = 0
    total_v_profit: float = 0.0
    total_v_loss: float = 0.0
    profiler_score: int = 0
    archetype: str = "UNKNOWN"
    shadow_score: float = 0.0

    @property
    def vWR(self) -> float:
        total = self.virtual_wins + self.virtual_losses
        return round(self.virtual_wins / total * 100, 1) if total else 0.0

    @property
    def vProfitFactor(self) -> float:
        if self.total_v_loss == 0:
            return 10.0 if self.total_v_profit > 0 else 0.0
        return round(self.total_v_profit / abs(self.total_v_loss), 2)

    @property
    def consistency(self) -> float:
        if self.total_virtual_trades < 3 or self.virtual_wins == 0:
            return 0.0
        base = self.vWR / 100.0
        vol = min(self.total_virtual_trades / 10.0, 1.0)
        return round(base * vol, 2)

    @property
    def hours_in_pool(self) -> float:
        return round((time.monotonic() - self.added_at) / 3600, 1)

    @property
    def is_promotion_eligible(self) -> bool:
        return (
            self.status == ShadowStatus.SHADOW_VERIFIED
            and (
                self.total_virtual_trades >= MIN_TRADES_VERIFIED
                or self.hours_in_pool >= MIN_HOURS_VERIFIED
            )
        )


class ShadowTracker:
    """Manages the shadow candidate pool with virtual execution."""

    def __init__(
        self,
        api: PolymarketClient,
        profiler: SmartMoneyProfiler,
    ) -> None:
        self.api = api
        self.profiler = profiler
        self._scorecards: dict[str, ShadowScorecard] = {}
        self._seen_tx: set[str] = set()

    # ---- File I/O -------------------------------------------------

    def load_candidates(self) -> int:
        """Load candidates from config/candidates.json."""
        if not CANDIDATES_PATH.exists():
            return 0
        with open(CANDIDATES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        loaded = 0
        for c in data.get("candidates", []):
            addr = c.get("address", "")
            if not addr or addr in self._scorecards:
                continue
            sc = ShadowScorecard(
                address=addr,
                nickname=c.get("nickname", f"Shadow_{addr[:8]}"),
                status=ShadowStatus(c.get("status", "CANDIDATE")),
                added_at=time.monotonic(),
                added_at_iso=c.get("added_at", ""),
                profiler_score=int(c.get("shadow_score", 0)),
                shadow_score=float(c.get("shadow_score", 0)),
            )
            self._scorecards[addr] = sc
            loaded += 1
        return loaded

    def save_candidates(self) -> None:
        """Persist current scorecards back to candidates.json."""
        out = []
        for sc in self._scorecards.values():
            out.append({
                "address": sc.address,
                "nickname": sc.nickname,
                "status": sc.status.value,
                "added_at": sc.added_at_iso,
                "shadow_score": sc.shadow_score,
                "vWR": sc.vWR,
                "vProfitFactor": sc.vProfitFactor,
                "total_trades": sc.total_virtual_trades,
                "v_profit": round(sc.total_v_profit, 2),
                "v_loss": round(sc.total_v_loss, 2),
                "hours_in_pool": sc.hours_in_pool,
            })
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
            json.dump({"candidates": out}, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def add_candidate(
        self,
        address: str,
        nickname: str = "",
        status: ShadowStatus = ShadowStatus.CANDIDATE,
    ) -> None:
        """Add a new address to shadow pool."""
        if address in self._scorecards:
            return
        self._scorecards[address] = ShadowScorecard(
            address=address,
            nickname=nickname or f"Shadow_{address[:8]}",
            status=status,
            added_at=time.monotonic(),
            added_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def remove_candidate(self, address: str) -> None:
        """Remove address from shadow pool."""
        self._scorecards.pop(address, None)

    @property
    def candidate_count(self) -> int:
        return len(self._scorecards)

    @property
    def scorecards(self) -> dict[str, ShadowScorecard]:
        return self._scorecards

    # ---- Main loop ------------------------------------------------

    async def run(self) -> None:
        """Background task: silently poll all shadow candidates."""
        loaded = self.load_candidates()
        if not self._scorecards:
            logger.info("[SHADOW_TRACK] No candidates in pool, idle")
            return
        logger.info(
            f"[SHADOW_TRACK] Started: {loaded} candidates "
            f"@ {SHADOW_POLL_INTERVAL}s interval"
        )
        cycle = 0
        while True:
            try:
                await self._poll_cycle()
                cycle += 1
                if cycle % 12 == 0:
                    self.save_candidates()
            except Exception:
                logger.exception("[SHADOW_TRACK] Poll cycle error")
            await asyncio.sleep(SHADOW_POLL_INTERVAL)

    async def _poll_cycle(self) -> None:
        """One cycle: poll each shadow, virtual-execute new trades."""
        for _addr, sc in list(self._scorecards.items()):
            try:
                trades = await self.api.get_trades(sc.address, limit=10)
                for t in trades:
                    tx = t.get("transactionHash", "")
                    if not tx or tx in self._seen_tx:
                        continue
                    self._seen_tx.add(tx)
                    await self._capture_signal(sc, t)
                self._check_exits(sc, trades)
            except Exception:
                pass
        self._update_scores()
        self._lifecycle_maintenance()

    # ---- Virtual execution ----------------------------------------

    async def _capture_signal(
        self, sc: ShadowScorecard, trade: dict[str, Any]
    ) -> None:
        """Pessimistic virtual entry using Ask1 (BUY) or Bid1 (SELL)."""
        side = trade.get("side", "BUY")
        token_id = trade.get("asset", "")
        size = float(trade.get("size", 0))
        whale_price = float(trade.get("price", 0))
        if size <= 0 or whale_price <= 0:
            return

        entry_price = whale_price
        if token_id:
            try:
                book = await self.api.get_orderbook(token_id)
                levels = book.get("asks" if side == "BUY" else "bids", [])
                if levels:
                    first = levels[0]
                    entry_price = float(
                        first.get("price", whale_price)
                        if isinstance(first, dict) else first[0]
                    )
            except Exception:
                pass

        usd_value = entry_price * size
        fee = round(usd_value * FEE_RATE, 4)

        vt = VirtualTrade(
            timestamp=time.monotonic(),
            condition_id=trade.get("conditionId", ""),
            market_title=trade.get("title", ""),
            side=side,
            outcome=trade.get("outcome", ""),
            entry_price=entry_price,
            size=size,
            usd_value=usd_value,
            fee=fee,
        )
        sc.total_virtual_trades += 1
        sc.last_trade_at = time.monotonic()
        if side == "BUY":
            sc.open_positions[vt.condition_id] = vt
        logger.debug(
            f"[SHADOW_TRACK] {sc.nickname} vEntry: {side} "
            f"{trade.get('outcome', '?')} @ ${entry_price:.4f} "
            f"(whale@${whale_price:.4f}) ${usd_value:.2f}"
        )

    # ---- Exit detection -------------------------------------------

    def _check_exits(
        self, sc: ShadowScorecard, trades: list[dict[str, Any]]
    ) -> None:
        """Close virtual positions when shadow target SELLs."""
        for t in trades:
            if t.get("side") != "SELL":
                continue
            cid = t.get("conditionId", "")
            if cid not in sc.open_positions:
                continue
            vt = sc.open_positions.pop(cid)
            exit_price = float(t.get("price", 0))
            if exit_price <= 0:
                continue
            vt.exit_price = exit_price
            vt.status = "CLOSED"
            vt.pnl = round(
                (exit_price - vt.entry_price) * vt.size - vt.fee, 4
            )
            sc.closed_trades.append(vt)
            if vt.pnl > 0:
                sc.virtual_wins += 1
                sc.total_v_profit += vt.pnl
            else:
                sc.virtual_losses += 1
                sc.total_v_loss += abs(vt.pnl)
            logger.debug(
                f"[SHADOW_TRACK] {sc.nickname} vExit: "
                f"pnl=${vt.pnl:.2f} | {vt.market_title[:40]}"
            )

    # ---- Scoring --------------------------------------------------

    def _update_scores(self) -> None:
        """Recompute shadow_score for all candidates.

        Weights: 40% vWR + 30% profitFactor + 20% consistency + 10% profiler
        """
        for sc in self._scorecards.values():
            vwr_n = min(sc.vWR / 100.0, 1.0)
            pf_n = min(sc.vProfitFactor / 5.0, 1.0)
            cons_n = sc.consistency
            prof_n = min(sc.profiler_score / 10.0, 1.0)
            sc.shadow_score = round(
                vwr_n * 4.0 + pf_n * 3.0 + cons_n * 2.0 + prof_n * 1.0, 2
            )

    # ---- Lifecycle ------------------------------------------------

    def _lifecycle_maintenance(self) -> None:
        """Upgrade statuses, clean inactive/underperforming."""
        now = time.monotonic()
        to_remove: list[str] = []
        for addr, sc in self._scorecards.items():
            hours = (now - sc.added_at) / 3600
            # Upgrade CANDIDATE -> SHADOW_VERIFIED
            if sc.status == ShadowStatus.CANDIDATE and (
                sc.total_virtual_trades >= MIN_TRADES_VERIFIED
                or hours >= MIN_HOURS_VERIFIED
            ):
                sc.status = ShadowStatus.SHADOW_VERIFIED
                logger.info(
                    f"[SHADOW_TRACK] {sc.nickname} -> SHADOW_VERIFIED "
                    f"({sc.total_virtual_trades} trades, {hours:.1f}h)"
                )
            # Auto-clean: inactive
            inactive_h = (
                (now - sc.last_trade_at) / 3600 if sc.last_trade_at else hours
            )
            if inactive_h > INACTIVE_HOURS and sc.status != ShadowStatus.DEMOTED:
                to_remove.append(addr)
                continue
            # Auto-clean: worse than baseline after 5+ trades
            if sc.total_virtual_trades >= 5 and sc.vWR < BASELINE_VWR:
                to_remove.append(addr)
        for addr in to_remove:
            nick = self._scorecards[addr].nickname
            logger.info(f"[SHADOW_TRACK] Cleaning {nick}")
            del self._scorecards[addr]

    # ---- Promotion API --------------------------------------------

    def get_promotion_candidates(self, n: int = 3) -> list[ShadowScorecard]:
        """Top-N promotion-eligible shadow candidates by score."""
        eligible = [
            sc
            for sc in self._scorecards.values()
            if sc.is_promotion_eligible and sc.shadow_score > 0
        ]
        return sorted(eligible, key=lambda s: -s.shadow_score)[:n]

    def get_best_replacement(
        self, live_median: float
    ) -> ShadowScorecard | None:
        """Find shadow that outperforms live median (merit promotion)."""
        top = self.get_promotion_candidates(n=1)
        if top and top[0].shadow_score > live_median:
            return top[0]
        return None

    def promote(self, address: str) -> ShadowScorecard | None:
        """Remove from shadow pool and return scorecard for promotion."""
        return self._scorecards.pop(address, None)

    def demote(self, address: str, nickname: str = "") -> None:
        """Add evicted real target back to shadow for re-observation."""
        self.add_candidate(address, nickname, ShadowStatus.DEMOTED)
