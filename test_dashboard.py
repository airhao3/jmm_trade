"""Simple test script for the live dashboard."""

import asyncio
from datetime import datetime

from src.config.loader import load_config
from src.utils.dashboard import LiveDashboard
from src.utils.dashboard_integration import DashboardIntegration


async def test_dashboard():
    """Test the live dashboard with simulated events."""
    # Load config
    config = load_config()
    
    # Create dashboard
    dashboard = LiveDashboard(config.logging.console)
    integration = DashboardIntegration(dashboard)
    
    # Initialize with test data
    integration.update_system_status(
        mode="WebSocket",
        investment=100.0,
        targets=1,
        database="Connected",
        websocket="Connected",
        telegram="Active",
        api_latency=62.8,
        rating="GOOD",
    )
    
    integration.update_dashboard_stats(
        total_trades=12,
        open_positions=3,
        closed_positions=9,
        win_rate=58.3,
        total_pnl=45.20,
        best_trade=8.50,
        api_latency=62.8,
        failed_requests=0,
        websocket_status="Connected",
        telegram_status="Active",
        database_status="Healthy",
    )
    
    # Add some test trades
    integration.add_trade_event("BUY", "Bitcoin Up or Down", 0.51, "OPEN", 0.0)
    integration.add_trade_event("SELL", "ETH Higher/Lower", 0.68, "CLOSED", 5.20)
    integration.add_trade_event("BUY", "BTC Above/Below", 0.45, "OPEN", 0.0)
    
    # Add some test events
    integration.log_system_event("Bot initialization started")
    integration.log_trade_detected("PBot1", "BUY", "Bitcoin Up or Down", 0.51, 100.0)
    integration.log_simulation_executed(1, 0.51, 1.50, "OPEN")
    integration.log_notification_sent("Telegram", "Jack", "Delivered")
    
    # Create stop event
    stop_event = asyncio.Event()
    
    # Run dashboard for 30 seconds
    async def auto_stop():
        await asyncio.sleep(30)
        stop_event.set()
    
    # Simulate periodic updates
    async def simulate_updates():
        await asyncio.sleep(5)
        for i in range(5):
            integration.update_dashboard_stats(
                total_trades=12 + i,
                api_latency=60.0 + i * 2,
            )
            integration.log_system_event(f"Periodic update {i+1}")
            await asyncio.sleep(5)
    
    try:
        await asyncio.gather(
            dashboard.run(stop_event),
            auto_stop(),
            simulate_updates(),
        )
    except KeyboardInterrupt:
        print("\nDashboard test stopped by user")
        stop_event.set()


if __name__ == "__main__":
    print("Starting dashboard test...")
    print("The dashboard will run for 30 seconds with simulated updates.")
    print("Press Ctrl+C to stop early.\n")
    asyncio.run(test_dashboard())
    print("\nDashboard test completed!")
