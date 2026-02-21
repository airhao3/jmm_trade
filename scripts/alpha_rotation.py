"""Alpha Rotation — automated target lifecycle management.

Designed to run as a cron job (every 6 hours):
  1. Profile all current targets → identify losers (3+ consecutive losses)
  2. Run alpha discovery → find new high-value addresses
  3. Evict losers, promote new alphas into targets.json
  4. Log rotation summary

Usage:
  python -m scripts.alpha_rotation [--dry-run] [--max-evict N] [--min-score N]

Crontab example (every 6 hours):
  0 */6 * * * cd /path/to/JMM_trade && .venv/bin/python -m scripts.alpha_rotation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from scripts.discover_alpha import discover_alpha
from src.api.client import PolymarketClient
from src.api.rate_limiter import TokenBucketRateLimiter
from src.config.loader import load_config
from src.core.profiler import Archetype, SmartMoneyProfiler

TARGETS_PATH = Path("config/targets.json")
ROTATION_LOG = Path("logs/alpha_rotation.log")


# ── Eviction logic ───────────────────────────────────────


async def evaluate_targets(
    api: PolymarketClient,
    profiler: SmartMoneyProfiler,
    max_consecutive_losses: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Evaluate current targets. Return (keep, evict) lists.

    Eviction criteria:
      - 3+ consecutive recent losses (bought high on losing side)
      - Wash trade score > 0.5 (confirmed rebate farmer)
      - Follow score dropped to 0
    """
    targets_data = _load_targets()
    keep: list[dict] = []
    evict: list[dict] = []

    for t_data in targets_data:
        addr = t_data["address"]
        nick = t_data["nickname"]

        # Always keep reference targets
        if "REF" in nick.upper():
            keep.append(t_data)
            continue

        from src.config.models import TargetAccount
        target = TargetAccount(address=addr, nickname=nick, enabled=True)

        try:
            profile = await profiler.profile(target)
        except Exception as exc:
            logger.warning(f"  [{nick}] Profile failed: {exc}, keeping")
            keep.append(t_data)
            continue

        # Check consecutive losses from recent trades
        trades = await api.get_trades(addr, limit=20)
        consecutive_losses = _count_consecutive_losses(trades)

        should_evict = False
        reasons: list[str] = []

        if consecutive_losses >= max_consecutive_losses:
            should_evict = True
            reasons.append(f"{consecutive_losses} consecutive losses")

        if profile.wash_trade_score > 0.5:
            should_evict = True
            reasons.append(f"wash={profile.wash_trade_score:.2f}")

        if profile.follow_score <= 0:
            should_evict = True
            reasons.append(f"score={profile.follow_score}/10")

        if profile.archetype in (Archetype.NOISE, Archetype.SCALPER):
            should_evict = True
            reasons.append(f"archetype={profile.archetype.value}")

        if should_evict:
            evict.append({**t_data, "_evict_reasons": reasons})
            logger.info(
                f"  ❌ EVICT [{nick}]: {', '.join(reasons)}"
            )
        else:
            keep.append(t_data)
            logger.info(
                f"  ✅ KEEP [{nick}]: {profile.archetype.value} "
                f"score={profile.follow_score}/10 "
                f"losses={consecutive_losses}"
            )

    return keep, evict


def _count_consecutive_losses(trades: list[dict[str, Any]]) -> int:
    """Count consecutive recent losses (BUY at price > 0.5 = likely losing)."""
    consecutive = 0
    for t in sorted(trades, key=lambda x: -int(x.get("timestamp", 0))):
        price = float(t.get("price", 0))
        side = t.get("side", "")
        # A "loss" heuristic: BUY at > 0.60 (overpaying) or SELL at < 0.40
        if (side == "BUY" and price > 0.60) or (side == "SELL" and price < 0.40):
            consecutive += 1
        else:
            break  # streak broken
    return consecutive


# ── Rotation logic ────────────────────────────────────────


