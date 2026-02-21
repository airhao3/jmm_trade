"""Target address management - add, remove, and list tracked addresses."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class TargetManager:
    """Manage tracked wallet addresses in a separate JSON file."""

    def __init__(self, targets_file: str = "config/targets.json"):
        self.targets_file = Path(targets_file)
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Create targets file if it doesn't exist."""
        if not self.targets_file.exists():
            self.targets_file.parent.mkdir(parents=True, exist_ok=True)
            self._save_targets({"targets": []})
            logger.info(f"Created targets file: {self.targets_file}")

    def _load_targets(self) -> dict[str, Any]:
        """Load targets from JSON file."""
        try:
            with open(self.targets_file) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load targets file: {e}")
            return {"targets": []}

    def _save_targets(self, data: dict[str, Any]) -> None:
        """Save targets to JSON file."""
        try:
            with open(self.targets_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved targets to {self.targets_file}")
        except Exception as e:
            logger.error(f"Failed to save targets file: {e}")
            raise

    def add_target(
        self,
        address: str,
        nickname: str,
        enabled: bool = True,
        notes: str = "",
    ) -> bool:
        """Add a new target address.

        Args:
            address: Ethereum address (0x...)
            nickname: Human-readable name
            enabled: Whether to actively track this address
            notes: Optional notes about this target

        Returns:
            True if added successfully, False if already exists
        """
        # Validate address format
        if not address.startswith("0x") or len(address) != 42:
            raise ValueError(f"Invalid Ethereum address: {address}")

        address = address.lower()
        data = self._load_targets()

        # Check if address already exists
        for target in data["targets"]:
            if target["address"].lower() == address:
                logger.warning(f"Target already exists: {address} ({nickname})")
                return False

        # Add new target
        new_target = {
            "address": address,
            "nickname": nickname,
            "enabled": enabled,
            "added_at": datetime.utcnow().isoformat() + "Z",
            "notes": notes,
        }
        data["targets"].append(new_target)
        self._save_targets(data)

        logger.info(f"Added target: {nickname} ({address})")
        return True

    def remove_target(self, identifier: str) -> bool:
        """Remove a target by address or nickname.

        Args:
            identifier: Address (0x...) or nickname

        Returns:
            True if removed, False if not found
        """
        identifier = identifier.lower()
        data = self._load_targets()

        # Find and remove target
        for i, target in enumerate(data["targets"]):
            if (
                target["address"].lower() == identifier
                or target["nickname"].lower() == identifier
            ):
                removed = data["targets"].pop(i)
                self._save_targets(data)
                logger.info(
                    f"Removed target: {removed['nickname']} ({removed['address']})"
                )
                return True

        logger.warning(f"Target not found: {identifier}")
        return False

    def enable_target(self, identifier: str) -> bool:
        """Enable a target by address or nickname."""
        return self._set_enabled(identifier, True)

    def disable_target(self, identifier: str) -> bool:
        """Disable a target by address or nickname."""
        return self._set_enabled(identifier, False)

    def _set_enabled(self, identifier: str, enabled: bool) -> bool:
        """Set enabled status for a target."""
        identifier = identifier.lower()
        data = self._load_targets()

        for target in data["targets"]:
            if (
                target["address"].lower() == identifier
                or target["nickname"].lower() == identifier
            ):
                target["enabled"] = enabled
                self._save_targets(data)
                status = "enabled" if enabled else "disabled"
                logger.info(f"{status.capitalize()} target: {target['nickname']}")
                return True

        logger.warning(f"Target not found: {identifier}")
        return False

    def list_targets(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        """List all targets.

        Args:
            enabled_only: If True, only return enabled targets

        Returns:
            List of target dictionaries
        """
        data = self._load_targets()
        targets = data["targets"]

        if enabled_only:
            targets = [t for t in targets if t.get("enabled", True)]

        return targets

    def get_target(self, identifier: str) -> dict[str, Any] | None:
        """Get a specific target by address or nickname."""
        identifier = identifier.lower()
        data = self._load_targets()

        for target in data["targets"]:
            if (
                target["address"].lower() == identifier
                or target["nickname"].lower() == identifier
            ):
                return target

        return None

    def update_target(
        self,
        identifier: str,
        nickname: str | None = None,
        notes: str | None = None,
    ) -> bool:
        """Update target information.

        Args:
            identifier: Address or current nickname
            nickname: New nickname (optional)
            notes: New notes (optional)

        Returns:
            True if updated, False if not found
        """
        identifier = identifier.lower()
        data = self._load_targets()

        for target in data["targets"]:
            if (
                target["address"].lower() == identifier
                or target["nickname"].lower() == identifier
            ):
                if nickname is not None:
                    target["nickname"] = nickname
                if notes is not None:
                    target["notes"] = notes
                target["updated_at"] = datetime.utcnow().isoformat() + "Z"
                self._save_targets(data)
                logger.info(f"Updated target: {target['nickname']}")
                return True

        logger.warning(f"Target not found: {identifier}")
        return False
