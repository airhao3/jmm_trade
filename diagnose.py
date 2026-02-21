"""Diagnostic script to check for common issues."""

import sys
from pathlib import Path

print("=" * 60)
print("  Polymarket Bot - Diagnostic Script")
print("=" * 60)
print()

# Check Python version
print(f"✓ Python version: {sys.version}")
print()

# Check if running in TTY
print(f"✓ TTY available: {sys.stdout.isatty()}")
print()

# Check critical imports
print("Checking critical imports...")
try:
    from src.config.loader import load_config
    print("  ✓ config.loader")
except Exception as e:
    print(f"  ✗ config.loader: {e}")
    sys.exit(1)

try:
    from src.utils.logger import setup_logger
    print("  ✓ utils.logger")
except Exception as e:
    print(f"  ✗ utils.logger: {e}")
    sys.exit(1)

try:
    from src.core.app import Application
    print("  ✓ core.app")
except Exception as e:
    print(f"  ✗ core.app: {e}")
    sys.exit(1)

# Try to load config
print()
print("Loading configuration...")
try:
    config = load_config()
    print(f"  ✓ Config loaded successfully")
    print(f"  - Mode: {config.monitoring.mode.value}")
    print(f"  - Console mode: {config.logging.console.mode}")
    print(f"  - Targets: {len(config.get_active_targets())}")
except Exception as e:
    print(f"  ✗ Config loading failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Try to setup logger
print()
print("Setting up logger...")
try:
    setup_logger(config.logging)
    print("  ✓ Logger setup successful")
except Exception as e:
    print(f"  ✗ Logger setup failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Check files and directories
print()
print("Checking files and directories...")
required_files = [
    ".env",
    "config/config.yaml",
    "data",
    "logs",
]

for file_path in required_files:
    path = Path(file_path)
    if path.exists():
        print(f"  ✓ {file_path}")
    else:
        print(f"  ✗ {file_path} (missing)")

print()
print("=" * 60)
print("  Diagnostic complete - no critical errors found")
print("=" * 60)