async def rotate(
    api: PolymarketClient,
    profiler: SmartMoneyProfiler,
    dry_run: bool = True,
    max_evict: int = 3,
    min_score: int = 5,
    max_targets: int = 8,
) -> dict[str, Any]:
    """Full rotation cycle: evaluate → evict → discover → promote."""
    logger.info("=" * 60)
    logger.info("ALPHA ROTATION CYCLE")
    logger.info("=" * 60)

    # Step 1: Evaluate current targets
    logger.info("\n📊 Evaluating current targets...")
    keep, evict = await evaluate_targets(api, profiler)

    # Cap evictions
    evict = evict[:max_evict]

    # Step 2: Discover new alphas if we have room
    slots_available = max_targets - len(keep)
    new_alphas: list[dict] = []

    if slots_available > 0:
        logger.info(f"\n🔍 Discovering alpha addresses ({slots_available} slots)...")
        candidates = await discover_alpha(api, profiler, top_n=slots_available + 5)

        # Filter: only promote if score >= min_score and not already tracked
        tracked_addrs = {t["address"].lower() for t in keep}
        for c in candidates:
            if len(new_alphas) >= slots_available:
                break
            if c["address"].lower() in tracked_addrs:
                continue
            if c.get("follow_score", 0) >= min_score:
                new_alphas.append(c)
    else:
        logger.info(f"\n📋 No slots available ({len(keep)} targets kept, max={max_targets})")

    # Step 3: Build new targets list
    new_targets = list(keep)
    for alpha in new_alphas:
        new_targets.append({
            "address": alpha["address"],
            "nickname": f"Alpha_{alpha['archetype']}_{alpha['rank']}",
            "enabled": True,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "notes": (
                f"Auto-discovered: profit=${alpha['estimated_profit']}, "
                f"score={alpha['follow_score']}/10, WR={alpha.get('win_rate', '?')}%"
            ),
        })

    # Summary
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kept": len(keep),
        "evicted": len(evict),
        "promoted": len(new_alphas),
        "total": len(new_targets),
        "evicted_addresses": [e["nickname"] for e in evict],
        "promoted_addresses": [a.get("address", "")[:16] for a in new_alphas],
    }

    logger.info(f"\n{'='*60}")
    logger.info("ROTATION SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"  Kept:     {summary['kept']}")
    logger.info(f"  Evicted:  {summary['evicted']} {summary['evicted_addresses']}")
    logger.info(f"  Promoted: {summary['promoted']} {summary['promoted_addresses']}")
    logger.info(f"  Total:    {summary['total']}")

    # Step 4: Write targets.json (unless dry-run)
    if dry_run:
        logger.info("\n⚠️ DRY RUN — no changes written to targets.json")
    else:
        _save_targets(new_targets)
        logger.info(f"\n✅ targets.json updated with {len(new_targets)} targets")

    # Log rotation
    _log_rotation(summary)

    return summary


# ── File I/O ──────────────────────────────────────────────


def _load_targets() -> list[dict]:
    if not TARGETS_PATH.exists():
        return []
    with open(TARGETS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("targets", [])


def _save_targets(targets: list[dict]) -> None:
    # Remove internal keys before saving
    clean = []
    for t in targets:
        clean.append({
            k: v for k, v in t.items() if not k.startswith("_")
        })
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGETS_PATH, "w", encoding="utf-8") as f:
        json.dump({"targets": clean}, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _log_rotation(summary: dict) -> None:
    ROTATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ROTATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")


# ── CLI ──────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha rotation cycle")
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Don't write changes (default: True)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually write changes to targets.json",
    )
    parser.add_argument(
        "--max-evict", type=int, default=3,
        help="Max targets to evict per cycle (default: 3)",
    )
    parser.add_argument(
        "--min-score", type=int, default=5,
        help="Min follow score to promote (default: 5)",
    )
    parser.add_argument(
        "--max-targets", type=int, default=8,
        help="Max total targets (default: 8)",
    )
    args = parser.parse_args()

    dry_run = not args.execute
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
        await rotate(
            api, profiler,
            dry_run=dry_run,
            max_evict=args.max_evict,
            min_score=args.min_score,
            max_targets=args.max_targets,
        )


if __name__ == "__main__":
    asyncio.run(main())
