"""Telegram notification channel."""

from __future__ import annotations

from loguru import logger

from src.config.models import TelegramConfig

from .manager import NotificationChannel


class TelegramNotifier(NotificationChannel):
    """Send messages via Telegram Bot API."""

    name = "telegram"

    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self._bot = None

    async def _ensure_bot(self) -> bool:
        if self._bot is not None:
            return True
        if not self.config.bot_token or not self.config.chat_id:
            logger.warning("Telegram: missing bot_token or chat_id")
            return False
        try:
            from telegram import Bot

            self._bot = Bot(token=self.config.bot_token)
            return True
        except ImportError:
            logger.error(
                "python-telegram-bot is not installed. Run: pip install python-telegram-bot"
            )
            return False
        except Exception as exc:
            logger.error(f"Telegram bot init failed: {exc}")
            return False

    async def send(self, message: str) -> bool:
        if not self.config.enabled:
            return True

        if not await self._ensure_bot():
            return False

        try:
            # Truncate to Telegram's 4096-char limit
            if len(message) > 4000:
                message = message[:3997] + "..."

            await self._bot.send_message(
                chat_id=self.config.chat_id,
                text=message,
                parse_mode=None,
            )
            logger.debug("Telegram message sent")
            return True
        except Exception as exc:
            logger.error(f"Telegram send failed: {exc}")
            raise
