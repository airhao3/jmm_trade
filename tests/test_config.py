"""Tests for configuration validation (src/config/models.py)."""

from __future__ import annotations

import os
from copy import deepcopy

import pytest

from src.config.models import (
    AppConfig,
    LoggingConfig,
    MarketFilterConfig,
    SimulationConfig,
    SystemConfig,
    TargetAccount,
)

# ── C01: Valid config loads ──────────────────────────────


@pytest.mark.unit
def test_valid_config_loads(raw_config_dict):
    config = AppConfig(**raw_config_dict)
    assert config.system.read_only_mode is True
    assert len(config.get_active_targets()) == 1
    assert config.monitoring.poll_interval == 1


# ── C02: Invalid address rejected ────────────────────────


@pytest.mark.unit
def test_invalid_address_rejected():
    with pytest.raises((ValueError, Exception)):
        TargetAccount(address="0xZZZinvalid", nickname="bad", active=True)


@pytest.mark.unit
def test_short_address_rejected():
    with pytest.raises((ValueError, Exception)):
        TargetAccount(address="0xabc", nickname="short", active=True)


# ── C03: Negative delay rejected ────────────────────────


@pytest.mark.unit
def test_negative_delay_rejected():
    with pytest.raises((ValueError, Exception)):
        SimulationConfig(delays=[-1, 3])


@pytest.mark.unit
def test_zero_delay_accepted():
    sim = SimulationConfig(delays=[0, 3])
    assert sim.delays == [0, 3]


# ── C04: Delays auto-sorted and deduplicated ────────────


@pytest.mark.unit
def test_delays_auto_sorted():
    sim = SimulationConfig(delays=[3, 1, 1])
    assert sim.delays == [1, 3]


@pytest.mark.unit
def test_delays_single():
    sim = SimulationConfig(delays=[5])
    assert sim.delays == [5]


# ── C05: Duration range enforced ────────────────────────


@pytest.mark.unit
def test_duration_range_invalid():
    with pytest.raises((ValueError, Exception)):
        MarketFilterConfig(
            assets=["BTC"],
            min_duration_minutes=15,
            max_duration_minutes=5,
            keywords=["up"],
        )


@pytest.mark.unit
def test_duration_range_equal_is_ok():
    mf = MarketFilterConfig(
        assets=["BTC"],
        min_duration_minutes=10,
        max_duration_minutes=10,
        keywords=["up"],
    )
    assert mf.min_duration_minutes == 10


# ── C06: Invalid log level ──────────────────────────────


@pytest.mark.unit
def test_invalid_log_level():
    with pytest.raises((ValueError, Exception)):
        LoggingConfig(level="VERBOSE")


@pytest.mark.unit
def test_valid_log_level():
    lc = LoggingConfig(level="debug")
    assert lc.level == "DEBUG"


# ── C07: FORCE_READ_ONLY env override ───────────────────


@pytest.mark.unit
def test_force_read_only_env_override():
    os.environ["FORCE_READ_ONLY"] = "true"
    sc = SystemConfig(read_only_mode=False, force_read_only=False)
    assert sc.read_only_mode is True
    assert sc.force_read_only is True


# ── C08: No active targets ──────────────────────────────


@pytest.mark.unit
def test_no_active_targets_rejected(raw_config_dict):
    d = deepcopy(raw_config_dict)
    d["targets"][0]["active"] = False
    with pytest.raises((ValueError, Exception)):
        AppConfig(**d)


# ── C09: Address auto-lowercased ─────────────────────────


@pytest.mark.unit
def test_address_auto_lowercased():
    ta = TargetAccount(
        address="0xAABBCCDD11223344556677889900AABBCCDDEEFF",
        nickname="Upper",
    )
    assert ta.address == "0xaabbccdd11223344556677889900aabbccddeeff"


# ── C10: Fee rate boundaries ─────────────────────────────


@pytest.mark.unit
def test_fee_rate_too_high():
    with pytest.raises((ValueError, Exception)):
        SimulationConfig(fee_rate=0.5)


@pytest.mark.unit
def test_fee_rate_zero_ok():
    sim = SimulationConfig(fee_rate=0.0)
    assert sim.fee_rate == 0.0
