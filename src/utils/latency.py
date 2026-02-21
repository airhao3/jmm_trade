"""Network latency checker for API endpoints."""

from __future__ import annotations

import asyncio
import time
from typing import NamedTuple

import aiohttp
from loguru import logger


class LatencyResult(NamedTuple):
    """Result of a latency test."""

    endpoint: str
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    samples: int
    success_rate: float


class LatencyChecker:
    """Check network latency to API endpoints."""

    # Latency thresholds for copy trading viability
    EXCELLENT_THRESHOLD_MS = 100  # <100ms: Excellent for high-frequency trading
    GOOD_THRESHOLD_MS = 200  # <200ms: Good for short-term markets (5-15min)
    ACCEPTABLE_THRESHOLD_MS = 500  # <500ms: Acceptable for longer markets (>30min)
    POOR_THRESHOLD_MS = 1000  # >1000ms: Poor, not recommended

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    async def check_endpoint(
        self, url: str, samples: int = 5, delay_between_samples: float = 0.5
    ) -> LatencyResult:
        """Test latency to a specific endpoint.

        Args:
            url: Full URL to test (e.g., https://clob.polymarket.com/healthcheck)
            samples: Number of test samples to take
            delay_between_samples: Seconds to wait between samples

        Returns:
            LatencyResult with statistics
        """
        latencies: list[float] = []
        failures = 0

        async with aiohttp.ClientSession() as session:
            for i in range(samples):
                try:
                    start = time.perf_counter()
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                        headers={"Cache-Control": "no-cache"},
                    ) as response:
                        await response.read()  # Ensure full response is received
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        latencies.append(elapsed_ms)
                        logger.debug(f"Latency test {i + 1}/{samples}: {elapsed_ms:.1f}ms")
                except Exception as exc:
                    failures += 1
                    logger.warning(f"Latency test {i + 1}/{samples} failed: {exc}")

                if i < samples - 1:  # Don't delay after last sample
                    await asyncio.sleep(delay_between_samples)

        if not latencies:
            logger.error(f"All latency tests failed for {url}")
            return LatencyResult(
                endpoint=url,
                avg_latency_ms=float("inf"),
                min_latency_ms=float("inf"),
                max_latency_ms=float("inf"),
                samples=samples,
                success_rate=0.0,
            )

        return LatencyResult(
            endpoint=url,
            avg_latency_ms=sum(latencies) / len(latencies),
            min_latency_ms=min(latencies),
            max_latency_ms=max(latencies),
            samples=samples,
            success_rate=len(latencies) / samples,
        )

    def assess_viability(self, avg_latency_ms: float) -> tuple[str, str]:
        """Assess trading viability based on latency.

        Returns:
            Tuple of (rating, recommendation)
        """
        if avg_latency_ms < self.EXCELLENT_THRESHOLD_MS:
            return (
                "EXCELLENT",
                "Ideal for high-frequency copy trading on all market types",
            )
        elif avg_latency_ms < self.GOOD_THRESHOLD_MS:
            return (
                "GOOD",
                "Suitable for short-term markets (5-15min) with competitive execution",
            )
        elif avg_latency_ms < self.ACCEPTABLE_THRESHOLD_MS:
            return (
                "ACCEPTABLE",
                "Suitable for longer markets (>30min), may struggle with fast markets",
            )
        elif avg_latency_ms < self.POOR_THRESHOLD_MS:
            return (
                "POOR",
                "High latency - only suitable for long-term markets (>1 hour)",
            )
        else:
            return (
                "UNSUITABLE",
                "Latency too high for copy trading - consider relocating VPS",
            )

    async def check_polymarket_apis(self) -> dict[str, LatencyResult]:
        """Check latency to all Polymarket API endpoints.

        Returns:
            Dict mapping endpoint name to LatencyResult
        """
        endpoints = {
            "CLOB": "https://clob.polymarket.com/healthcheck",
            "Gamma": "https://gamma-api.polymarket.com/healthcheck",
        }

        results = {}
        for name, url in endpoints.items():
            logger.info(f"Testing latency to {name} API ({url})...")
            result = await self.check_endpoint(url)
            results[name] = result

            rating, recommendation = self.assess_viability(result.avg_latency_ms)
            logger.info(
                f"{name} API latency: {result.avg_latency_ms:.1f}ms "
                f"(min: {result.min_latency_ms:.1f}ms, max: {result.max_latency_ms:.1f}ms) "
                f"- Rating: {rating}"
            )
            logger.info(f"  → {recommendation}")

        return results

    def log_summary(self, results: dict[str, LatencyResult]) -> None:
        """Log a summary of latency test results."""
        if not results:
            logger.warning("No latency test results to summarize")
            return

        logger.info("=" * 60)
        logger.info("NETWORK LATENCY ASSESSMENT")
        logger.info("=" * 60)

        for name, result in results.items():
            rating, recommendation = self.assess_viability(result.avg_latency_ms)
            logger.info(f"{name} API:")
            logger.info(f"  Average: {result.avg_latency_ms:.1f}ms")
            logger.info(f"  Range: {result.min_latency_ms:.1f}ms - {result.max_latency_ms:.1f}ms")
            logger.info(f"  Success Rate: {result.success_rate * 100:.0f}%")
            logger.info(f"  Rating: {rating}")
            logger.info(f"  Recommendation: {recommendation}")
            logger.info("")

        # Overall assessment
        avg_overall = sum(r.avg_latency_ms for r in results.values()) / len(results)
        overall_rating, overall_rec = self.assess_viability(avg_overall)
        logger.info(f"Overall Average Latency: {avg_overall:.1f}ms")
        logger.info(f"Overall Rating: {overall_rating}")
        logger.info(f"Overall Recommendation: {overall_rec}")
        logger.info("=" * 60)
