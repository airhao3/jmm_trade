"""Token-bucket rate limiter for async API calls."""

from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """Token Bucket algorithm with burst support.

    Parameters
    ----------
    max_requests : int
        Sustained request rate (requests per *time_window*).
    time_window : int
        Window in seconds over which *max_requests* applies.
    burst_size : int | None
        Maximum tokens stored (allows short bursts). Defaults to *max_requests*.
    """

    def __init__(
        self,
        max_requests: int = 100,
        time_window: int = 60,
        burst_size: int | None = None,
    ) -> None:
        self.max_requests = max_requests
        self.time_window = time_window
        self.burst_size = burst_size or max_requests
        self._refill_rate = max_requests / time_window  # tokens/sec

        self._tokens: float = float(self.burst_size)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    # ── public ───────────────────────────────────────────

    async def acquire(self, tokens: int = 1) -> None:
        """Wait until *tokens* are available, then consume them."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Calculate wait time outside lock
                deficit = tokens - self._tokens
                wait = deficit / self._refill_rate

            # Sleep WITHOUT holding the lock
            await asyncio.sleep(max(wait, 0.01))

    @property
    def available_tokens(self) -> float:
        return self._tokens

    # ── private ──────────────────────────────────────────

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self.burst_size,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now
