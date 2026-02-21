"""Dashboard integration helper for Application class."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.utils.dashboard import LiveDashboard


class DashboardIntegration:
    """Helper class to integrate dashboard with application events."""

    def __init__(self, dashboard: LiveDashboard) -> None:
        self.dashboard = dashboard
        self.start_time = datetime.now()

    def _format_uptime(self) -> str:
        """Format uptime as 'Xh Ym'."""
        delta = datetime.now() - self.start_time
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        return f"{hours}h {minutes}m"

    def update_system_status(
        self,
        mode: str | None = None,
        investment: float | None = None,
        targets: int | None = None,
        database: str | None = None,
        websocket: str | None = None,
        telegram: str | None = None,
        api_latency: float | None = None,
        rating: str | None = None,
    ) -> None:
        """Update system status section."""
        updates = {"uptime": self._format_uptime()}

        if mode is not None:
            updates["mode"] = mode
        if investment is not None:
            updates["investment"] = investment
        if targets is not None:
            updates["targets"] = targets
        if database is not None:
            updates["database"] = database
        if websocket is not None:
            updates["websocket"] = websocket
        if telegram is not None:
            updates["telegram"] = telegram
        if api_latency is not None:
            updates["api_latency"] = api_latency
        if rating is not None:
            updates["rating"] = rating

        self.dashboard.update_system_status(**updates)

    def update_dashboard_stats(
        self,
        total_trades: int | None = None,
        open_positions: int | None = None,
        closed_positions: int | None = None,
        win_rate: float | None = None,
        total_pnl: float | None = None,
        best_trade: float | None = None,
        api_latency: float | None = None,
        failed_requests: int | None = None,
        websocket_status: str | None = None,
        telegram_status: str | None = None,
        database_status: str | None = None,
    ) -> None:
        """Update dashboard statistics."""
        updates = {"uptime": self._format_uptime()}

        if total_trades is not None:
            updates["total_trades"] = total_trades
        if open_positions is not None:
            updates["open_positions"] = open_positions
        if closed_positions is not None:
            updates["closed_positions"] = closed_positions
        if win_rate is not None:
            updates["win_rate"] = win_rate
        if total_pnl is not None:
            updates["total_pnl"] = total_pnl
        if best_trade is not None:
            updates["best_trade"] = best_trade
        if api_latency is not None:
            updates["api_latency"] = api_latency
        if failed_requests is not None:
            updates["failed_requests"] = failed_requests
        if websocket_status is not None:
            updates["websocket_status"] = websocket_status
        if telegram_status is not None:
            updates["telegram_status"] = telegram_status
        if database_status is not None:
            updates["database_status"] = database_status

        self.dashboard.update_dashboard(**updates)

    def add_trade_event(
        self,
        action: str,
        market: str,
        price: float,
        status: str = "OPEN",
        pnl: float = 0.0,
    ) -> None:
        """Add a trade to recent trades."""
        trade = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "market": market,
            "price": price,
            "status": status,
            "pnl": pnl,
        }
        self.dashboard.add_trade(trade)

    def add_event(
        self,
        event_type: str,
        message: str,
        details: list[str] | None = None,
    ) -> None:
        """Add an event to the event stream."""
        event = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "message": message,
            "details": details or [],
        }
        self.dashboard.add_event(event)

    def log_system_event(self, message: str) -> None:
        """Log a system event."""
        self.add_event("SYSTEM", message)

    def log_trade_detected(self, target: str, action: str, market: str, price: float, amount: float) -> None:
        """Log a new trade detection."""
        self.add_event(
            "TRADE",
            f"New trade detected from {target}",
            [
                f"Action: {action} | Market: {market[:40]}",
                f"Price: {price:.2f} | Amount: ${amount:.2f}",
            ],
        )

    def log_simulation_executed(self, delay: int, entry: float, fee: float, status: str) -> None:
        """Log a simulation execution."""
        self.add_event(
            "SIMULATION",
            f"Trade executed with {delay}s delay",
            [
                f"Entry: {entry:.2f} | Fee: ${fee:.2f}",
                f"Net: ${entry * 100 + fee:.2f} | Status: {status}",
            ],
        )

    def log_notification_sent(self, channel: str, recipient: str, status: str = "Delivered") -> None:
        """Log a notification sent."""
        self.add_event(
            "NOTIFY",
            f"{channel} notification sent",
            [f"Recipient: {recipient} | Status: {status}"],
        )

    def log_settlement(self, trade_id: int, market: str, result: str, pnl: float) -> None:
        """Log a market settlement."""
        self.add_event(
            "SETTLEMENT",
            f"Trade #{trade_id} settled | Result: {result}",
            [f"Market: {market[:40]}", f"PnL: ${pnl:+.2f}"],
        )

    def log_error(self, message: str, details: list[str] | None = None) -> None:
        """Log an error event."""
        self.add_event("ERROR", message, details)

    def log_warning(self, message: str, details: list[str] | None = None) -> None:
        """Log a warning event."""
        self.add_event("WARNING", message, details)

    async def periodic_stats_update(self, db, metrics, interval: int = 5) -> None:
        """Periodically update dashboard statistics from database and metrics."""
        while True:
            try:
                # Get stats from database
                stats = await db.get_statistics() if db else {}

                # Update dashboard
                self.update_dashboard_stats(
                    total_trades=stats.get("total_trades", 0),
                    open_positions=stats.get("open_positions", 0),
                    closed_positions=stats.get("closed_positions", 0),
                    win_rate=stats.get("win_rate", 0.0) * 100,
                    total_pnl=stats.get("total_pnl", 0.0),
                    best_trade=stats.get("best_trade", 0.0),
                    api_latency=metrics.avg_api_latency if metrics else 0.0,
                    failed_requests=metrics.failed_requests if metrics else 0,
                )

            except Exception as e:
                logger.debug(f"Dashboard stats update failed: {e}")

            await asyncio.sleep(interval)
