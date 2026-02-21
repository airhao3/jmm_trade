"""Pydantic configuration models with full validation."""

from __future__ import annotations

import os
import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class MonitorMode(StrEnum):
    POLL = "poll"
    WEBSOCKET = "websocket"


class LogFormat(StrEnum):
    JSON = "json"
    TEXT = "text"


# ── System ───────────────────────────────────────────────
class SystemConfig(BaseModel):
    read_only_mode: bool = True
    force_read_only: bool = True

    @model_validator(mode="after")
    def enforce_read_only(self) -> SystemConfig:
        env_override = os.getenv("FORCE_READ_ONLY", "true").lower()
        if env_override == "true":
            self.read_only_mode = True
            self.force_read_only = True
        return self


# ── Monitoring ───────────────────────────────────────────
class MonitoringConfig(BaseModel):
    mode: MonitorMode = MonitorMode.POLL
    poll_interval: int = Field(ge=1, le=60, default=3)
    max_concurrent: int = Field(ge=1, le=50, default=5)
    retry_on_error: bool = True
    max_retries: int = Field(ge=0, le=10, default=3)


# ── Simulation ───────────────────────────────────────────
class SimulationConfig(BaseModel):
    delays: list[int] = Field(default=[1, 3])
    investment_per_trade: float = Field(ge=1.0, default=100.0)
    fee_rate: float = Field(ge=0.0, le=0.1, default=0.015)
    enable_slippage_check: bool = True
    max_slippage_pct: float = Field(ge=0.0, default=5.0)

    @field_validator("delays")
    @classmethod
    def validate_delays(cls, v: list[int]) -> list[int]:
        if not all(d >= 0 for d in v):
            raise ValueError("All delays must be non-negative integers")
        return sorted(set(v))


# ── Market Filter ────────────────────────────────────────
class MarketFilterConfig(BaseModel):
    enabled: bool = True
    assets: list[str] = Field(default=["BTC", "ETH", "Bitcoin", "Ethereum"])
    min_duration_minutes: int = Field(ge=1, default=5)
    max_duration_minutes: int = Field(ge=1, default=15)
    keywords: list[str] = Field(default=["up", "down", "higher", "lower"])
    exclude_keywords: list[str] = Field(default=[])

    @model_validator(mode="after")
    def check_duration_range(self) -> MarketFilterConfig:
        if self.min_duration_minutes > self.max_duration_minutes:
            raise ValueError("min_duration_minutes must be <= max_duration_minutes")
        return self


# ── Target Accounts ──────────────────────────────────────
class TargetAccount(BaseModel):
    address: str
    nickname: str
    active: bool = True
    weight: float = Field(ge=0.0, le=1.0, default=1.0)

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not re.match(r"^0x[a-fA-F0-9]{40}$", v):
            raise ValueError(f"Invalid Ethereum address: {v}")
        return v.lower()


# ── API ──────────────────────────────────────────────────
class RateLimitConfig(BaseModel):
    max_requests: int = Field(ge=1, default=100)
    time_window: int = Field(ge=1, default=60)
    burst_size: int = Field(ge=1, default=10)


class APIConfig(BaseModel):
    base_urls: dict[str, str]
    websocket_urls: dict[str, str] = Field(default={})
    timeout: int = Field(ge=5, le=120, default=30)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


# ── Notifications ────────────────────────────────────────
class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str | None = None
    chat_id: str | None = None
    rate_limit: int = Field(ge=1, default=20)

    @model_validator(mode="after")
    def resolve_env_vars(self) -> TelegramConfig:
        if self.enabled:
            if self.bot_token and self.bot_token.startswith("${"):
                env_key = self.bot_token[2:-1]
                self.bot_token = os.getenv(env_key)
            if self.chat_id and self.chat_id.startswith("${"):
                env_key = self.chat_id[2:-1]
                self.chat_id = os.getenv(env_key)
        return self


