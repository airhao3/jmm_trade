"""Application orchestrator – wires all components and runs the main loop."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from loguru import logger

from src.api.client import PolymarketClient
from src.api.price_feed import PriceFeed
from src.api.rate_limiter import TokenBucketRateLimiter
from src.api.websocket import PolymarketWebSocket
from src.config.models import AppConfig, TargetAccount
from src.core.alerts import AlertEngine, format_arbitrage_alert, format_momentum_alert
from src.core.enricher import TradeEnricher
from src.core.monitor import TradeMonitor
from src.core.portfolio import Portfolio
from src.core.profiler import SmartMoneyProfiler
from src.core.risk_manager import RiskManager, TradeSignal, format_risk_verdict
from src.core.settlement import SettlementEngine
from src.core.shadow import ShadowTracker
from src.core.simulator import TradeSimulator
from src.core.sizing import compute_position_size, format_sizing_summary
from src.data.database import Database
from src.data.export import export_trades_to_csv
from src.notifications.imessage import IMessageNotifier
from src.notifications.manager import EventType, NotificationManager
from src.notifications.rich_formatter import format_rich_trade_alert, format_sim_result
from src.notifications.telegram import TelegramNotifier
from src.notifications.telegram_bot import TelegramBotHandler
from src.utils.latency import LatencyChecker
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

        # WebSocket supplementary state
        self._ws_activity_flag: bool = False
        self._price_cache: dict[str, float] = {}
        self._burst_targets: set[str] = set()  # addresses to burst-poll
        self._asset_to_targets: dict[str, set[str]] = {}  # asset_id -> target addresses

        # Trade enricher & alert engine (initialised in run())
        self.enricher: TradeEnricher | None = None
        self.alert_engine: AlertEngine | None = None
        self.price_feed: PriceFeed | None = None
        self.profiler: SmartMoneyProfiler | None = None
        self.risk_manager: RiskManager = RiskManager()
        self.shadow_tracker: ShadowTracker | None = None

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

            # ── Network latency assessment ───────────────
            await self._assess_network_latency()

            # ── Startup connectivity test ────────────────
            await self._startup_connectivity_test(api)

            # ── Real-time price feed (OKX WSS) ────────────
            self.price_feed = PriceFeed(symbols=["BTC", "ETH", "SOL"])

            # ── Core components ──────────────────────────
            self.monitor = TradeMonitor(self.config, api)
            self.simulator = TradeSimulator(self.config, api, self.db)
            self.settlement = SettlementEngine(self.config, api, self.db)
            self.portfolio = Portfolio(self.db)
            self.profiler = SmartMoneyProfiler(api)
            self.enricher = TradeEnricher(
                api,
                ws_price_cache=self._price_cache,
                price_feed=self.price_feed,
            )

            # ── Startup target profiling ──────────────────
            for target in self.config.get_active_targets():
                profile = await self.profiler.profile(target)
                logger.info(
                    f"  [{target.nickname}] {profile.archetype.value} "
                    f"(follow={profile.follow_score}/10: {profile.follow_reason})"
                )

            # ── Alert engine ──────────────────────────────
            self.alert_engine = AlertEngine(api)
            self.alert_engine.on_arbitrage(self._on_arbitrage)
            self.alert_engine.on_momentum(self._on_momentum)

            # ── Notifications ────────────────────────────
            self.notifier = NotificationManager(self.config.notifications, self.db)
            if self.config.notifications.telegram.enabled:
                self.notifier.register_channel(TelegramNotifier(self.config.notifications.telegram))
            if self.config.notifications.imessage.enabled:
                self.notifier.register_channel(IMessageNotifier(self.config.notifications.imessage))

            # ── Telegram Bot commands ─────────────────
            self.telegram_bot: TelegramBotHandler | None = None
            if self.config.notifications.telegram.enabled:
                self.telegram_bot = TelegramBotHandler(self.config.notifications.telegram)
                await self.telegram_bot.start()

            # ── Shadow tracker (silent candidate monitoring) ──
            self.shadow_tracker = ShadowTracker(api, self.profiler)

            # ── Wire monitor -> simulator -> notifier ────
            self.monitor.on_new_trade(self._on_new_trade)

            # ── Launch concurrent tasks ──────────────────
            tasks = []

            # Real-time price feed (OKX WSS)
            tasks.append(self.price_feed.start())

            # Monitoring: always run poll loop for trade detection
            tasks.append(asyncio.create_task(self._poll_loop_with_metrics(), name="poll_loop"))

            # WebSocket: also run for real-time market data
            if self.config.monitoring.mode.value == "websocket":
                tasks.append(asyncio.create_task(self._run_websocket(), name="ws_loop"))

            # Alert engine (arbitrage + momentum)
            tasks.append(asyncio.create_task(self.alert_engine.run(), name="alert_engine"))

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
            if self.config.logging.files.metrics.enabled:
                tasks.append(asyncio.create_task(self.metrics.run(), name="metrics"))

            # Periodic portfolio log
            tasks.append(asyncio.create_task(self._periodic_portfolio_log(), name="portfolio_log"))

            # Auto CSV export
            if self.config.export.enabled:
                tasks.append(asyncio.create_task(self._periodic_export(), name="csv_export"))

            # Shadow tracking (silent candidate pool)
            tasks.append(
                asyncio.create_task(self.shadow_tracker.run(), name="shadow_track")
            )

            logger.info(f"Launched {len(tasks)} background tasks")

            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Application shutting down…")
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                if self.price_feed:
                    await self.price_feed.stop()
                if self.telegram_bot:
                    await self.telegram_bot.stop()
                if self.notifier:
                    await self.notifier.stop()
                await self.db.close()
                logger.info("Shutdown complete")

    # ── Callbacks ────────────────────────────────────────

    async def _on_new_trade(self, target: TargetAccount, trade: dict[str, Any]) -> None:
        """Called by the monitor when a qualifying trade is found.

        Pipeline: enrich → pre-flight filter → notify → simulate.
        Low-quality trades (small USD, resolved markets, high spread,
        negative PnL whales) are logged and skipped.
        """
        # ── Enrich trade data ──────────────────────────────
        enriched = None
        if self.enricher:
            try:
                enriched = await self.enricher.enrich(target, trade)
                logger.info(
                    f"[{target.nickname}] Trade enriched in "
                    f"{enriched.enrichment_latency_ms:.0f}ms "
                    f"(errors: {len(enriched.enrichment_errors)})"
                )
            except Exception:
                logger.exception("Trade enrichment failed, using basic notification")

        # ── Pre-flight quality gate ────────────────────────
        pf = None
        if self.enricher and enriched:
            pf = self.enricher.pre_flight(
                trade,
                whale=enriched.whale,
                orderbook=enriched.orderbook,
                market=enriched.market,
            )

            # Adverse momentum warning (even if trade passes)
            if pf.adverse_momentum and self.notifier:
                await self.notifier.notify(
                    EventType.NEW_TRADE,
                    {"_rich_message": (
                        "⚠️ ADVERSE MOMENTUM ALERT\n\n"
                        f"📊 {enriched.market_title}\n"
                        f"OKX price moving >0.5% AGAINST this position!\n"
                        f"Consider exiting or reducing exposure."
                    )},
                )

            if not pf.passed:
                logger.info(
                    f"[{target.nickname}] SKIPPED: {', '.join(pf.skip_reasons)} "
                    f"| {trade.get('title', '?')[:40]}"
                )
                return  # do not notify or simulate

        # ── Exit strategy: detect target reducing/exiting ────
        if enriched and enriched.position.position_change in ("EXIT", "REDUCE"):
            exit_msg = (
                f"🚨 EXIT SIGNAL\n\n"
                f"👤 {target.nickname} is "
                f"{'EXITING' if enriched.position.position_change == 'EXIT' else 'REDUCING'} "
                f"position!\n"
                f"📊 {enriched.market_title}\n"
                f"🔴 SELL {enriched.outcome} @ ${enriched.price:.4f}\n"
                f"💰 Size: ${enriched.usd_value:.2f}\n"
                f"📉 Remaining: {enriched.position.total_shares:.1f} shares\n\n"
                f"⚠️ Consider closing any copy positions in this market."
            )
            if self.notifier:
                await self.notifier.notify(
                    EventType.NEW_TRADE,
                    {"_rich_message": exit_msg},
                )
            logger.warning(
                f"[{target.nickname}] EXIT SIGNAL: "
                f"{enriched.position.position_change} on {enriched.market_title}"
            )

        # ── Send rich notification ─────────────────────────
        if self.notifier:
            if enriched:
                rich_msg = format_rich_trade_alert(enriched)
                await self.notifier.notify(
                    EventType.NEW_TRADE,
                    {"_rich_message": rich_msg},
                )
            else:
                await self.notifier.notify(
                    EventType.NEW_TRADE,
                    {
                        "nickname": target.nickname,
                        "side": trade.get("side"),
                        "title": trade.get("title"),
                        "price": trade.get("price"),
                    },
                )

        # ── Risk assessment (cross-target conflicts) ────────
        profile = self.profiler.get_cached(target.address) if self.profiler else None
        risk_verdict = None
        condition_id = trade.get("conditionId", "")
        outcome = trade.get("outcome", "")
        side = trade.get("side", "BUY")

        if condition_id:
            # Record this trade signal for other targets to see
            await self.risk_manager.record_signal(TradeSignal(
                address=target.address,
                nickname=target.nickname,
                condition_id=condition_id,
                market_title=trade.get("title", ""),
                side=side,
                outcome=outcome,
                price=float(trade.get("price", 0)),
                usd_value=float(trade.get("price", 0)) * float(trade.get("size", 0)),
                follow_score=profile.follow_score if profile else 5,
                archetype=profile.archetype.value if profile else "UNKNOWN",
                timestamp=time.monotonic(),
            ))

            # Assess risk against other active signals
            risk_verdict = await self.risk_manager.assess_risk(
                target_nickname=target.nickname,
                target_score=profile.follow_score if profile else 5,
                condition_id=condition_id,
                side=side,
                outcome=outcome,
            )

            if risk_verdict.reasons:
                risk_msg = format_risk_verdict(risk_verdict)
                logger.info(f"[{target.nickname}] {risk_msg}")
                if self.notifier and risk_verdict.action != "PROCEED":
                    await self.notifier.notify(
                        EventType.NEW_TRADE,
                        {"_rich_message": f"🛡️ {risk_msg}"},
                    )

            if risk_verdict.action == "SKIP":
                logger.info(
                    f"[{target.nickname}] Trade SKIPPED by risk manager: "
                    f"{risk_verdict.reasons}"
                )
                return

        # ── Dynamic position sizing ──────────────────────────
        sizing = compute_position_size(
            base_amount=self.config.simulation.investment_per_trade,
            profile=profile,
            trade=trade,
            pre_flight_score=pf.score if pf else 0,
        )

        # Apply risk multiplier
        if risk_verdict and risk_verdict.multiplier != 1.0:
            sizing.investment = round(sizing.investment * risk_verdict.multiplier, 2)
            sizing.reasons.append(
                f"Risk adj x{risk_verdict.multiplier:.1f} ({risk_verdict.action})"
            )

        logger.info(
            f"[{target.nickname}] {format_sizing_summary(sizing)}"
        )

        # ── Run simulation (skip if toxic spread) ──────────
        if pf and pf.skip_simulation:
            logger.info(
                f"[{target.nickname}] Simulation SKIPPED: spread breaker active "
                f"| {trade.get('title', '?')[:40]}"
            )
            return
        results = await self.simulator.simulate(
            target, trade, investment_override=sizing.investment
        )

        if self.metrics:
            self.metrics.total_trades += len(results)

        for sim in results:
            if sim.sim_success:
                if self.metrics and sim.slippage_pct is not None:
                    self.metrics.record_slippage(sim.slippage_pct)
                if self.notifier:
                    if enriched:
                        sim_msg = format_sim_result(
                            enriched,
                            sim.sim_delay,
                            sim.sim_price,
                            sim.slippage_pct,
                            sim.sim_success,
                        )
                        await self.notifier.notify(
                            EventType.SIM_EXECUTED,
                            {"_rich_message": sim_msg},
                        )
                    else:
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
                    if enriched:
                        sim_msg = format_sim_result(
                            enriched,
                            sim.sim_delay,
                            sim.sim_price,
                            sim.slippage_pct,
                            sim.sim_success,
                            sim.sim_failure_reason,
                        )
                        await self.notifier.notify(
                            EventType.SIM_FAILED,
                            {"_rich_message": sim_msg},
                        )
                    else:
                        await self.notifier.notify(
                            EventType.SIM_FAILED,
                            {
                                "delay": sim.sim_delay,
                                "reason": sim.sim_failure_reason,
                                "market": sim.market_name,
                            },
                        )

    # ── Alert callbacks ─────────────────────────────────

    async def _on_arbitrage(self, arb: Any) -> None:
        """Called by AlertEngine when an arbitrage opportunity is found."""
        if self.notifier:
            msg = format_arbitrage_alert(arb)
            await self.notifier.notify(EventType.NEW_TRADE, {"_rich_message": msg})

    async def _on_momentum(self, shift: Any) -> None:
        """Called by AlertEngine when a momentum shift is detected."""
        if self.notifier:
            msg = format_momentum_alert(shift)
            await self.notifier.notify(EventType.NEW_TRADE, {"_rich_message": msg})

    # ── Startup check ────────────────────────────────────

    async def _assess_network_latency(self) -> None:
        """Assess network latency to Polymarket APIs and determine copy trading viability."""
        logger.info("Assessing network latency to Polymarket APIs...")
        checker = LatencyChecker(timeout=10)
        results = await checker.check_polymarket_apis()
        checker.log_summary(results)

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

    # ── Adaptive per-target poll loops ───────────────────

    async def _poll_loop_with_metrics(self) -> None:
        """Launch independent poll loops per target with adaptive intervals.

        Intervals determined by SmartMoneyProfiler archetype:
          SNIPER  → 0.5s  (high value, catch immediately)
          WHALE   → 1.0s  (important)
          UNKNOWN → 2.0s  (default)
          SCALPER → 5.0s  (save rate limit)
          NOISE   → 10.0s (low priority)

        Each loop also supports burst mode: WS orderbook price-jump
        triggers an immediate poll for that target's tracked markets.
        """
        targets = self.config.get_active_targets()
        if not targets:
            logger.warning("No active targets configured")
            return

        # Build per-target poll intervals from profiler
        target_intervals: dict[str, float] = {}
        for t in targets:
            if self.profiler:
                prof = self.profiler._cache.get(t.address)
                interval = prof.poll_interval if prof else 2.0
            else:
                interval = self.config.monitoring.poll_interval
            target_intervals[t.address] = interval

        logger.info(
            f"Trade monitor started: adaptive polling for "
            f"{len(targets)} targets | "
            + ", ".join(
                f"{t.nickname}={target_intervals[t.address]}s"
                for t in targets
            )
        )

        # Launch independent per-target poll loops
        loops = [
            asyncio.create_task(
                self._adaptive_poll_target(t, target_intervals[t.address]),
                name=f"poll_{t.nickname}",
            )
            for t in targets
        ]
        await asyncio.gather(*loops)

    async def _adaptive_poll_target(
        self, target: TargetAccount, base_interval: float
    ) -> None:
        """Poll a single target at its adaptive interval.

        Supports burst mode via self._burst_targets set.
        """
        poll_num = 0
        while True:
            try:
                # Burst: WS price-jump flagged this target for immediate poll
                current_interval = base_interval
                if target.address in self._burst_targets:
                    current_interval = 0.3  # immediate burst
                    self._burst_targets.discard(target.address)

                # Also burst if global WS activity flag is set
                if self._ws_activity_flag:
                    current_interval = min(current_interval, 0.5)
                    self._ws_activity_flag = False

                new_count, latency = await self.monitor.poll_target_once(target)
                poll_num += 1

                if self.metrics:
                    self.metrics.polls_completed = self.monitor._poll_count
                    self.metrics.record_api_latency(latency)

                if new_count > 0:
                    logger.info(
                        f"[{target.nickname}] Poll #{poll_num}: "
                        f"{new_count} new trades ({latency * 1000:.0f}ms)"
                    )
                elif poll_num % 30 == 0:
                    logger.info(
                        f"[{target.nickname}] Poll #{poll_num}: "
                        f"idle ({latency * 1000:.0f}ms, "
                        f"interval={base_interval}s)"
                    )
            except Exception:
                logger.exception(f"[{target.nickname}] Poll error")
                if self.metrics:
                    self.metrics.increment_failed_requests()
            await asyncio.sleep(current_interval)

    # ── WebSocket (supplementary) ─────────────────────────

    async def _run_websocket(self) -> None:
        """WebSocket market channel for real-time price data.

        NOTE: This does NOT detect trades. The 'market' channel only provides
        orderbook/price updates. Trade detection is done by REST API polling.

        Purpose:
          1. Cache real-time prices for slippage calculation
          2. Detect market activity to trigger accelerated polling
        """
        ws = PolymarketWebSocket(self.config.api, channel="market")

        async def _handle_ws_message(data: dict[str, Any] | list[Any]) -> None:
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        await _process_market_event(item)
                return
            if isinstance(data, dict):
                await _process_market_event(data)

        async def _process_market_event(event: dict[str, Any]) -> None:
            event_type = event.get("event_type", "")

            if event_type == "last_trade_price":
                # A trade happened on this market – trigger burst poll
                asset_id = event.get("asset_id", "")
                price = event.get("price", "?")
                # Burst-poll all targets tracking this asset
                affected = self._asset_to_targets.get(asset_id, set())
                if affected:
                    self._burst_targets.update(affected)
                    logger.debug(
                        f"WS trade @ {price} → burst {len(affected)} targets "
                        f"(asset {str(asset_id)[:12]}...)"
                    )
                else:
                    self._ws_activity_flag = True

            elif event_type == "price_change":
                asset_id = event.get("asset_id", "")
                price = event.get("price")
                if asset_id and price:
                    p = float(price)
                    old_p = self._price_cache.get(asset_id)
                    self._price_cache[asset_id] = p

                    # Price-jump detection: >2% change → burst poll
                    if old_p and old_p > 0:
                        pct_change = abs(p - old_p) / old_p * 100
                        if pct_change > 2.0:
                            affected = self._asset_to_targets.get(asset_id, set())
                            self._burst_targets.update(affected)
                            logger.info(
                                f"⚡ Price jump {pct_change:.1f}% on "
                                f"{str(asset_id)[:12]}... → burst {len(affected)} targets"
                            )

                    # Feed price to alert engine for momentum detection
                    if self.alert_engine:
                        self.alert_engine.update_price(asset_id, p)

        ws.on_message(_handle_ws_message)

        # Discover active markets for target accounts
        subscribed = 0
        for target in self.config.get_active_targets():
            try:
                trades = await self.api.get_trades(target.address, limit=20)
                asset_ids = list({t.get("asset", "") for t in trades if t.get("asset")})
                if asset_ids:
                    await ws.subscribe(asset_ids)
                    subscribed += len(asset_ids)
                    # Build asset → target mapping for burst polling
                    for aid in asset_ids:
                        self._asset_to_targets.setdefault(aid, set()).add(
                            target.address
                        )
                    logger.info(
                        f"[{target.nickname}] WebSocket tracking {len(asset_ids)} "
                        f"active markets for price data"
                    )

                # Register market pairs for arbitrage scanning
                if self.alert_engine:
                    seen_conditions = set()
                    for t in trades:
                        cid = t.get("conditionId", "")
                        token = t.get("asset", "")
                        title = t.get("title", "")
                        if cid and token and cid not in seen_conditions:
                            seen_conditions.add(cid)
                            # Register as yes token; no token discovered later
                            self.alert_engine.track_market(
                                cid, token, "", title
                            )
            except Exception as exc:
                logger.warning(f"Failed to load markets for {target.nickname}: {exc}")

        if subscribed == 0:
            logger.warning("WebSocket: no markets to track, falling back to poll-only mode")
            return

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
