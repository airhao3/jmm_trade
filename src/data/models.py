"""Data models for sim trades, market cache, and metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class SimTrade:
    """A single simulated trade record."""

    trade_id: str
    target_address: str
    target_nickname: str

    # Market
    market_id: str
    market_name: str
    condition_id: str
    event_slug: str = ""

    # Target trade info
    target_side: str = "BUY"
    target_price: float = 0.0
    target_size: float = 0.0
    target_timestamp: int = 0
    target_execution_time: Optional[int] = None
    target_pnl: Optional[float] = None

    # Simulation
    sim_delay: int = 0
    sim_price: Optional[float] = None
    sim_delayed_price: Optional[float] = None
    sim_investment: float = 100.0
    sim_fee: float = 0.0
    sim_success: bool = True
    sim_failure_reason: Optional[str] = None

    # Cost
    slippage_pct: Optional[float] = None
    total_cost: float = 0.0

    # Status
    status: str = TradeStatus.OPEN.value
    settlement_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None

    # Timestamps
    created_at: Optional[str] = None
    settled_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class MarketInfo:
    """Cached market metadata."""

    condition_id: str
    market_id: str = ""
    market_name: str = ""
    event_slug: str = ""
    end_date: Optional[str] = None
    is_active: bool = True
    is_resolved: bool = False
    resolution_price: Optional[float] = None
    cache_ttl: int = 3600
    last_updated: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class MetricRecord:
    """A single metrics data point."""

    metric_type: str
    metric_value: float
    metadata: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class AccountStats:
    """Aggregated statistics for display."""

    total_trades: int = 0
    open_positions: int = 0
    settled_trades: int = 0
    failed_trades: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_slippage: float = 0.0
    avg_fee: float = 0.0
    total_investment: float = 0.0
    total_simulated: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
