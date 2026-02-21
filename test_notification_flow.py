"""Test the full notification flow with simulated trade event."""

import asyncio
from datetime import datetime

from src.config.loader import load_config
from src.notifications.manager import NotificationManager, EventType


async def test_trade_notification():
    """Test sending a trade notification through the notification system."""
    print("=" * 60)
    print("  Testing Trade Notification Flow")
    print("=" * 60)
    print()
    
    # Load config
    config = load_config()
    
    # Create notification manager
    print("1. Initializing notification manager...")
    manager = NotificationManager(config.notifications, db=None)
    
    # Register Telegram channel
    if config.notifications.telegram.enabled:
        from src.notifications.telegram import TelegramNotifier
        telegram = TelegramNotifier(config.notifications.telegram)
        manager.register_channel(telegram)
        print(f"   ✓ Telegram channel registered")
    
    print(f"   Channels: {manager._channels}")
    print()
    
    # Simulate a new trade event
    print("2. Simulating NEW_TRADE event...")
    trade_data = {
        "target": "PBot1",
        "action": "BUY",
        "market": "Bitcoin Up or Down - February 22, 3:30AM ET",
        "price": 0.52,
        "amount": 100.0,
        "timestamp": datetime.now().isoformat(),
    }
    
    await manager.notify(
        EventType.NEW_TRADE,
        f"🔔 New Trade Detected\n\n"
        f"Target: {trade_data['target']}\n"
        f"Action: {trade_data['action']}\n"
        f"Market: {trade_data['market']}\n"
        f"Price: {trade_data['price']}\n"
        f"Amount: ${trade_data['amount']:.2f}\n"
        f"Time: {trade_data['timestamp']}"
    )
    print("   ✓ Trade notification queued")
    print()
    
    # Wait for aggregation interval and send
    print("3. Waiting for notification to be sent...")
    print("   (Notifications are aggregated and sent every 30 seconds)")
    
    # Manually trigger send for testing
    await manager._flush()
    print("   ✓ Notification batch sent")
    print()
    
    # Simulate a settlement event
    print("4. Simulating SETTLEMENT event...")
    settlement_data = {
        "trade_id": 25,
        "market": "Bitcoin Up or Down - February 22, 3:30AM ET",
        "result": "WON",
        "pnl": 5.80,
    }
    
    await manager.notify(
        EventType.SETTLEMENT,
        f"💰 Market Settled\n\n"
        f"Trade #{settlement_data['trade_id']}\n"
        f"Market: {settlement_data['market']}\n"
        f"Result: {settlement_data['result']}\n"
        f"PnL: ${settlement_data['pnl']:+.2f}"
    )
    print("   ✓ Settlement notification queued")
    print()
    
    # Send settlement notification
    print("5. Sending settlement notification...")
    await manager._flush()
    print("   ✓ Settlement notification sent")
    print()
    
    print("=" * 60)
    print("  ✓ Notification flow test completed!")
    print("=" * 60)
    print()
    print("Check your Telegram to see if you received:")
    print("  1. New trade notification")
    print("  2. Settlement notification")


if __name__ == "__main__":
    asyncio.run(test_trade_notification())
