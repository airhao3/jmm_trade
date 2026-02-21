"""Structured logging setup using loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from src.config.models import LoggingConfig

# Global dashboard instance (set by application)
_dashboard = None


def set_dashboard(dashboard) -> None:
    """Set the global dashboard instance for live mode."""
    global _dashboard
    _dashboard = dashboard


def get_dashboard():
    """Get the global dashboard instance."""
    return _dashboard


def setup_logger(config: LoggingConfig) -> None:
    """Configure loguru with console + file outputs based on mode."""
    # Remove default handler
    logger.remove()

    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── Console output (only if not in live mode) ────────
    if config.console.enabled and config.console.mode != "live":
        # Traditional scrolling console output
        logger.add(
            sys.stdout,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            level=config.level,
            colorize=config.console.colors.enabled,
        )
    # In live mode, console output is suppressed (dashboard handles it)

    # ── Main log file ─────────────────────────────────────
    if config.files.main.enabled:
        logger.add(
            str(log_dir / Path(config.files.main.path).name),
            level=config.files.main.level,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
                "{name}:{function}:{line} - {message}"
            ),
            rotation=config.files.main.rotation,
            retention=config.files.main.retention,
            filter=lambda record: not record["extra"].get("metrics", False),
        )

    # ── Trades log file ───────────────────────────────────
    if config.files.trades.enabled:
        logger.add(
            str(log_dir / Path(config.files.trades.path).name),
            level=config.files.trades.level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
            rotation=config.files.trades.rotation,
            retention=config.files.trades.retention,
            filter=lambda record: record["extra"].get("trade", False),
        )

    # ── Metrics log file ──────────────────────────────────
    if config.files.metrics.enabled:
        logger.add(
            str(log_dir / Path(config.files.metrics.path).name),
            level=config.files.metrics.level,
            format="{message}",
            rotation=config.files.metrics.rotation,
            retention=config.files.metrics.retention,
            serialize=True,
            filter=lambda record: record["extra"].get("metrics", False),
        )

    # ── Errors log file ───────────────────────────────────
    if config.files.errors.enabled:
        logger.add(
            str(log_dir / Path(config.files.errors.path).name),
            level=config.files.errors.level,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
                "{name}:{function}:{line} - {message}\n{exception}"
            ),
            rotation=config.files.errors.rotation,
            retention=config.files.errors.retention,
        )

    mode_str = f"mode={config.console.mode}" if config.console.enabled else "console=disabled"
    logger.info(f"Logger initialised: level={config.level} {mode_str}")
