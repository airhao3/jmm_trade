"""Tests for the token-bucket rate limiter (src/api/rate_limiter.py)."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.api.rate_limiter import TokenBucketRateLimiter


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_within_burst():
    """Acquiring tokens within burst size should not block."""
    rl = TokenBucketRateLimiter(max_requests=10, time_window=1, burst_size=5)
    t0 = time.monotonic()
    await rl.acquire(1)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_burst_size_cap():
    """Tokens should never exceed burst_size."""
    rl = TokenBucketRateLimiter(max_requests=100, time_window=1, burst_size=5)
    assert rl.available_tokens <= 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_depletes_tokens():
    """Acquiring tokens should reduce available count."""
    rl = TokenBucketRateLimiter(max_requests=100, time_window=60, burst_size=10)
    initial = rl.available_tokens
    await rl.acquire(3)
    assert rl.available_tokens < initial


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_blocks_when_depleted():
    """When tokens are depleted, acquire should block until refill."""
    rl = TokenBucketRateLimiter(max_requests=10, time_window=1, burst_size=2)
    await rl.acquire(2)  # deplete burst
    t0 = time.monotonic()
    await rl.acquire(1)  # must wait for refill
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.05  # should have waited some time


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_acquire():
    """Multiple coroutines should share tokens fairly."""
    rl = TokenBucketRateLimiter(max_requests=20, time_window=1, burst_size=5)
    results = []

    async def worker(i):
        await rl.acquire(1)
        results.append(i)

    tasks = [asyncio.create_task(worker(i)) for i in range(5)]
    await asyncio.gather(*tasks)
    assert len(results) == 5