class IMessageConfig(BaseModel):
    enabled: bool = False
    phone_number: str | None = None

    @model_validator(mode="after")
    def resolve_env_vars(self) -> IMessageConfig:
        if self.enabled and self.phone_number and self.phone_number.startswith("${"):
            env_key = self.phone_number[2:-1]
            self.phone_number = os.getenv(env_key)
        return self


class NotificationsConfig(BaseModel):
    enabled: bool = True
    aggregation_interval: int = Field(ge=5, default=30)
    max_retries: int = Field(ge=0, default=3)
    retry_backoff: list[int] = Field(default=[1, 2, 4])
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    imessage: IMessageConfig = Field(default_factory=IMessageConfig)


# ── Database ─────────────────────────────────────────────
class DatabaseConfig(BaseModel):
    path: str = "data/trades.db"
    backup_enabled: bool = True
    backup_interval: int = Field(ge=60, default=3600)
    market_cache_ttl: int = Field(ge=60, default=3600)
    auto_vacuum: bool = True


# ── Export ────────────────────────────────────────────────
class ExportConfig(BaseModel):
    enabled: bool = True
    csv_path: str = "data/exports/"
    auto_export_interval: int = Field(ge=60, default=3600)
    include_headers: bool = True


# ── Logging ──────────────────────────────────────────────
class ConsoleLayoutConfig(BaseModel):
    sections: list[str] = Field(default=["system_status", "dashboard", "recent_trades", "event_stream"])
    limits: dict[str, int] = Field(default={"recent_trades": 5, "event_stream": 10})


class ConsoleColorsConfig(BaseModel):
    enabled: bool = True
    scheme: str = "professional"


class ConsoleEventsConfig(BaseModel):
    show: list[str] = Field(default=["system", "trade", "simulation", "notification", "settlement", "error", "warning"])
    hide: list[str] = Field(default=["debug"])


class ConsoleConfig(BaseModel):
    enabled: bool = True
    mode: str = "live"  # live | scroll | minimal
    refresh_interval: float = Field(ge=0.1, le=5.0, default=1.0)
    layout: ConsoleLayoutConfig = Field(default_factory=ConsoleLayoutConfig)
    colors: ConsoleColorsConfig = Field(default_factory=ConsoleColorsConfig)
    events: ConsoleEventsConfig = Field(default_factory=ConsoleEventsConfig)


class FileLogConfig(BaseModel):
    enabled: bool = True
    path: str
    level: str = "INFO"
    format: str = "structured"
    rotation: str = "100 MB"
    retention: str = "30 days"


class MetricsFileConfig(BaseModel):
    enabled: bool = True
    path: str = "logs/metrics.json"
    level: str = "INFO"
    format: str = "json"
    rotation: str = "100 MB"
    retention: str = "7 days"
    interval: int = Field(ge=60, default=300)


class FilesConfig(BaseModel):
    main: FileLogConfig = Field(default_factory=lambda: FileLogConfig(path="logs/bot.log"))
    trades: FileLogConfig = Field(default_factory=lambda: FileLogConfig(path="logs/trades.log", format="table", retention="90 days"))
    metrics: MetricsFileConfig = Field(default_factory=MetricsFileConfig)
    errors: FileLogConfig = Field(default_factory=lambda: FileLogConfig(path="logs/errors.log", level="WARN", format="detailed", retention="90 days"))


class LoggingConfig(BaseModel):
    level: str = "INFO"
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"Invalid log level: {v}. Must be one of {allowed}")
        return v.upper()


# ── Root Config ──────────────────────────────────────────
class AppConfig(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    market_filter: MarketFilterConfig = Field(default_factory=MarketFilterConfig)
    targets: list[TargetAccount] = Field(default=[])
    api: APIConfig
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def validate_has_targets(self) -> AppConfig:
        active = [t for t in self.targets if t.active]
        if not active:
            raise ValueError("At least one active target account is required")
        return self

    def get_active_targets(self) -> list[TargetAccount]:
        return [t for t in self.targets if t.active]
