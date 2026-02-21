"""Simple test to send Telegram notification directly."""

import asyncio
from src.config.loader import load_config
from src.notifications.telegram import TelegramNotifier


async def test_simple_notification():
    """Test sending a simple notification."""
    print("=" * 60)
    print("  Simple Telegram Notification Test")
    print("=" * 60)
    print()
    
    # Load config
    config = load_config()
    
    # Create Telegram notifier
    print("1. Creating Telegram notifier...")
    notifier = TelegramNotifier(config.notifications.telegram)
    print(f"   ✓ Notifier created")
    print(f"   Enabled: {config.notifications.telegram.enabled}")
    print()
    
    # Test 1: New trade notification
    print("2. Sending new trade notification...")
    trade_message = """🔔 New Trade Detected

Target: PBot1
Action: BUY
Market: Bitcoin Up or Down - February 22, 3:40AM ET
Price: 0.52
Amount: $100.00
Time: 2026-02-22 03:40:00"""
    
    success = await notifier.send(trade_message)
    if success:
        print("   ✓ Trade notification sent successfully")
    else:
        print("   ✗ Trade notification failed")
    print()
    
    # Wait a bit
    await asyncio.sleep(2)
    
    # Test 2: Settlement notification
    print("3. Sending settlement notification...")
    settlement_message = """💰 Market Settled

Trade #25
Market: Bitcoin Up or Down - February 22, 3:40AM ET
Result: WON
Entry: 0.52
Exit: 1.00
PnL: +$5.80
Duration: 5m 30s"""
    
    success = await notifier.send(settlement_message)
    if success:
        print("   ✓ Settlement notification sent successfully")
    else:
        print("   ✗ Settlement notification failed")
    print()
    
    # Wait a bit
    await asyncio.sleep(2)
    
    # Test 3: System notification
    print("4. Sending system notification...")
    system_message = """⚙️ System Update

Bot is now monitoring PBot1 account
WebSocket: Connected
Telegram: Active
Status: All systems operational"""
    
    success = await notifier.send(system_message)
    if success:
        print("   ✓ System notification sent successfully")
    else:
        print("   ✗ System notification failed")
    print()
    
    print("=" * 60)
    print("  ✓ All notifications sent!")
    print("=" * 60)
    print()
    print("Check your Telegram (@jmm_trader_bot) to verify:")
    print("  1. New trade notification")
    print("  2. Settlement notification")
    print("  3. System notification")


if __name__ == "__main__":
    asyncio.run(test_simple_notification())
