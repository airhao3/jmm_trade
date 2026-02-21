"""Async Polymarket API client built on aiohttp."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import aiohttp
from loguru import logger

from src.config.models import APIConfig, SystemConfig

from .rate_limiter import TokenBucketRateLimiter

# ── Safety guard ─────────────────────────────────────────
READ_ONLY_MODE = True


def _assert_read_only() -> None:
    """Dual check: module-level flag AND env var."""
    if not READ_ONLY_MODE or os.getenv("FORCE_READ_ONLY", "true").lower() == "true":
        return  # read-only is active – safe
    raise RuntimeError("READ_ONLY_MODE has been disabled – refusing to proceed")


class PolymarketClient:
    """Fully async, rate-limited Polymarket REST client.

    Usage::

        async with PolymarketClient(api_config, system_config) as client:
            trades = await client.get_trades(address)
    """

    def __init__(
        self,
        api_config: APIConfig,
        system_config: SystemConfig,
        rate_limiter: TokenBucketRateLimiter | None = None,
    ) -> None:
        self.config = api_config
        self.system = system_config
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            max_requests=api_config.rate_limit.max_requests,
            time_window=api_config.rate_limit.time_window,
            burst_size=api_config.rate_limit.burst_size,
        )
        self._session: aiohttp.ClientSession | None = None
        self._request_count: int = 0
        self._total_latency: float = 0.0
        self._on_latency: Any | None = None  # callback(latency_seconds)

    def set_latency_callback(self, callback: Any) -> None:
        """Register a callback invoked with each request's latency in seconds."""
        self._on_latency = callback

    # ── Context manager ──────────────────────────────────

    async def __aenter__(self) -> PolymarketClient:
        headers = {"Accept": "application/json"}

        # Attach API key if available (read-only scope)
        api_key = os.getenv("POLYMARKET_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.timeout),
            headers=headers,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Generic request ──────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> Any:
        """HTTP request with rate limiting, retries, and latency tracking."""
        _assert_read_only()
        await self.rate_limiter.acquire()

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                t0 = time.monotonic()
                async with self._session.request(method, url, **kwargs) as resp:
                    latency = time.monotonic() - t0
                    self._request_count += 1
                    self._total_latency += latency
                    if self._on_latency:
                        self._on_latency(latency)

                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 2))
                        logger.warning(f"Rate limited, waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue

                    # Don't retry client errors (4xx except 429) — they're permanent
                    if 400 <= resp.status < 500:
                        logger.debug(f"[{method}] {url} -> {resp.status} (no retry)")
                        return None

                    resp.raise_for_status()
                    data = await resp.json()
                    logger.debug(f"[{method}] {url} -> {resp.status} ({latency:.3f}s)")
                    return data

            except (TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    f"Request failed ({attempt}/{max_retries}): {exc} – retrying in {wait}s"
                )
                if attempt < max_retries:
                    await asyncio.sleep(wait)

        raise ConnectionError(
            f"All {max_retries} retries exhausted for {method} {url}"
        ) from last_exc

    # ── Metrics ──────────────────────────────────────────

    @property
    def avg_latency(self) -> float:
        if self._request_count == 0:
            return 0.0
        return self._total_latency / self._request_count

    # ── Data API ─────────────────────────────────────────

    async def get_trades(
        self,
        user_address: str,
        limit: int = 100,
        offset: int = 0,
        side: str | None = None,
    ) -> list[dict]:
        """GET /trades – fetch trade history for a user."""
        url = f"{self.config.base_urls['data']}/trades"
        params: dict[str, Any] = {
            "user": user_address,
            "limit": min(limit, 10000),
            "offset": offset,
        }
        if side:
            params["side"] = side
        return await self._request("GET", url, params=params) or []

    async def get_activity(
        self,
        user_address: str,
        limit: int = 100,
        activity_type: str = "TRADE",
    ) -> list[dict]:
        """GET /activity – fetch activity log for a user."""
        url = f"{self.config.base_urls['data']}/activity"
        params: dict[str, Any] = {
            "user": user_address,
            "limit": min(limit, 500),
            "type": activity_type,
        }
        return await self._request("GET", url, params=params) or []

    async def get_positions(self, user_address: str) -> list[dict]:
        """GET /positions – current open positions for a user."""
        url = f"{self.config.base_urls['data']}/positions"
        params = {"user": user_address}
        return await self._request("GET", url, params=params) or []

    # ── CLOB API ─────────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> dict:
        """GET /book – order book for a specific token."""
        url = f"{self.config.base_urls['clob']}/book"
        params = {"token_id": token_id}
        return await self._request("GET", url, params=params) or {"asks": [], "bids": []}

    async def get_price(self, token_id: str) -> dict:
        """GET /price – current price for a token."""
        url = f"{self.config.base_urls['clob']}/price"
        params = {"token_id": token_id}
        return await self._request("GET", url, params=params)

    async def get_midpoint(self, token_id: str) -> dict:
        """GET /midpoint – midpoint price."""
        url = f"{self.config.base_urls['clob']}/midpoint"
        params = {"token_id": token_id}
        return await self._request("GET", url, params=params)

    async def get_spread(self, token_id: str) -> dict:
        """GET /spread – bid-ask spread."""
        url = f"{self.config.base_urls['clob']}/spread"
        params = {"token_id": token_id}
        return await self._request("GET", url, params=params)

    async def batch_get_orderbooks(self, token_ids: list[str]) -> dict[str, dict]:
        """Concurrently fetch order books for multiple tokens."""
        tasks = {tid: self.get_orderbook(tid) for tid in token_ids}
        results: dict[str, dict] = {}
        for tid, coro in tasks.items():
            try:
                results[tid] = await coro
            except Exception as exc:
                logger.warning(f"Failed to fetch orderbook for {tid}: {exc}")
        return results

    # ── Gamma API ────────────────────────────────────────

    async def get_market(self, condition_id: str) -> dict:
        """GET /markets?condition_id= – market metadata."""
        url = f"{self.config.base_urls['gamma']}/markets"
        data = await self._request("GET", url, params={"condition_id": condition_id})
        if data is None:
            return {}
        if isinstance(data, list):
            return data[0] if data else {}
        return data

    async def get_event(self, event_id: str) -> dict:
        """GET /events?id= – event metadata."""
        url = f"{self.config.base_urls['gamma']}/events"
        data = await self._request("GET", url, params={"id": event_id})
        if data is None:
            return {}
        if isinstance(data, list):
            return data[0] if data else {}
        return data

    async def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        """GET /markets – search/list markets."""
        url = f"{self.config.base_urls['gamma']}/markets"
        params: dict[str, Any] = {"limit": limit}
        if query:
            # Gamma API uses a general listing; filter client-side
            pass
        return await self._request("GET", url, params=params)

    # ── Enrichment APIs ─────────────────────────────────

    async def get_leaderboard_rank(self, user_address: str) -> dict | None:
        """Fetch user's leaderboard profile (profit, rank, volume)."""
        url = f"{self.config.base_urls['data']}/leaderboard"
        data = await self._request("GET", url, params={"user": user_address})
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return None

    async def get_profit_stats(self, user_address: str) -> dict:
        """Fetch user's overall profit/volume stats from profile API."""
        url = f"{self.config.base_urls['data']}/profile"
        data = await self._request("GET", url, params={"user": user_address})
        if isinstance(data, dict):
            return data
        return {}

    async def get_market_by_slug(self, slug: str) -> dict:
        """GET /markets?slug= – full market details by slug."""
        url = f"{self.config.base_urls['gamma']}/markets"
        data = await self._request("GET", url, params={"slug": slug})
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}

    async def get_user_market_positions(
        self, user_address: str, condition_id: str
    ) -> list[dict]:
        """Get user's positions in a specific market."""
        positions = await self.get_positions(user_address)
        return [
            p for p in positions
            if p.get("conditionId") == condition_id or p.get("asset") == condition_id
        ]

    async def get_market_volume(
        self, condition_id: str
    ) -> dict:
        """GET /markets?condition_id= – for volume & liquidity data."""
        return await self.get_market(condition_id)

    # ── Market-level trades (for alpha discovery) ──────

    async def get_market_trades(
        self,
        asset_id: str,
        limit: int = 500,
    ) -> list[dict]:
        """GET /trades – fetch ALL trades for a market token (not user-specific).

        Used by alpha discovery to find profitable early entries.
        """
        url = f"{self.config.base_urls['data']}/trades"
        params: dict[str, Any] = {
            "asset": asset_id,
            "limit": min(limit, 10000),
        }
        return await self._request("GET", url, params=params) or []

    # ── Forbidden operations ─────────────────────────────

    async def create_order(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """BLOCKED – create_order is disabled in read-only mode."""
        raise RuntimeError(
            "create_order is PERMANENTLY DISABLED. "
            "READ_ONLY_MODE is active. No real orders will ever be placed."
        )

    async def cancel_order(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        """BLOCKED – cancel_order is disabled in read-only mode."""
        raise RuntimeError("cancel_order is PERMANENTLY DISABLED. READ_ONLY_MODE is active.")
