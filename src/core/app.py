"""Application orchestrator – wires all components and runs the main loop."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.api.rate_limiter import TokenBucketRateLimiter
from src.api.websocket import PolymarketWebSocket
from src.config.models import AppConfig, TargetAccount
from src.core.monitor import TradeMonitor
from src.core.portfolio import Portfolio
from src.core.settlement import SettlementEngine
from src.core.simulator import TradeSimulator
from src.data.database import Database
from src.data.export import export_trades_to_csv
from src.notifications.imessage import IMessageNotifier
from src.notifications.manager import EventType, NotificationManager
from src.notifications.telegram import TelegramNotifier
from src.utils.metrics import MetricsCollector


class Application:
    """Top-level application that orchestrates all subsystems."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._assert_safety()

        # Components (initialised in run())
        self.db: Database | None = None
        self.api: PolymarketClient | None = None
        self.monitor: TradeMonitor | None = None
        self.simulator: TradeSimulator | None = None
        self.settlement: SettlementEngine | None = None
        self.portfolio: Portfolio | None = None
        self.notifier: NotificationManager | None = None
        self.metrics: MetricsCollector | None = None

    # ── Safety ───────────────────────────────────────────

    @staticmethod
    def _assert_safety() -> None:
        force = os.getenv("FORCE_READ_ONLY", "true").lower()
        if force != "true":
            logger.warning("FORCE_READ_ONLY is not 'true' – forcing it now")
            os.environ["FORCE_READ_ONLY"] = "true"

    # ── Run ──────────────────────────────────────────────

    async def run(self) -> None:
        """Start all subsystems and run until interrupted."""
        logger.info("=" * 60)
        logger.info("  Polymarket Copy Trader – SIMULATION MODE")
        logger.info("  READ_ONLY_MODE = True   (no real orders)")
        logger.info("=" * 60)

        # ── Database ─────────────────────────────────────
        self.db = Database(self.config.database)
        await self.db.connect()

        # Sync target accounts to DB
        for target in self.config.get_active_targets():
            await self.db.upsert_account(target.address, target.nickname, target.weight)

        # ── Rate limiter ─────────────────────────────────
        rl = TokenBucketRateLimiter(
            max_requests=self.config.api.rate_limit.max_requests,
            time_window=self.config.api.rate_limit.time_window,
            burst_size=self.config.api.rate_limit.burst_size,
        )

        # ── API client (context manager) ─────────────────
        async with PolymarketClient(self.config.api, self.config.system, rl) as api:
            self.api = api

            # ── Metrics ──────────────────────────────
            self.metrics = MetricsCollector(self.config.logging, self.db)
            self.metrics.active_accounts = len(self.config.get_active_targets())

            # ── Wire API latency -> metrics ──────────────
            api.set_latency_callback(self.metrics.record_api_latency)

            # ── Startup connectivity test ────────────────
            await self._startup_connectivity_test(api)

            # ── Core components ──────────────────────────
            self.monitor = TradeMonitor(self.config, api)
            self.simulator = TradeSimulator(self.config, api, self.db)
            self.settlement = SettlementEngine(self.config, api, self.db)
            self.portfolio = Portfolio(self.db)

            # ── Notifications ────────────────────────────
            self.notifier = NotificationManager(self.config.notifications, self.db)
            if self.config.notifications.telegram.enabled:
                self.notifier.register_channel(TelegramNotifier(self.config.notifications.telegram))
            if self.config.notifications.imessage.enabled:
                self.notifier.register_channel(IMessageNotifier(self.config.notifications.imessage))

            # ── Wire monitor -> simulator -> notifier ────
            self.monitor.on_new_trade(self._on_new_trade)

            # ── Launch concurrent tasks ──────────────────
            tasks = []

            # Monitoring (with metrics sync)
            if self.config.monitoring.mode.value == "poll":
                tasks.append(asyncio.create_task(self._poll_loop_with_metrics(), name="poll_loop"))
            else:
                tasks.append(asyncio.create_task(self._run_websocket(), name="ws_loop"))

            # Settlement
            tasks.append(
                asyncio.create_task(
                    self.settlement.settlement_loop(interval=60),
                    name="settlement",
                )
            )

            # Notifications
            if self.config.notifications.enabled:
                tasks.append(asyncio.create_task(self.notifier.run(), name="notifier"))

            # Metrics
            if self.config.logging.metrics_enabled:
                tasks.append(asyncio.create_task(self.metrics.run(), name="metrics"))

            # Periodic portfolio log
            tasks.append(asyncio.create_task(self._periodic_portfolio_log(), name="portfolio_log"))

            # Auto CSV export
            if self.config.export.enabled:
                tasks.append(asyncio.create_task(self._periodic_export(), name="csv_export"))

            logger.info(f"Launched {len(tasks)} background tasks")

            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Application shutting down…")
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                if self.notifier:
                    await self.notifier.stop()
                await self.db.close()
                logger.info("Shutdown complete")

    # ── Callbacks ────────────────────────────────────────

    async def _on_new_trade(self, target: TargetAccount, trade: dict[str, Any]) -> None:
        """Called by the monitor when a qualifying trade is found."""
        # Notify: new trade detected
        if self.notifier:
            await self.notifier.notify(
                EventType.NEW_TRADE,
                {
                    "nickname": target.nickname,
                    "side": trade.get("side"),
                    "title": trade.get("title"),
                    "price": trade.get("price"),
                },
            )

        # Run simulation
        results = await self.simulator.simulate(target, trade)

        if self.metrics:
            self.metrics.total_trades += len(results)

        for sim in results:
            if sim.sim_success:
                if self.metrics and sim.slippage_pct is not None:
                    self.metrics.record_slippage(sim.slippage_pct)
                if self.notifier:
                    await self.notifier.notify(
                        EventType.SIM_EXECUTED,
                        {
                            "delay": sim.sim_delay,
                            "sim_price": sim.sim_price,
                            "slippage": round(sim.slippage_pct or 0, 2),
                            "market": sim.market_name,
                        },
                    )
            else:
                if self.notifier:
                    await self.notifier.notify(
                        EventType.SIM_FAILED,
                        {
                            "delay": sim.sim_delay,
                            "reason": sim.sim_failure_reason,
                            "market": sim.market_name,
                        },
                    )

    # ── Startup check ────────────────────────────────────

    async def _startup_connectivity_test(self, api: PolymarketClient) -> None:
        """Test API connectivity and log latency before starting."""
        targets = self.config.get_active_targets()
        logger.info("Running startup connectivity test...")

        for target in targets:
            try:
                t0 = time.monotonic()
                trades = await api.get_trades(target.address, limit=3)
                lat = (time.monotonic() - t0) * 1000
                logger.info(
                    f"  [{target.nickname}] Data API: OK ({len(trades)} trades, {lat:.0f}ms)"
                )
                if trades:
                    t = trades[0]
                    logger.info(
                        f"    Latest: {t.get('side')} {t.get('title', '?')[:55]} @ {t.get('price')}"
                    )
                    # Test orderbook for the latest trade's token
                    token_id = t.get("asset", "")
                    if token_id:
                        t0 = time.monotonic()
                        book = await api.get_orderbook(token_id)
                        lat2 = (time.monotonic() - t0) * 1000
                        asks = book.get("asks", [])
                        bids = book.get("bids", [])
                        logger.info(
                            f"    Orderbook: {len(asks)} asks, {len(bids)} bids ({lat2:.0f}ms)"
                        )
            except Exception as exc:
                logger.error(f"  [{target.nickname}] Connectivity FAILED: {exc}")

        logger.info(f"Startup test complete (avg API latency: {api.avg_latency * 1000:.0f}ms)")

    # ── Poll loop with metrics sync ──────────────────────

    async def _poll_loop_with_metrics(self) -> None:
        """Wrap monitor.poll_loop to sync counters to metrics."""
        interval = self.config.monitoring.poll_interval
        logger.info(
            f"Monitor started: polling every {interval}s for "
            f"{len(self.config.get_active_targets())} targets"
        )
        while True:
            try:
                new_count, latency = await self.monitor.poll_once()

                # Sync to metrics
                if self.metrics:
                    self.metrics.polls_completed = self.monitor._poll_count
                    self.metrics.record_api_latency(latency)

                if new_count > 0:
                    logger.info(
                        f"Poll #{self.monitor._poll_count}: "
                        f"{new_count} new trades ({latency * 1000:.0f}ms)"
                    )
                elif self.monitor._poll_count % 20 == 0:
                    avg = (
                        (self.monitor._total_poll_latency / self.monitor._poll_count * 1000)
                        if self.monitor._poll_count
                        else 0
                    )
                    logger.info(
                        f"Poll #{self.monitor._poll_count}: "
                        f"no new trades ({latency * 1000:.0f}ms, "
                        f"avg={avg:.0f}ms)"
                    )
            except Exception:
                logger.exception("Unhandled error in poll cycle")
                if self.metrics:
                    self.metrics.increment_failed_requests()
            await asyncio.sleep(interval)

    # ── WebSocket mode ───────────────────────────────────

    async def _run_websocket(self) -> None:
        """Start WebSocket-based monitoring (placeholder for future)."""
        ws = PolymarketWebSocket(self.config.api, channel="market")

        async def _handle_ws_message(data: dict[str, Any] | list[Any]) -> None:
            if isinstance(data, list):
                logger.debug(f"WS message: list with {len(data)} items")
                return
            logger.debug(f"WS message: {data.get('event_type', 'unknown')}")

        ws.on_message(_handle_ws_message)

        # Discover active markets for target accounts
        for target in self.config.get_active_targets():
            try:
                positions = await self.api.get_positions(target.address)
                asset_ids = [p.get("asset", "") for p in positions if p.get("asset")]
                if asset_ids:
                    await ws.subscribe(asset_ids)
            except Exception as exc:
                logger.warning(f"Failed to load positions for {target.nickname}: {exc}")

        await ws.run()

    # ── Periodic tasks ───────────────────────────────────

    async def _periodic_portfolio_log(self) -> None:
        """Log portfolio summary every 5 minutes."""
        while True:
            await asyncio.sleep(300)
            try:
                await self.portfolio.log_portfolio_snapshot()
            except Exception:
                logger.debug("Portfolio snapshot failed")

    async def _periodic_export(self) -> None:
        """Auto-export sim trades to CSV at configured interval."""
        interval = self.config.export.auto_export_interval
        while True:
            await asyncio.sleep(interval)
            try:
                trades = await self.db.get_all_trades()
                if trades:
                    await export_trades_to_csv(trades, self.config.export)
            except Exception:
                logger.debug("Periodic CSV export failed")
