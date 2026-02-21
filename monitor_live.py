"""Live monitoring script to watch for trade events and notifications."""

import asyncio
import time
from datetime import datetime
from pathlib import Path


async def monitor_logs():
    """Monitor bot logs in real-time and highlight important events."""
    log_file = Path("logs/bot.log")
    
    print("=" * 80)
    print("  🤖 Polymarket Copy Trader - Live Monitor")
    print("=" * 80)
    print()
    print(f"📊 Monitoring: PBot1 (0x88f4...d4db)")
    print(f"📱 Telegram: @jmm_trader_bot")
    print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("=" * 80)
    print("  Waiting for trade events...")
    print("=" * 80)
    print()
    
    # Track last position in file
    last_pos = log_file.stat().st_size if log_file.exists() else 0
    
    # Keywords to highlight
    trade_keywords = [
        "NEW_TRADE",
        "New trade detected",
        "SIM_EXECUTED",
        "SIM_FAILED",
        "SETTLEMENT",
        "Market settled",
        "Telegram message sent",
        "WebSocket message",
    ]
    
    try:
        while True:
            if log_file.exists():
                current_size = log_file.stat().st_size
                
                if current_size > last_pos:
                    with open(log_file, 'r') as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        last_pos = current_size
                        
                        for line in new_lines:
                            # Check if line contains important keywords
                            is_important = any(keyword in line for keyword in trade_keywords)
                            
                            if is_important:
                                # Highlight important lines
                                print(f"🔔 {line.strip()}")
                                
                                # Extra notification for trade events
                                if "NEW_TRADE" in line or "New trade detected" in line:
                                    print("   " + "=" * 76)
                                    print("   ⚡ NEW TRADE DETECTED - Check Telegram for notification!")
                                    print("   " + "=" * 76)
                                
                                elif "Telegram message sent" in line:
                                    print("   " + "=" * 76)
                                    print("   📱 TELEGRAM NOTIFICATION SENT!")
                                    print("   " + "=" * 76)
                                
                                elif "SETTLEMENT" in line or "Market settled" in line:
                                    print("   " + "=" * 76)
                                    print("   💰 MARKET SETTLED - Check Telegram for PnL!")
                                    print("   " + "=" * 76)
                            
                            # Also show WebSocket messages (less prominent)
                            elif "WebSocket message" in line:
                                print(f"   {line.strip()}")
            
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        print()
        print("=" * 80)
        print("  Monitoring stopped")
        print("=" * 80)


if __name__ == "__main__":
    print()
    print("Press Ctrl+C to stop monitoring")
    print()
    asyncio.run(monitor_logs())
