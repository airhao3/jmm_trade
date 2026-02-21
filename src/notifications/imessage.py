"""iMessage notification channel (macOS only)."""

from __future__ import annotations

import asyncio
import platform

from loguru import logger

from src.config.models import IMessageConfig

from .manager import NotificationChannel


class IMessageNotifier(NotificationChannel):
    """Send messages via macOS iMessage / Messages.app using AppleScript."""

    name = "imessage"

    def __init__(self, config: IMessageConfig) -> None:
        self.config = config

    async def send(self, message: str) -> bool:
        if not self.config.enabled:
            return True

        if platform.system() != "Darwin":
            logger.warning("iMessage is only supported on macOS")
            return False

        if not self.config.phone_number:
            logger.warning("iMessage: phone_number not configured")
            return False

        try:
            # Truncate very long messages
            if len(message) > 2000:
                message = message[:1997] + "..."

            escaped_msg = message.replace("\\", "\\\\").replace('"', '\\"')
            phone = self.config.phone_number

            script = (
                f'tell application "Messages"\n'
                f"  set targetService to 1st account whose service type = iMessage\n"
                f'  set targetBuddy to participant "{phone}" of targetService\n'
                f'  send "{escaped_msg}" to targetBuddy\n'
                f"end tell"
            )

            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                err = stderr.decode().strip()
                logger.error(f"iMessage send failed: {err}")
                raise RuntimeError(f"osascript error: {err}")

            logger.debug("iMessage sent")
            return True

        except Exception as exc:
            logger.error(f"iMessage send error: {exc}")
            raise
