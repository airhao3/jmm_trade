"""Test script for Telegram bot functionality."""

import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_telegram_bot():
    """Test Telegram bot connection and message sending."""
    print("=" * 60)
    print("  Telegram Bot Test")
    print("=" * 60)
    print()
    
    # Check environment variables
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    print("1. Checking environment variables...")
    if not bot_token or bot_token == "your_bot_token_here":
        print("   ✗ TELEGRAM_BOT_TOKEN not set or using default value")
        print("   Please set it in .env file")
        return False
    else:
        print(f"   ✓ TELEGRAM_BOT_TOKEN: {bot_token[:10]}...{bot_token[-4:]}")
    
    if not chat_id or chat_id == "your_chat_id_here":
        print("   ✗ TELEGRAM_CHAT_ID not set or using default value")
        print("   Please set it in .env file")
        return False
    else:
        print(f"   ✓ TELEGRAM_CHAT_ID: {chat_id}")
    
    print()
    
    # Test bot initialization
    print("2. Testing bot initialization...")
    try:
        from telegram import Bot
        bot = Bot(token=bot_token)
        print("   ✓ Bot object created successfully")
    except ImportError:
        print("   ✗ python-telegram-bot not installed")
        print("   Run: pip install python-telegram-bot")
        return False
    except Exception as e:
        print(f"   ✗ Bot initialization failed: {e}")
        return False
    
    print()
    
    # Test getting bot info
    print("3. Testing bot connection (getMe)...")
    try:
        me = await bot.get_me()
        print(f"   ✓ Bot connected successfully")
        print(f"   Bot ID: {me.id}")
        print(f"   Bot Username: @{me.username}")
        print(f"   Bot Name: {me.first_name}")
    except Exception as e:
        print(f"   ✗ Failed to get bot info: {e}")
        return False
    
    print()
    
    # Test sending a message
    print("4. Testing message sending...")
    try:
        test_message = "🤖 Telegram Bot Test\n\nThis is a test message from Polymarket Copy Trader.\n\nIf you see this, the bot is working correctly!"
        message = await bot.send_message(
            chat_id=chat_id,
            text=test_message,
        )
        print(f"   ✓ Message sent successfully")
        print(f"   Message ID: {message.message_id}")
        print(f"   Chat ID: {message.chat.id}")
        print(f"   Chat Type: {message.chat.type}")
        if message.chat.username:
            print(f"   Chat Username: @{message.chat.username}")
        if message.chat.first_name:
            print(f"   Chat Name: {message.chat.first_name}")
    except Exception as e:
        print(f"   ✗ Failed to send message: {e}")
        print(f"   Error type: {type(e).__name__}")
        return False
    
    print()
    print("=" * 60)
    print("  ✓ All tests passed!")
    print("=" * 60)
    return True


async def test_notification_system():
    """Test the full notification system."""
    print("\n" + "=" * 60)
    print("  Testing Full Notification System")
    print("=" * 60)
    print()
    
    from src.config.loader import load_config
    from src.notifications.telegram import TelegramNotifier
    
    # Load config
    config = load_config()
    
    print("1. Checking notification configuration...")
    print(f"   Notifications enabled: {config.notifications.enabled}")
    print(f"   Telegram enabled: {config.notifications.telegram.enabled}")
    print(f"   Bot token set: {bool(config.notifications.telegram.bot_token)}")
    print(f"   Chat ID set: {bool(config.notifications.telegram.chat_id)}")
    
    if not config.notifications.telegram.enabled:
        print("\n   ⚠ Telegram is disabled in config.yaml")
        print("   Set notifications.telegram.enabled to true")
        return False
    
    print()
    
    # Test TelegramNotifier
    print("2. Testing TelegramNotifier class...")
    notifier = TelegramNotifier(config.notifications.telegram)
    
    test_message = "📊 Test Notification\n\nNew trade detected:\nAction: BUY\nMarket: Bitcoin Up or Down\nPrice: 0.51\nAmount: $100.00"
    
    try:
        success = await notifier.send(test_message)
        if success:
            print("   ✓ Notification sent successfully via TelegramNotifier")
        else:
            print("   ✗ Notification failed")
            return False
    except Exception as e:
        print(f"   ✗ Exception during send: {e}")
        return False
    
    print()
    print("=" * 60)
    print("  ✓ Full notification system test passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    print("\nStarting Telegram bot tests...\n")
    
    # Run basic bot test
    success1 = asyncio.run(test_telegram_bot())
    
    if success1:
        # Run full system test
        success2 = asyncio.run(test_notification_system())
        
        if success2:
            print("\n✅ All tests passed! Telegram bot is working correctly.\n")
        else:
            print("\n⚠ Basic bot works but notification system has issues.\n")
    else:
        print("\n❌ Basic bot test failed. Please fix the issues above.\n")
