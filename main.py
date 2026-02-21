#!/usr/bin/env python3
"""Polymarket Copy Trading Simulator – entry point.

Usage:
    python main.py run                    # Start polling monitor
    python main.py run --mode ws          # WebSocket mode
    python main.py run --dry-run          # No notifications
    python main.py export                 # Export trades to CSV
    python main.py stats                  # Show statistics
    python main.py check-config           # Validate config only
"""

import os
import sys

# ── Safety: enforce read-only before anything else ───────
os.environ.setdefault("FORCE_READ_ONLY", "true")
READ_ONLY_MODE = True

# Ensure src package is importable when run from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.cli.commands import cli

if __name__ == "__main__":
    cli(obj={})
