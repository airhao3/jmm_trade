"""Application wrapper with dashboard integration.

This module provides a wrapper around the Application class that integrates
the live dashboard when console mode is set to 'live'.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from src.config.models import AppConfig
from src.core.app import Application
from src.utils.dashboard import LiveDashboard
from src.utils.dashboard_integration import DashboardIntegration
from src.utils.logger import set_dashboard

if TYPE_CHECKING:
    pass


async def run_with_dashboard(config: AppConfig) -> None:
    """Run the application with live dashboard if enabled."""

    # Check if live dashboard mode is enabled
    if config.logging.console.enabled and config.logging.console.mode == "live":
        # Create dashboard
        dashboard = LiveDashboard(config.logging.console)
        dashboard_integration = DashboardIntegration(dashboard)

        # Set global dashboard for logger
        set_dashboard(dashboard)

        # Initialize dashboard with startup info
        dashboard_integration.log_system_event("Bot initialization started")
        dashboard_integration.update_system_status(
            mode="WebSocket" if config.monitoring.mode.value == "websocket" else "Poll",
            investment=config.simulation.investment_per_trade,
            targets=len(config.get_active_targets()),
            database="Connecting...",
            websocket="Connecting...",
            telegram="Initializing...",
        )

        # Create stop event for dashboard
        stop_event = asyncio.Event()

        # Create application
        app = Application(config)

        # Patch application to integrate with dashboard
        _patch_application_with_dashboard(app, dashboard_integration)

        # Run dashboard and application concurrently
        try:
            await asyncio.gather(
                dashboard.run(stop_event),
                app.run(),
            )
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
            stop_event.set()
        except Exception as e:
            logger.error(f"Application error: {e}")
            dashboard_integration.log_error(f"Application error: {e}")
            stop_event.set()
            raise
    else:
        # Run without dashboard (traditional mode)
        app = Application(config)
        await app.run()


def _patch_application_with_dashboard(app: Application, integration: DashboardIntegration) -> None:
    """Patch application methods to integrate with dashboard.

    This is a temporary solution until we refactor the Application class
    to natively support dashboard integration.
    """
    # Store original methods
    original_run = app.run

    async def patched_run():
        """Patched run method with dashboard integration."""
        # Update dashboard during startup
        integration.log_system_event("Database connecting...")

        # Call original run
        await original_run()

    # Replace method
    app.run = patched_run

    # Store integration for later use
    app._dashboard_integration = integration  # type: ignore
