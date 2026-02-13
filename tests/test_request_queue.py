import asyncio
import time

import pytest

from infra.queue.request_queue import FakeResponse, QueueMetrics, RateLimitExceeded, RequestQueue


def test_serializes_requests_per_host():
    asyncio.run(_test_serializes_requests_per_host())


async def _test_serializes_requests_per_host():
    metrics = QueueMetrics()
    queue = RequestQueue(metrics=metrics, randomizer=lambda a, b: (a + b) / 2)
    started = []

    async def op(name: str):
        started.append(name)
        await asyncio.sleep(0.01)
        return FakeResponse(status=200)

    result1 = asyncio.create_task(queue.enqueue("api.github.com", lambda: op("first")))
    result2 = asyncio.create_task(queue.enqueue("api.github.com", lambda: op("second")))

    responses = await asyncio.gather(result1, result2)
    await queue.close()

    assert [r.status for r in responses] == [200, 200]
    assert started == ["first", "second"], "Requests for the same host should be serialized"
    assert metrics.queue_depths["api.github.com"] == 0
    assert metrics.wait_times["api.github.com"] and all(wait >= 0 for wait in metrics.wait_times["api.github.com"])


def test_backoff_on_rate_limit_and_retry_after_respected():
    asyncio.run(_test_backoff_on_rate_limit_and_retry_after_respected())


async def _test_backoff_on_rate_limit_and_retry_after_respected():
    metrics = QueueMetrics()
    queue = RequestQueue(metrics=metrics, base_backoff=0.01, jitter=0, randomizer=lambda a, b: 0)

    attempts = 0

    async def rate_limited_then_success():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse(status=429, headers={"Retry-After": "0.02"})
        return FakeResponse(status=200)

    start = time.monotonic()
    response = await queue.enqueue("api.github.com", rate_limited_then_success)
    elapsed = time.monotonic() - start
    await queue.close()

    assert response.status == 200
    assert attempts == 2
    assert metrics.backoff_events
    backoff = metrics.backoff_events[0]
    assert backoff.host == "api.github.com"
    assert backoff.retry_after == pytest.approx(0.02, rel=0.2)
    assert elapsed >= 0.02


def test_persists_retry_after_for_subsequent_tasks():
    asyncio.run(_test_persists_retry_after_for_subsequent_tasks())


async def _test_persists_retry_after_for_subsequent_tasks():
    metrics = QueueMetrics()
    queue = RequestQueue(metrics=metrics, base_backoff=0.01, jitter=0, randomizer=lambda a, b: 0)

    async def rate_limited_once():
        return FakeResponse(status=429, headers={"Retry-After": "0.03"})

    start_time = time.monotonic()
    first = asyncio.create_task(queue.enqueue("uploads.github.com", rate_limited_once, max_attempts=1))
    second_started_at = None

    async def second_task():
        nonlocal second_started_at
        second_started_at = time.monotonic()
        return FakeResponse(status=200)

    second = asyncio.create_task(queue.enqueue("uploads.github.com", second_task))

    await asyncio.gather(first, second, return_exceptions=True)
    await queue.close()

    assert isinstance(first.exception(), RateLimitExceeded)
    assert second_started_at is not None
    assert second_started_at - start_time >= queue.metrics.backoff_events[0].delay


def test_metrics_capture_wait_time_and_depth():
    asyncio.run(_test_metrics_capture_wait_time_and_depth())


async def _test_metrics_capture_wait_time_and_depth():
    metrics = QueueMetrics()
    queue = RequestQueue(metrics=metrics, randomizer=lambda a, b: 0)

    async def op(delay: float):
        await asyncio.sleep(delay)
        return FakeResponse(status=200)

    tasks = [asyncio.create_task(queue.enqueue("graph.microsoft.com", lambda d=d: op(d))) for d in (0.01, 0.02, 0.03)]
    await asyncio.gather(*tasks)
    await queue.close()

    depths = metrics.queue_depths["graph.microsoft.com"]
    waits = metrics.wait_times["graph.microsoft.com"]
    assert depths == 0
    assert len(waits) == 3
    assert any(wait > 0 for wait in waits)
