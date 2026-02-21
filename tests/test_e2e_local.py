#!/usr/bin/env python3
"""End-to-end local test: exercises the full pipeline with real API data.

Usage:
    source .venv/bin/activate
    python tests/test_e2e_local.py

Tests (in order):
    1. Config loading and validation
    2. API connectivity (Data, CLOB, Gamma)
    3. Market filter against real trade titles
    4. Simulator: inject real trade -> orderbook snapshot -> slippage/fee
    5. Database: verify records persisted
    6. Settlement: check resolved markets
    7. Portfolio stats
    8. CSV export
    9. Stats CLI output
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FORCE_READ_ONLY"] = "true"


# ── Helpers ──────────────────────────────────────────────

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"
results = []


def report(name: str, ok: bool, detail: str = ""):
    tag = PASS if ok else FAIL
    msg = f"  {tag}  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((name, ok, detail))


async def main():
    print("=" * 60)
    print("  E2E Local Test – Polymarket Copy Trader")
    print("=" * 60)

    # ── 1. Config ────────────────────────────────────────
    print("\n[1/9] Config loading...")
    try:
        from src.config.loader import load_config

        config = load_config("config/config.yaml")
        report("Config loaded", True, f"{len(config.get_active_targets())} targets")
        report("Read-only enforced", config.system.read_only_mode is True)
        report(
            "Delays configured", len(config.simulation.delays) > 0, str(config.simulation.delays)
        )
    except Exception as e:
        report("Config loaded", False, str(e))
        print("Cannot continue without config. Exiting.")
        return

    # ── 2. API Connectivity ──────────────────────────────
    print("\n[2/9] API connectivity...")
    from src.api.client import PolymarketClient
    from src.api.rate_limiter import TokenBucketRateLimiter

    rl = TokenBucketRateLimiter(
        max_requests=config.api.rate_limit.max_requests,
        time_window=config.api.rate_limit.time_window,
        burst_size=config.api.rate_limit.burst_size,
    )

    target = config.get_active_targets()[0]
    trades_data = []
    orderbook_data = {}
    market_data = {}

    async with PolymarketClient(config.api, config.system, rl) as api:
        # 2a. Data API - /trades
        try:
            t0 = time.monotonic()
            trades_data = await api.get_trades(target.address, limit=10)
            lat = (time.monotonic() - t0) * 1000
            report(
                "Data API /trades", len(trades_data) > 0, f"{len(trades_data)} trades, {lat:.0f}ms"
            )
        except Exception as e:
            report("Data API /trades", False, str(e))

        # 2b. CLOB API - /book
        if trades_data:
            token_id = trades_data[0].get("asset", "")
            if token_id:
                try:
                    t0 = time.monotonic()
                    orderbook_data = await api.get_orderbook(token_id)
                    lat = (time.monotonic() - t0) * 1000
                    asks = orderbook_data.get("asks", [])
                    bids = orderbook_data.get("bids", [])
                    report(
                        "CLOB API /book", True, f"{len(asks)} asks, {len(bids)} bids, {lat:.0f}ms"
                    )
                except Exception as e:
                    report("CLOB API /book", False, str(e))

        # 2c. Gamma API - /markets
        if trades_data:
            cond_id = trades_data[0].get("conditionId", "")
            if cond_id:
                try:
                    t0 = time.monotonic()
                    market_data = await api.get_market(cond_id)
                    lat = (time.monotonic() - t0) * 1000
                    report(
                        "Gamma API /markets",
                        market_data is not None,
                        f"slug={market_data.get('slug', '?')[:30]}, {lat:.0f}ms",
                    )
                except Exception as e:
                    report("Gamma API /markets", False, str(e))

        # ── 3. Market Filter ─────────────────────────────
        print("\n[3/9] Market filter against real trades...")
        from src.core.monitor import TradeMonitor

        monitor = TradeMonitor(config, api)

        pass_count = 0
        fail_count = 0
        for trade in trades_data:
            title = trade.get("title", "?")
            passes = monitor._passes_market_filter(trade)
            if passes:
                pass_count += 1
            else:
                fail_count += 1
            print(f'    {"✓" if passes else "✗"} "{title[:60]}"')

        report(
            "Market filter tested",
            True,
            f"{pass_count} pass, {fail_count} filtered out of {len(trades_data)}",
        )

        # ── 4. Simulator (inject real trade) ─────────────
        print("\n[4/9] Simulator: inject trade through pipeline...")
        from src.config.models import DatabaseConfig
        from src.data.database import Database

        # Use a test DB
        test_db_path = "data/test_e2e.db"
        test_db_config = DatabaseConfig(path=test_db_path, market_cache_ttl=3600)
        db = Database(test_db_config)
        await db.connect()
        await db.upsert_account(target.address, target.nickname, target.weight)

        from src.core.simulator import TradeSimulator

        simulator = TradeSimulator(config, api, db)

        # Pick a real trade to simulate
        test_trade = trades_data[0] if trades_data else None
        sim_results = []

        if test_trade:
            trade_title = test_trade.get("title", "?")
            trade_side = test_trade.get("side", "?")
            trade_price = test_trade.get("price", "?")
            print(f'    Injecting: {trade_side} "{trade_title[:50]}" @ {trade_price}')

            try:
                sim_results = await simulator.simulate(target, test_trade)
                report(
                    "Simulator executed", len(sim_results) > 0, f"{len(sim_results)} sim records"
                )

                for sr in sim_results:
                    status = "OK" if sr.sim_success else f"FAILED: {sr.sim_failure_reason}"
                    slip = f"{sr.slippage_pct:.2f}%" if sr.slippage_pct is not None else "N/A"
                    print(
                        f"    delay={sr.sim_delay}s: sim_price={sr.sim_price} "
                        f"slip={slip} fee=${sr.sim_fee} cost=${sr.total_cost} [{status}]"
                    )

                report(
                    "Sim prices populated",
                    all(sr.sim_price is not None or not sr.sim_success for sr in sim_results),
                )
                report(
                    "Slippage calculated",
                    all(sr.slippage_pct is not None or not sr.sim_success for sr in sim_results),
                )
                report("Fees calculated", all(sr.sim_fee is not None for sr in sim_results))
            except Exception as e:
                report("Simulator executed", False, str(e))
                import traceback

                traceback.print_exc()
        else:
            report("Simulator executed", False, "No trades available to inject")

        # ── 5. Database verification ─────────────────────
        print("\n[5/9] Database records...")
        try:
            all_trades = await db.get_all_trades()
            open_trades = await db.get_open_trades()
            report(
                "Trades persisted in DB",
                len(all_trades) > 0,
                f"{len(all_trades)} total ({len(open_trades)} open)",
            )

            for ot in all_trades[:3]:
                print(
                    f"    id={ot['trade_id'][:20]}... side={ot['target_side']} "
                    f"delay={ot['sim_delay']}s status={ot['status']}"
                )

            # Check trade_exists
            if sim_results:
                exists = await db.trade_exists(sim_results[0].trade_id)
                report("trade_exists() works", exists is True)
        except Exception as e:
            report("Trades persisted in DB", False, str(e))
            import traceback

            traceback.print_exc()

        # ── 6. Settlement ────────────────────────────────
        print("\n[6/9] Settlement engine...")
        from src.core.settlement import SettlementEngine

        settlement = SettlementEngine(config, api, db)

        try:
            settled_count = await settlement.settle_once()
            report("Settlement ran", True, f"{settled_count} trades settled")

            # Check if any trades changed status
            open_after = await db.get_open_trades()
            settled_diff = len(open_trades) - len(open_after)
            if settled_diff > 0:
                report("Trades settled with PnL", True, f"{settled_diff} newly settled")
            else:
                report("No resolved markets yet", True, "expected for active markets")
        except Exception as e:
            report("Settlement ran", False, str(e))
            import traceback

            traceback.print_exc()

        # ── 7. Portfolio / Statistics ────────────────────
        print("\n[7/9] Statistics...")
        try:
            stats = await db.get_statistics()
            report(
                "Statistics query",
                True,
                f"trades={stats.total_trades} open={stats.open_positions} "
                f"pnl=${stats.total_pnl:+.2f}",
            )
            print(
                f"    Total: {stats.total_trades} | Open: {stats.open_positions} | "
                f"Settled: {stats.settled_trades} | Failed: {stats.failed_trades}"
            )
            print(
                f"    PnL: ${stats.total_pnl:+.2f} | Win rate: {stats.win_rate:.1f}% | "
                f"Avg slip: {stats.avg_slippage:.2f}%"
            )
        except Exception as e:
            report("Statistics query", False, str(e))
            import traceback

            traceback.print_exc()

        # PnL summary
        try:
            summary = await db.get_pnl_summary()
            report("PnL summary query", True, f"{len(summary)} groups")
            for row in summary:
                print(
                    f"    {row['target_nickname']} delay={row['sim_delay']}s: "
                    f"{row['trade_count']} trades, pnl=${row['total_pnl']:+.2f}"
                )
        except Exception as e:
            report("PnL summary query", False, str(e))
            import traceback

            traceback.print_exc()

        # ── 8. CSV Export ────────────────────────────────
        print("\n[8/9] CSV export...")
        try:
            from src.data.export import export_trades_to_csv

            all_trades = await db.get_all_trades()
            if all_trades:
                path = await export_trades_to_csv(all_trades, config.export)
                report("CSV export", True, f"-> {path}")
            else:
                report("CSV export", False, "No trades to export")
        except Exception as e:
            report("CSV export", False, str(e))
            import traceback

            traceback.print_exc()

        # ── 9. Simulate a second trade (dedup test) ──────
        print("\n[9/9] Deduplication test...")
        if test_trade:
            try:
                dup_results = await simulator.simulate(target, test_trade)
                report(
                    "Duplicate rejected",
                    len(dup_results) == 0,
                    f"returned {len(dup_results)} (expected 0)",
                )
            except Exception as e:
                report("Duplicate rejected", False, str(e))

        # ── Cleanup ──────────────────────────────────────
        await db.close()

    # ── Summary ──────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    color = "\033[92m" if failed == 0 else "\033[91m"
    print(f"  {color}Results: {passed}/{total} passed, {failed} failed\033[0m")
    print("=" * 60)

    if failed > 0:
        print("\nFailed tests:")
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name}: {detail}")


if __name__ == "__main__":
    asyncio.run(main())
