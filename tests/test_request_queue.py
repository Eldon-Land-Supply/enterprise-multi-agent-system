import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.queue import RateLimitedRequestQueue, RequestOutcome


def test_serializes_requests_by_host():
    async def scenario():
        queue = RateLimitedRequestQueue(max_workers=2, per_host_limit=1)
        await queue.start()

        in_progress = 0
        observed_max = 0

        async def tracked_request():
            nonlocal in_progress, observed_max
            in_progress += 1
            observed_max = max(observed_max, in_progress)
            await asyncio.sleep(0.05)
            in_progress -= 1
            return RequestOutcome(status_code=200, headers={})

        await asyncio.gather(
            queue.enqueue("example.com", tracked_request),
            queue.enqueue("example.com", tracked_request),
        )
        await queue.close()

        return observed_max

    observed_max = asyncio.run(scenario())
    assert observed_max == 1, "per-host serialization was not enforced"


def test_exponential_backoff_and_retry_after_is_persisted():
    async def scenario():
        queue = RateLimitedRequestQueue(base_backoff_seconds=0.1, jitter_ratio=0.0)
        await queue.start()

        attempts = 0

        async def flaky_request():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return RequestOutcome(status_code=429, headers={"retry-after": "0.2"})
            return RequestOutcome(status_code=200, headers={})

        result_task = asyncio.create_task(queue.enqueue("api.github.com", flaky_request))
        await asyncio.wait_for(result_task, timeout=2)
        await queue.close()
        return attempts, queue.metrics

    attempts, metrics = asyncio.run(scenario())

    assert attempts == 2
    assert metrics.backoff_events == 1
    assert pytest.approx(metrics.last_backoff_seconds, rel=0.1) == 0.2
    assert "api.github.com" in metrics.retry_after_by_host


def test_metrics_capture_queue_depth_and_wait_times():
    async def scenario():
        queue = RateLimitedRequestQueue(max_workers=1, per_host_limit=1)
        await queue.start()

        async def slow_request(delay: float):
            await asyncio.sleep(delay)
            return RequestOutcome(status_code=200, headers={})

        start = time.monotonic()
        task1 = asyncio.create_task(queue.enqueue("alpha", lambda: slow_request(0.05)))
        task2 = asyncio.create_task(queue.enqueue("alpha", lambda: slow_request(0.05)))
        await asyncio.gather(task1, task2)
        await queue.close()
        elapsed = time.monotonic() - start

        return elapsed, queue.metrics

    elapsed, metrics = asyncio.run(scenario())

    assert elapsed >= 0.09
    assert metrics.completed == 2
    assert metrics.total_enqueued == 2
    assert metrics.queue_depth == 0
    assert metrics.average_wait_time > 0
