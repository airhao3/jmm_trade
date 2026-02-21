"""CSV export utilities."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from src.config.models import ExportConfig


async def export_trades_to_csv(
    trades: List[Dict[str, Any]],
    config: ExportConfig,
    filename: str | None = None,
) -> str:
    """Write a list of trade dicts to a CSV file.

    Returns the absolute path of the written file.
    """
    if not trades:
        logger.warning("No trades to export")
        return ""

    out_dir = Path(config.csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"sim_trades_{ts}.csv"

    filepath = out_dir / filename
    fieldnames = list(trades[0].keys())

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=fieldnames, extrasaction="ignore"
        )
        if config.include_headers:
            writer.writeheader()
        writer.writerows(trades)

    logger.info(f"Exported {len(trades)} trades -> {filepath.resolve()}")
    return str(filepath.resolve())


async def export_pnl_summary_to_csv(
    summary: List[Dict[str, Any]],
    config: ExportConfig,
) -> str:
    """Export PnL summary report."""
    if not summary:
        return ""

    out_dir = Path(config.csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filepath = out_dir / f"pnl_summary_{ts}.csv"
    fieldnames = list(summary[0].keys())

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary)

    logger.info(f"Exported PnL summary -> {filepath.resolve()}")
    return str(filepath.resolve())
