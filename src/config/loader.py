"""Configuration loader: YAML + env vars -> validated AppConfig."""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from .models import AppConfig, TargetAccount


def load_config(config_path: str = "config/config.yaml") -> AppConfig:
    """Load and validate configuration from YAML file.

    Environment variables from .env are loaded first so that
    ``${VAR}`` placeholders in the YAML can be resolved by Pydantic
    model validators.
    """
    # Load .env first
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path.resolve()}")

    # Read YAML
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file.resolve()}")

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Config file is empty: {config_file}")

    # Override log level from env if present
    env_log_level = os.getenv("LOG_LEVEL")
    if env_log_level:
        raw.setdefault("logging", {})["level"] = env_log_level

    # Load targets from external file if it exists
    targets_file = Path("config/targets.json")
    if targets_file.exists():
        try:
            with open(targets_file, encoding="utf-8") as f:
                targets_data = json.load(f)

            # Convert JSON targets to TargetAccount objects
            external_targets = []
            for target in targets_data.get("targets", []):
                if target.get("enabled", True):  # Only load enabled targets
                    external_targets.append(
                        TargetAccount(
                            address=target["address"],
                            nickname=target["nickname"],
                            active=target.get("enabled", True),
                        )
                    )

            if external_targets:
                # Replace config.yaml targets with external targets
                raw.setdefault("targets", [])
                raw["targets"] = [
                    {
                        "address": t.address,
                        "nickname": t.nickname,
                        "active": t.active,
                    }
                    for t in external_targets
                ]
                logger.info(f"Loaded {len(external_targets)} targets from {targets_file}")
        except Exception as e:
            logger.warning(f"Failed to load targets from {targets_file}: {e}")

    # Build validated config
    config = AppConfig(**raw)

    # Safety check
    if not config.system.read_only_mode:
        force = os.getenv("FORCE_READ_ONLY", "true").lower()
        if force == "true":
            config.system.read_only_mode = True
            logger.warning("FORCE_READ_ONLY env override activated -> read_only_mode=True")

    logger.info(
        f"Config loaded: {len(config.get_active_targets())} active targets, "
        f"mode={config.monitoring.mode.value}, "
        f"investment=${config.simulation.investment_per_trade}"
    )
    return config
