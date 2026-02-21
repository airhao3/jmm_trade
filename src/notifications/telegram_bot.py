"""Telegram Bot command handler - interactive management via Telegram."""

from __future__ import annotations

import os

from loguru import logger

from src.config.models import TelegramConfig
from src.utils.target_manager import TargetManager


class TelegramBotHandler:
    """Handle Telegram bot commands and inline keyboard interactions."""

    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.target_manager = TargetManager()
        self._app = None
        self._running = False

    async def start(self) -> None:
        """Start the Telegram bot polling for commands."""
        if not self.config.enabled or not self.config.bot_token:
            logger.warning("Telegram bot commands disabled: missing config")
            return

        try:
            from telegram import BotCommand
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
            )
        except ImportError:
            logger.error("python-telegram-bot not installed")
            return

        self._app = Application.builder().token(self.config.bot_token).build()

        # Register command handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("targets", self._cmd_list_targets))
        self._app.add_handler(CommandHandler("add", self._cmd_add_target))
        self._app.add_handler(CommandHandler("remove", self._cmd_remove_target))
        self._app.add_handler(CommandHandler("enable", self._cmd_enable_target))
        self._app.add_handler(CommandHandler("disable", self._cmd_disable_target))
        self._app.add_handler(CommandHandler("trades", self._cmd_recent_trades))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))

        # Callback query handler for inline keyboards
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Set bot commands menu
        await self._app.bot.set_my_commands([
            BotCommand("start", "Show main menu"),
            BotCommand("status", "Bot status"),
            BotCommand("targets", "List tracked targets"),
            BotCommand("add", "Add target: /add ADDRESS NICKNAME"),
            BotCommand("remove", "Remove target: /remove NICKNAME"),
            BotCommand("trades", "Recent trades"),
            BotCommand("stats", "Trading statistics"),
            BotCommand("help", "Show help"),
        ])

        # Allow disabling bot commands via env (useful when VPS already runs one)
        if os.getenv("TELEGRAM_BOT_COMMANDS", "true").lower() == "false":
            logger.info("Telegram bot commands disabled via TELEGRAM_BOT_COMMANDS=false")
            self._app = None
            return

        self._running = True
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            drop_pending_updates=True,
            error_callback=self._polling_error_callback,
        )
        logger.info("Telegram bot command handler started")

    def _polling_error_callback(self, exc: Exception) -> None:
        """Suppress Conflict traceback spam from polling loop."""
        if "Conflict" in str(exc):
            if not getattr(self, "_conflict_warned", False):
                self._conflict_warned = True
                logger.warning(
                    "Telegram bot conflict: another instance is polling. "
                    "Stop the other instance or set TELEGRAM_BOT_COMMANDS=false"
                )
        else:
            logger.error(f"Telegram polling error: {exc}")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app and self._running:
            self._running = False
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot command handler stopped")

    def _authorized(self, update) -> bool:
        """Check if the user is authorized (matches configured chat_id)."""
        if not update.effective_chat:
            return False
        return str(update.effective_chat.id) == str(self.config.chat_id)

    # ── Commands ──────────────────────────────────────────

    async def _cmd_start(self, update, context) -> None:
        """Handle /start command - show main menu with buttons."""
        if not self._authorized(update):
            await update.message.reply_text("Unauthorized")
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton("Status", callback_data="cmd_status"),
                InlineKeyboardButton("Targets", callback_data="cmd_targets"),
            ],
            [
                InlineKeyboardButton("Trades", callback_data="cmd_trades"),
                InlineKeyboardButton("Stats", callback_data="cmd_stats"),
            ],
            [
                InlineKeyboardButton("+ Add Target", callback_data="cmd_add_help"),
                InlineKeyboardButton("Help", callback_data="cmd_help"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "Polymarket Copy Trader\n"
            "----------------------\n"
            "Select an option:",
            reply_markup=reply_markup,
        )

    async def _cmd_help(self, update, context) -> None:
        """Handle /help command."""
        if not self._authorized(update):
            return

        help_text = (
            "Commands:\n"
            "\n"
            "/start - Main menu\n"
            "/status - Bot status\n"
            "/targets - List tracked targets\n"
            "/add ADDRESS NICKNAME - Add target\n"
            "/remove NICKNAME - Remove target\n"
            "/enable NICKNAME - Enable target\n"
            "/disable NICKNAME - Disable target\n"
            "/trades - Recent trades\n"
            "/stats - Trading statistics\n"
            "/help - This message\n"
            "\n"
            "Example:\n"
            "/add 0x1234...5678 WhaleTrader"
        )

        msg = update.message or update.callback_query.message
        await msg.reply_text(help_text)

    async def _cmd_status(self, update, context) -> None:
        """Handle /status command."""
        if not self._authorized(update):
            return

        targets = self.target_manager.list_targets(enabled_only=True)
        all_targets = self.target_manager.list_targets(enabled_only=False)

        status_text = (
            "BOT STATUS\n"
            "----------\n"
            f"Active targets: {len(targets)}/{len(all_targets)}\n"
            f"Mode: Simulation (READ_ONLY)\n"
        )

        for t in targets:
            addr = t["address"]
            status_text += f"\n  [{t['nickname']}] {addr[:8]}...{addr[-4:]}"

        msg = update.message or update.callback_query.message
        await msg.reply_text(status_text)

    async def _cmd_list_targets(self, update, context) -> None:
        """Handle /targets command - list all targets with action buttons."""
        if not self._authorized(update):
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        targets = self.target_manager.list_targets(enabled_only=False)

        if not targets:
            msg = update.message or update.callback_query.message
            await msg.reply_text("No targets configured.\nUse /add ADDRESS NICKNAME")
            return

        text = f"TRACKED TARGETS ({len(targets)})\n"
        text += "-" * 30 + "\n"

        keyboard = []
        for t in targets:
            status = "ON" if t.get("enabled", True) else "OFF"
            addr = t["address"]
            text += f"\n[{status}] {t['nickname']}\n  {addr[:10]}...{addr[-6:]}\n"

            if t.get("notes"):
                text += f"  {t['notes']}\n"

            # Action buttons per target
            nickname = t["nickname"]
            if t.get("enabled", True):
                keyboard.append([
                    InlineKeyboardButton(
                        f"Disable {nickname}", callback_data=f"disable_{nickname}"
                    ),
                    InlineKeyboardButton(
                        f"Remove {nickname}", callback_data=f"remove_{nickname}"
                    ),
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(
                        f"Enable {nickname}", callback_data=f"enable_{nickname}"
                    ),
                    InlineKeyboardButton(
                        f"Remove {nickname}", callback_data=f"remove_{nickname}"
                    ),
                ])

        keyboard.append([
            InlineKeyboardButton("+ Add New Target", callback_data="cmd_add_help"),
            InlineKeyboardButton("Refresh", callback_data="cmd_targets"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = update.message or update.callback_query.message
        await msg.reply_text(text, reply_markup=reply_markup)

    async def _cmd_add_target(self, update, context) -> None:
        """Handle /add ADDRESS NICKNAME command."""
        if not self._authorized(update):
            return

        args = context.args if context.args else []

        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /add ADDRESS NICKNAME\n"
                "\n"
                "Example:\n"
                "/add 0x1234567890abcdef1234567890abcdef12345678 WhaleTrader"
            )
            return

        address = args[0]
        nickname = args[1]
        notes = " ".join(args[2:]) if len(args) > 2 else ""

        try:
            success = self.target_manager.add_target(
                address=address,
                nickname=nickname,
                enabled=True,
                notes=notes,
            )
            if success:
                await update.message.reply_text(
                    f"Added target:\n"
                    f"  Name: {nickname}\n"
                    f"  Address: {address}\n"
                    f"  Status: ENABLED\n"
                    f"\nRestart bot to start tracking."
                )
            else:
                await update.message.reply_text(f"Target already exists: {address}")

        except ValueError as e:
            await update.message.reply_text(f"Invalid address: {e}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def _cmd_remove_target(self, update, context) -> None:
        """Handle /remove NICKNAME command."""
        if not self._authorized(update):
            return

        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /remove NICKNAME")
            return

        identifier = args[0]
        target = self.target_manager.get_target(identifier)

        if not target:
            await update.message.reply_text(f"Target not found: {identifier}")
            return

        success = self.target_manager.remove_target(identifier)
        if success:
            await update.message.reply_text(
                f"Removed: {target['nickname']}\n"
                f"Address: {target['address']}"
            )
        else:
            await update.message.reply_text("Failed to remove target")

    async def _cmd_enable_target(self, update, context) -> None:
        """Handle /enable NICKNAME command."""
        if not self._authorized(update):
            return

        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /enable NICKNAME")
            return

        success = self.target_manager.enable_target(args[0])
        if success:
            await update.message.reply_text(f"Enabled: {args[0]}")
        else:
            await update.message.reply_text(f"Not found: {args[0]}")

    async def _cmd_disable_target(self, update, context) -> None:
        """Handle /disable NICKNAME command."""
        if not self._authorized(update):
            return

        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /disable NICKNAME")
            return

        success = self.target_manager.disable_target(args[0])
        if success:
            await update.message.reply_text(f"Disabled: {args[0]}")
        else:
            await update.message.reply_text(f"Not found: {args[0]}")

    async def _cmd_recent_trades(self, update, context) -> None:
        """Handle /trades command - show recent trades."""
        if not self._authorized(update):
            return

        try:
            from src.config.models import DatabaseConfig
            from src.data.database import Database

            db = Database(DatabaseConfig(path="data/trades.db"))
            await db.connect()
            trades = await db.get_all_trades()
            await db.close()

            if not trades:
                msg = update.message or update.callback_query.message
                await msg.reply_text("No trades recorded yet.")
                return

            # Show last 10 trades
            recent = trades[-10:]
            text = f"RECENT TRADES ({len(trades)} total)\n"
            text += "-" * 30 + "\n"

            for t in reversed(recent):
                side = t.get("target_side", "?")
                name = t.get("market_name", "?")
                if len(name) > 35:
                    name = name[:32] + "..."
                price = t.get("target_price", 0)
                delay = t.get("sim_delay", 0)
                status = t.get("status", "?")
                slip = t.get("slippage_pct", 0)

                text += f"\n{side} {name}\n"
                text += f"  Price: {price} | Delay: {delay}s\n"
                text += f"  Status: {status} | Slip: {slip:.1f}%\n"

            msg = update.message or update.callback_query.message
            await msg.reply_text(text)

        except Exception as e:
            msg = update.message or update.callback_query.message
            await msg.reply_text(f"Error loading trades: {e}")

    async def _cmd_stats(self, update, context) -> None:
        """Handle /stats command - show trading statistics."""
        if not self._authorized(update):
            return

        try:
            from src.config.models import DatabaseConfig
            from src.data.database import Database

            db = Database(DatabaseConfig(path="data/trades.db"))
            await db.connect()
            trades = await db.get_all_trades()
            await db.close()

            if not trades:
                msg = update.message or update.callback_query.message
                await msg.reply_text("No trades recorded yet.")
                return

            total = len(trades)
            settled = sum(1 for t in trades if t.get("status") == "SETTLED")
            failed = sum(1 for t in trades if t.get("status") == "FAILED")
            open_trades = sum(1 for t in trades if t.get("status") == "OPEN")

            total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)
            avg_slip = sum(t.get("slippage_pct", 0) or 0 for t in trades) / total

            text = (
                "STATISTICS\n"
                "----------\n"
                f"Total trades: {total}\n"
                f"Open: {open_trades}\n"
                f"Settled: {settled}\n"
                f"Failed: {failed}\n"
                f"Total PnL: ${total_pnl:+.2f}\n"
                f"Avg slippage: {avg_slip:.1f}%\n"
            )

            msg = update.message or update.callback_query.message
            await msg.reply_text(text)

        except Exception as e:
            msg = update.message or update.callback_query.message
            await msg.reply_text(f"Error: {e}")

    # ── Callback Query Handler ────────────────────────────

    async def _handle_callback(self, update, context) -> None:
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        if not self._authorized(update):
            return

        data = query.data

        if data == "cmd_status":
            await self._cmd_status(update, context)
        elif data == "cmd_targets":
            await self._cmd_list_targets(update, context)
        elif data == "cmd_trades":
            await self._cmd_recent_trades(update, context)
        elif data == "cmd_stats":
            await self._cmd_stats(update, context)
        elif data == "cmd_help":
            await self._cmd_help(update, context)
        elif data == "cmd_add_help":
            await query.message.reply_text(
                "To add a target, send:\n"
                "/add ADDRESS NICKNAME\n"
                "\n"
                "Example:\n"
                "/add 0x1234...5678 WhaleTrader"
            )
        elif data.startswith("enable_"):
            nickname = data[7:]
            success = self.target_manager.enable_target(nickname)
            if success:
                await query.message.reply_text(f"Enabled: {nickname}")
            else:
                await query.message.reply_text(f"Not found: {nickname}")
        elif data.startswith("disable_"):
            nickname = data[8:]
            success = self.target_manager.disable_target(nickname)
            if success:
                await query.message.reply_text(f"Disabled: {nickname}")
            else:
                await query.message.reply_text(f"Not found: {nickname}")
        elif data.startswith("remove_"):
            nickname = data[7:]
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = [[
                InlineKeyboardButton(
                    "Yes, remove", callback_data=f"confirm_remove_{nickname}"
                ),
                InlineKeyboardButton("Cancel", callback_data="cmd_targets"),
            ]]
            await query.message.reply_text(
                f"Remove {nickname}?",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif data.startswith("confirm_remove_"):
            nickname = data[15:]
            success = self.target_manager.remove_target(nickname)
            if success:
                await query.message.reply_text(f"Removed: {nickname}")
            else:
                await query.message.reply_text(f"Not found: {nickname}")
