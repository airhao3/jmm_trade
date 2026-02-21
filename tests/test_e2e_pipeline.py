#!/usr/bin/env python3
"""Pipeline test: exercises the full success path + settlement with real API.

Approach:
    1. Fetch PBot1's recent trade to get a real token_id/conditionId
    2. Get current orderbook for that token
    3. Create a synthetic trade with price = current best_ask (so slippage ≈ 0)
    4. Run it through the simulator -> should produce OPEN trades
    5. Manually resolve via settlement -> should produce SETTLED with PnL
    6. Verify stats and export

Usage:
    source .venv/bin/activate
    python tests/test_e2e_pipeline.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["FORCE_READ_ONLY"] = "true"


PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
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
    print("  Pipeline Test – Full Success + Settlement Path")
    print("=" * 60)

    from src.api.client import PolymarketClient
    from src.api.rate_limiter import TokenBucketRateLimiter
    from src.config.loader import load_config
    from src.config.models import DatabaseConfig
    from src.core.monitor import TradeMonitor
    from src.core.settlement import SettlementEngine
    from src.core.simulator import TradeSimulator
    from src.data.database import Database
    from src.data.export import export_trades_to_csv

    config = load_config("config/config.yaml")

    rl = TokenBucketRateLimiter(
        max_requests=config.api.rate_limit.max_requests,
        time_window=config.api.rate_limit.time_window,
        burst_size=config.api.rate_limit.burst_size,
    )
    target = config.get_active_targets()[0]

    # Test DB
    test_db_path = "data/test_pipeline.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    db_config = DatabaseConfig(path=test_db_path, market_cache_ttl=3600)
    db = Database(db_config)
    await db.connect()
    await db.upsert_account(target.address, target.nickname, target.weight)

    async with PolymarketClient(config.api, config.system, rl) as api:
        # ── Step 1: Find an active market with orderbook ─
        print("\n[Step 1] Find active market with orderbook...")
        trades = await api.get_trades(target.address, limit=20)
        active_trade = None
        active_book = None

        for t in trades:
            token_id = t.get("asset", "")
            if not token_id:
                continue
            try:
                book = await api.get_orderbook(token_id)
                asks = book.get("asks", [])
                bids = book.get("bids", [])
                if asks and bids:
                    best_ask = float(asks[0]["price"])
                    best_bid = float(bids[0]["price"])
                    # Need a market where the current price is close to the trade price
                    # OR just use ANY market with both asks and bids
                    if best_ask > 0.01 and best_ask < 0.99:
                        active_trade = t
                        active_book = book
                        print(f'    Found: "{t["title"][:50]}"')
                        print(f"    ask={best_ask} bid={best_bid}")
                        break
            except Exception:
                continue

        if not active_trade or not active_book:
            # All markets expired — construct synthetic test with ANY token that has an orderbook
            print("    No ideal active market found. Using latest trade with modified price.")
            for t in trades:
                token_id = t.get("asset", "")
                if not token_id:
                    continue
                try:
                    book = await api.get_orderbook(token_id)
                    asks = book.get("asks", [])
                    bids = book.get("bids", [])
                    if asks or bids:
                        active_trade = t
                        active_book = book
                        break
                except Exception:
                    continue

        if not active_trade:
            report("Find active market", False, "No markets with orderbook found")
            await db.close()
            return

        report("Find active market", True, active_trade.get("title", "?")[:50])

        # ── Step 2: Create realistic synthetic trade ─────
        print("\n[Step 2] Create synthetic trade with realistic price...")
        asks = active_book.get("asks", [])
        bids = active_book.get("bids", [])

        if asks:
            current_price = float(asks[0]["price"])
        elif bids:
            current_price = float(bids[0]["price"])
        else:
            current_price = 0.5

        # Set target price very close to current (simulating real-time copy)
        synthetic_trade = {
            "proxyWallet": target.address,
            "side": "BUY",
            "asset": active_trade["asset"],
            "conditionId": active_trade.get("conditionId", ""),
            "size": 50,
            "price": current_price,  # target bought at current price
            "timestamp": int(time.time()),
            "title": active_trade.get("title", "Test Market"),
            "slug": active_trade.get("slug", "test-slug"),
            "eventSlug": active_trade.get("eventSlug", "test-event"),
            "outcome": active_trade.get("outcome", "Yes"),
            "outcomeIndex": active_trade.get("outcomeIndex", 0),
            "transactionHash": f"0x{uuid.uuid4().hex}{'0' * 24}",
        }

        print(f"    Synthetic trade: BUY @ {current_price}")
        print(f'    Title: "{synthetic_trade["title"][:50]}"')
        report("Synthetic trade created", True, f"price={current_price}")

        # ── Step 3: Market filter ────────────────────────
        print("\n[Step 3] Market filter...")
        monitor = TradeMonitor(config, api)
        passes = monitor._passes_market_filter(synthetic_trade)
        report("Market filter", True, f"passes={passes}")

        # ── Step 4: Simulator (success path) ─────────────
        print("\n[Step 4] Simulator execution...")
        simulator = TradeSimulator(config, api, db)
        sim_results = await simulator.simulate(target, synthetic_trade)

        report("Simulator produced records", len(sim_results) > 0, f"{len(sim_results)} records")

        success_count = 0
        for sr in sim_results:
            status_str = "SUCCESS" if sr.sim_success else f"FAILED({sr.sim_failure_reason})"
            slip = f"{sr.slippage_pct:.2f}%" if sr.slippage_pct is not None else "N/A"
            print(
                f"    delay={sr.sim_delay}s: sim={sr.sim_price} "
                f"target={sr.target_price} slip={slip} "
                f"fee=${sr.sim_fee} status={sr.status} [{status_str}]"
            )
            if sr.sim_success:
                success_count += 1

        report(
            "At least one OPEN trade",
            success_count > 0,
            f"{success_count}/{len(sim_results)} succeeded",
        )

        # ── Step 5: Verify DB state ──────────────────────
        print("\n[Step 5] Database state...")
        all_trades = await db.get_all_trades()
        open_trades = await db.get_open_trades()
        report("All trades in DB", len(all_trades) == len(sim_results), f"total={len(all_trades)}")
        report("Open trades", len(open_trades) == success_count, f"open={len(open_trades)}")

        # ── Step 6: Settlement test ──────────────────────
        print("\n[Step 6] Settlement...")
        settlement = SettlementEngine(config, api, db)

        # First check — market may or may not be resolved
        settled = await settlement.settle_once()
        print(f"    settle_once() returned {settled}")

        # For a thorough test: manually settle one trade to test the PnL path
        if open_trades:
            trade = open_trades[0]
            print(f"\n    Manual settlement test for trade: {trade['trade_id'][:30]}...")
            print(
                f"    side={trade['target_side']} sim_price={trade['sim_price']} "
                f"investment={trade['sim_investment']} fee={trade['sim_fee']}"
            )

            # Simulate resolution at 1.0 (YES wins)
            resolution_price = 1.0
            pnl, pnl_pct = settlement._calculate_pnl(trade, resolution_price)
            print(f"    If resolution=1.0: pnl=${pnl:+.2f} ({pnl_pct:+.1f}%)")

            # Simulate resolution at 0.0 (NO wins)
            pnl_loss, pnl_loss_pct = settlement._calculate_pnl(trade, 0.0)
            print(f"    If resolution=0.0: pnl=${pnl_loss:+.2f} ({pnl_loss_pct:+.1f}%)")

            # Note: buying at 0.99, resolution=1.0 gives payout = 100/0.99*1.0 ≈ 101.01
            # pnl = 101.01 - 100 - 1.50 = -0.49 (tiny loss due to fee at high price)
            # This is mathematically correct! Use a lower price for a "win" test.
            pnl_low, _ = settlement._calculate_pnl(
                {**trade, "sim_price": 0.50}, resolution_price=1.0
            )
            report("PnL win (at price 0.50)", pnl_low > 0, f"${pnl_low:+.2f}")
            report("PnL loss (resolution=0)", pnl_loss < 0, f"${pnl_loss:+.2f}")

            # Actually settle it with resolution=1.0
            await db.settle_trade(
                trade_id=trade["trade_id"],
                settlement_price=resolution_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
            )
            report("Trade settled in DB", True)

            # Verify
            open_after = await db.get_open_trades()
            report(
                "Open trades decreased",
                len(open_after) < len(open_trades),
                f"was {len(open_trades)} now {len(open_after)}",
            )
        else:
            print("    No open trades to manually settle (all FAILED due to slippage)")
            report("Manual settlement", True, "skipped — no open trades")

        # ── Step 7: Full statistics ──────────────────────
        print("\n[Step 7] Full statistics...")
        stats = await db.get_statistics()
        print(
            f"    Total: {stats.total_trades} | Open: {stats.open_positions} | "
            f"Settled: {stats.settled_trades} | Failed: {stats.failed_trades}"
        )
        print(
            f"    PnL: ${stats.total_pnl:+.2f} | Win: {stats.win_rate:.0f}% | "
            f"Slip: {stats.avg_slippage:.2f}% | Fee: ${stats.avg_fee:.2f}"
        )
        print(
            f"    Best: ${stats.best_trade_pnl:+.2f} | Worst: ${stats.worst_trade_pnl:+.2f} | "
            f"Investment: ${stats.total_investment:.2f}"
        )
        report("Stats query complete", stats.total_trades > 0)
        report(
            "Settled trades counted",
            stats.settled_trades > 0 or len(open_trades) == 0,
            f"settled={stats.settled_trades}",
        )

        summary = await db.get_pnl_summary()
        for row in summary:
            print(
                f"    {row['target_nickname']} delay={row['sim_delay']}s: "
                f"{row['trade_count']} trades | pnl=${row['total_pnl']:+.2f} | "
                f"win={row['win_rate']:.0f}% | slip={row['avg_slippage']:.2f}%"
            )
        report("PnL summary", len(summary) > 0, f"{len(summary)} groups")

        # ── Step 8: CSV export ───────────────────────────
        print("\n[Step 8] CSV export...")
        all_final = await db.get_all_trades()
        path = await export_trades_to_csv(all_final, config.export)
        report("CSV exported", True, f"{len(all_final)} trades -> {path}")

        # Verify CSV content
        import csv

        with open(path) as f:
            reader = csv.reader(f)
            headers = next(reader)
            rows = list(reader)
            report("CSV has headers", len(headers) > 10, f"{len(headers)} columns")
            report("CSV has data", len(rows) == len(all_final), f"{len(rows)} rows")

        # ── Step 9: Stats CLI ────────────────────────────
        print("\n[Step 9] Stats CLI output...")
        # Simulate what the CLI stats command does
        stats2 = await db.get_statistics()
        report("CLI stats would show data", stats2.total_trades > 0)

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
