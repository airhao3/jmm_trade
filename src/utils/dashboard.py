"""Live dashboard for single-page real-time display.

Provides a fixed-layout terminal UI that updates in place without scrolling.
Uses Rich library for professional terminal rendering with colors and tables.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config.models import ConsoleConfig


class LiveDashboard:
    """Single-page live dashboard with real-time updates."""

    def __init__(self, config: ConsoleConfig) -> None:
        self.config = config
        self.console = Console()
        self.layout = Layout()

        # Data storage
        self.system_status: dict[str, Any] = {
            "mode": "WebSocket",
            "investment": 100.0,
            "targets": 1,
            "database": "Connected",
            "websocket": "Connected",
            "telegram": "Active",
            "uptime": "0h 0m",
            "api_latency": 0.0,
            "rating": "GOOD",
        }

        self.dashboard_data: dict[str, Any] = {
            "target_name": "PBot1",
            "target_address": "0x88f4...d4db",
            "status": "ACTIVE",
            "total_trades": 0,
            "open_positions": 0,
            "closed_positions": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "best_trade": 0.0,
            "api_latency": 0.0,
            "failed_requests": 0,
            "websocket_status": "Connected",
            "telegram_status": "Active",
            "database_status": "Healthy",
            "uptime": "0h 0m",
        }

        self.recent_trades: deque = deque(maxlen=config.layout.limits.get("recent_trades", 5))
        self.event_stream: deque = deque(maxlen=config.layout.limits.get("event_stream", 10))

        self._create_layout()

    def _create_layout(self) -> None:
        """Create fixed layout structure."""
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="system", size=5),
            Layout(name="dashboard", size=10),
            Layout(name="trades", size=9),
            Layout(name="events", size=15),
            Layout(name="footer", size=3),
        )

    def _render_header(self) -> Panel:
        """Render header with title and timestamp."""
        now = datetime.now().strftime("%H:%M:%S")
        title = Text()
        title.append("POLYMARKET COPY TRADER - SIMULATION MODE", style="bold bright_white")
        title.append(f"                    [{now}]", style="cyan")

        return Panel(
            title,
            border_style="bright_blue",
            padding=(0, 1),
        )

    def _render_system_status(self) -> Panel:
        """Render system status bar."""
        status = self.system_status

        content = Text()
        content.append("Mode: ", style="white")
        content.append(f"{status['mode']:<15}", style="bright_cyan")
        content.append("Investment: ", style="white")
        content.append(f"${status['investment']:<10.0f}", style="bright_yellow")
        content.append("Targets: ", style="white")
        content.append(f"{status['targets']}\n", style="bright_green")

        content.append("Database: ", style="white")
        content.append(f"{status['database']:<12}", style="green" if status['database'] == "Connected" else "red")
        content.append("WebSocket: ", style="white")
        content.append(f"{status['websocket']:<12}", style="green" if status['websocket'] == "Connected" else "red")
        content.append("Telegram: ", style="white")
        content.append(f"{status['telegram']}\n", style="green" if status['telegram'] == "Active" else "red")

        content.append("Uptime: ", style="white")
        content.append(f"{status['uptime']:<15}", style="cyan")
        content.append("API Latency: ", style="white")
        content.append(f"{status['api_latency']:.1f}ms", style="green")
        content.append("      Rating: ", style="white")
        content.append(status['rating'], style="green" if status['rating'] == "EXCELLENT" or status['rating'] == "GOOD" else "yellow")

        return Panel(
            content,
            title="[bold bright_white]SYSTEM STATUS[/]",
            title_align="left",
            border_style="bright_blue",
            padding=(0, 1),
        )

    def _render_dashboard(self) -> Panel:
        """Render monitoring dashboard."""
        data = self.dashboard_data

        # Create two-column layout
        table = Table.grid(padding=(0, 2))
        table.add_column(style="white", width=40)
        table.add_column(style="white", width=40)

        # Header
        target_text = Text()
        target_text.append(f"Target: {data['target_name']} ({data['target_address']})", style="bright_cyan")

        status_text = Text()
        status_text.append("Status: ", style="white")
        status_text.append(data['status'], style="green" if data['status'] == "ACTIVE" else "red")

        table.add_row(target_text, status_text)
        table.add_row("")  # Spacer

        # Trading Statistics | System Health
        table.add_row(
            Text("Trading Statistics", style="bold cyan"),
            Text("System Health", style="bold cyan")
        )
        table.add_row("─" * 35, "─" * 35)

        # Row 1
        trades_text = Text()
        trades_text.append("Total Trades:        ", style="white")
        trades_text.append(f"{data['total_trades']}", style="bright_white")

        latency_text = Text()
        latency_text.append("API Latency:      ", style="white")
        latency_text.append(f"{data['api_latency']:.1f}ms  ", style="green")
        latency_text.append("GOOD", style="green")

        table.add_row(trades_text, latency_text)

        # Row 2
        open_text = Text()
        open_text.append("Open Positions:       ", style="white")
        open_text.append(f"{data['open_positions']}", style="yellow")

        failed_text = Text()
        failed_text.append("Failed Requests:      ", style="white")
        failed_text.append(f"{data['failed_requests']}   ", style="green" if data['failed_requests'] == 0 else "red")
        failed_text.append("GOOD", style="green" if data['failed_requests'] == 0 else "red")

        table.add_row(open_text, failed_text)

        # Row 3
        closed_text = Text()
        closed_text.append("Closed Positions:     ", style="white")
        closed_text.append(f"{data['closed_positions']}", style="blue")

        ws_text = Text()
        ws_text.append("WebSocket:    ", style="white")
        ws_text.append(data['websocket_status'], style="green" if data['websocket_status'] == "Connected" else "red")

        table.add_row(closed_text, ws_text)

        # Row 4
        winrate_text = Text()
        winrate_text.append("Win Rate:         ", style="white")
        winrate_text.append(f"{data['win_rate']:.1f}%", style="green" if data['win_rate'] >= 50 else "yellow")

        tg_text = Text()
        tg_text.append("Telegram:         ", style="white")
        tg_text.append(data['telegram_status'], style="green" if data['telegram_status'] == "Active" else "red")

        table.add_row(winrate_text, tg_text)

        # Row 5
        pnl_text = Text()
        pnl_text.append("Total PnL:      ", style="white")
        pnl_style = "green" if data['total_pnl'] > 0 else "red" if data['total_pnl'] < 0 else "white"
        pnl_text.append(f"${data['total_pnl']:+.2f}", style=pnl_style)

        db_text = Text()
        db_text.append("Database:         ", style="white")
        db_text.append(data['database_status'], style="green" if data['database_status'] == "Healthy" else "red")

        table.add_row(pnl_text, db_text)

        # Row 6
        best_text = Text()
        best_text.append("Best Trade:      ", style="white")
        best_text.append(f"${data['best_trade']:+.2f}", style="green")

        uptime_text = Text()
        uptime_text.append("Uptime:          ", style="white")
        uptime_text.append(data['uptime'], style="cyan")

        table.add_row(best_text, uptime_text)

        return Panel(
            table,
            title="[bold bright_white]MONITORING DASHBOARD[/]",
            title_align="left",
            border_style="bright_blue",
            padding=(0, 1),
        )

    def _render_recent_trades(self) -> Panel:
        """Render recent trades table."""
        table = Table(show_header=True, header_style="bold cyan", border_style="blue")
        table.add_column("Time", style="white", width=10)
        table.add_column("Action", style="white", width=6)
        table.add_column("Market", style="white", width=25)
        table.add_column("Price", style="white", width=6)
        table.add_column("Status", style="white", width=8)
        table.add_column("PnL", style="white", width=10)

        if not self.recent_trades:
            table.add_row("--:--:--", "---", "No trades yet", "---", "---", "$0.00")
        else:
            for trade in self.recent_trades:
                time_str = trade.get("time", "")
                action = trade.get("action", "")
                market = trade.get("market", "")
                price = trade.get("price", 0.0)
                status = trade.get("status", "")
                pnl = trade.get("pnl", 0.0)

                # Color coding
                action_style = "bright_green" if action == "BUY" else "bright_red"
                status_style = "yellow" if status == "OPEN" else "blue"
                pnl_style = "green" if pnl > 0 else "red" if pnl < 0 else "white"

                table.add_row(
                    time_str,
                    Text(action, style=action_style),
                    market[:25],
                    f"{price:.2f}",
                    Text(status, style=status_style),
                    Text(f"${pnl:+.2f}", style=pnl_style),
                )

        return Panel(
            table,
            title="[bold bright_white]RECENT TRADES (Last 5)[/]",
            title_align="left",
            border_style="bright_blue",
            padding=(0, 1),
        )

    def _render_event_stream(self) -> Panel:
        """Render live event stream."""
        content = Text()

        if not self.event_stream:
            content.append("Waiting for events...", style="dim white")
        else:
            for event in self.event_stream:
                timestamp = event.get("timestamp", "")
                event_type = event.get("type", "")
                message = event.get("message", "")
                details = event.get("details", [])

                # Event type color
                type_colors = {
                    "SYSTEM": "bright_blue",
                    "TRADE": "bright_yellow",
                    "SIMULATION": "bright_green",
                    "NOTIFY": "bright_cyan",
                    "SETTLEMENT": "bright_magenta",
                    "ERROR": "bright_red",
                    "WARNING": "yellow",
                }
                type_color = type_colors.get(event_type, "white")

                # Main event line
                content.append(f"[{timestamp}] ", style="cyan")
                content.append(f"{event_type:<12}", style=type_color)
                content.append(f"| {message}\n", style="white")

                # Details
                for detail in details:
                    content.append(f"           {detail}\n", style="dim white")

                content.append("\n")

        return Panel(
            content,
            title="[bold bright_white]LIVE EVENT STREAM (Last 10 events)[/]",
            title_align="left",
            border_style="bright_blue",
            padding=(0, 1),
        )

    def _render_footer(self) -> Panel:
        """Render footer with last update time."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer = Text()
        footer.append(f"Last Updated: {now}", style="cyan")
        footer.append(" | Press Ctrl+C to stop", style="dim white")

        return Panel(
            footer,
            border_style="bright_blue",
            padding=(0, 1),
        )

    def update_display(self) -> Layout:
        """Update all sections and return the layout."""
        self.layout["header"].update(self._render_header())
        self.layout["system"].update(self._render_system_status())
        self.layout["dashboard"].update(self._render_dashboard())
        self.layout["trades"].update(self._render_recent_trades())
        self.layout["events"].update(self._render_event_stream())
        self.layout["footer"].update(self._render_footer())

        return self.layout

    def update_system_status(self, **kwargs: Any) -> None:
        """Update system status data."""
        self.system_status.update(kwargs)

    def update_dashboard(self, **kwargs: Any) -> None:
        """Update dashboard data."""
        self.dashboard_data.update(kwargs)

    def add_trade(self, trade: dict[str, Any]) -> None:
        """Add a trade to recent trades."""
        self.recent_trades.append(trade)

    def add_event(self, event: dict[str, Any]) -> None:
        """Add an event to the event stream."""
        self.event_stream.append(event)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run the live dashboard with continuous updates."""
        with Live(
            self.update_display(),
            console=self.console,
            refresh_per_second=1 / self.config.refresh_interval,
            screen=True,
        ) as live:
            while not stop_event.is_set():
                live.update(self.update_display())
                await asyncio.sleep(self.config.refresh_interval)
