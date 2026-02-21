"""Structured logging setup using loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from src.config.models import LogFormat, LoggingConfig


def setup_logger(config: LoggingConfig) -> None:
    """Configure loguru with console + file + optional metrics sink."""
    # Remove default handler
    logger.remove()

    # ── Console (always human-readable) ──────────────────
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level=config.level,
        colorize=True,
    )

    # ── Application log file ─────────────────────────────
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    if config.format == LogFormat.JSON:
        logger.add(
            str(log_dir / "app.log"),
            level=config.level,
            rotation=config.rotation,
            retention=config.retention,
            serialize=True,
        )
    else:
        logger.add(
            str(log_dir / "app.log"),
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
                "{name}:{function}:{line} - {message}"
            ),
            level=config.level,
            rotation=config.rotation,
            retention=config.retention,
        )

    # ── Metrics log (filtered by extra.metrics flag) ─────
    if config.metrics_enabled:
        logger.add(
            str(log_dir / "metrics.log"),
            filter=lambda record: record["extra"].get("metrics", False),
            format="{message}",
            rotation="100 MB",
            retention="7 days",
            serialize=True,
        )

    logger.info(f"Logger initialised: level={config.level} format={config.format.value}")
