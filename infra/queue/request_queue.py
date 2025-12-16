"""Centralized request queue with rate limit awareness and metrics."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional


@dataclass
class RequestOutcome:
    """Represents the outcome of an executed request."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    payload: Any | None = None


@dataclass
class QueueMetrics:
    """Metrics describing queue state and backoff activity."""

    total_enqueued: int = 0
    completed: int = 0
    backoff_events: int = 0
    queue_depth: int = 0
    average_wait_time: float = 0.0
    last_backoff_seconds: float = 0.0
    retry_after_by_host: Dict[str, float] = field(default_factory=dict)
    _wait_samples: list[float] = field(default_factory=list, repr=False)

    def record_wait(self, duration_seconds: float) -> None:
        self._wait_samples.append(duration_seconds)
        self.average_wait_time = sum(self._wait_samples) / len(self._wait_samples)

    def record_backoff(self, host: str, duration_seconds: float) -> None:
        self.backoff_events += 1
        self.last_backoff_seconds = duration_seconds
        self.retry_after_by_host[host] = time.monotonic() + duration_seconds


@dataclass
class _QueuedRequest:
    host: str
    request_fn: Callable[[], Awaitable[RequestOutcome]]
    future: asyncio.Future[RequestOutcome]
    enqueued_at: float
    attempt: int = 0

    def next_attempt(self) -> "_QueuedRequest":
        return _QueuedRequest(
            host=self.host,
            request_fn=self.request_fn,
            future=self.future,
            enqueued_at=time.monotonic(),
            attempt=self.attempt + 1,
        )


class RateLimitedRequestQueue:
    """Queue that enforces bounded concurrency and rate limit backoff."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        per_host_limit: int = 1,
        base_backoff_seconds: float = 0.25,
        max_backoff_seconds: float = 30.0,
        jitter_ratio: float = 0.25,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if per_host_limit <= 0:
            raise ValueError("per_host_limit must be positive")
        if base_backoff_seconds <= 0:
            raise ValueError("base_backoff_seconds must be positive")
        self._queue: asyncio.Queue[_QueuedRequest | None] = asyncio.Queue()
        self._max_workers = max_workers
        self._per_host_limit = per_host_limit
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._jitter_ratio = jitter_ratio
        self._metrics = QueueMetrics()
        self._workers: list[asyncio.Task[None]] = []
        self._host_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._host_backoff: Dict[str, float] = {}
        self._closed = False

    @property
    def metrics(self) -> QueueMetrics:
        return self._metrics

    def _get_host_semaphore(self, host: str) -> asyncio.Semaphore:
        if host not in self._host_semaphores:
            self._host_semaphores[host] = asyncio.Semaphore(self._per_host_limit)
        return self._host_semaphores[host]

    async def start(self) -> None:
        if self._workers:
            return
        for _ in range(self._max_workers):
            self._workers.append(asyncio.create_task(self._worker()))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def enqueue(
        self,
        host: str,
        request_fn: Callable[[], Awaitable[RequestOutcome]],
    ) -> RequestOutcome:
        if self._closed:
            raise RuntimeError("Cannot enqueue after queue is closed")
        future: asyncio.Future[RequestOutcome] = asyncio.get_event_loop().create_future()
        await self._queue.put(_QueuedRequest(host, request_fn, future, time.monotonic()))
        self._metrics.total_enqueued += 1
        self._metrics.queue_depth = self._queue.qsize()
        return await future

    def _should_backoff(self, outcome: RequestOutcome) -> bool:
        retry_after = self._parse_retry_after(outcome.headers)
        is_rate_limited = outcome.status_code == 429 or retry_after is not None
        secondary_limit = outcome.headers.get("x-ratelimit-remaining") == "0"
        return is_rate_limited or secondary_limit

    def _parse_retry_after(self, headers: Mapping[str, str]) -> Optional[float]:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is None:
            return None
        try:
            return float(retry_after)
        except ValueError:
            return None

    def _backoff_delay(self, attempt: int, retry_after_header: Optional[float]) -> float:
        if retry_after_header is not None:
            delay = retry_after_header
        else:
            delay = self._base_backoff * (2 ** attempt)
        delay += random.uniform(0, delay * self._jitter_ratio)
        return min(delay, self._max_backoff)

    async def _worker(self) -> None:
        while True:
            queued = await self._queue.get()
            if queued is None:
                self._queue.task_done()
                return
            self._metrics.queue_depth = self._queue.qsize()
            wait_time = time.monotonic() - queued.enqueued_at
            self._metrics.record_wait(wait_time)
            now = time.monotonic()
            retry_until = self._host_backoff.get(queued.host)
            if retry_until is not None and retry_until > now:
                sleep_for = retry_until - now
                self._metrics.record_wait(sleep_for)
                await asyncio.sleep(sleep_for)
            semaphore = self._get_host_semaphore(queued.host)
            try:
                async with semaphore:
                    outcome = await queued.request_fn()
            except Exception as exc:  # noqa: BLE001 - propagate failure to caller
                queued.future.set_exception(exc)
                self._queue.task_done()
                self._metrics.queue_depth = self._queue.qsize()
                continue

            if self._should_backoff(outcome):
                retry_after_header = self._parse_retry_after(outcome.headers)
                delay = self._backoff_delay(queued.attempt, retry_after_header)
                self._metrics.record_backoff(queued.host, delay)
                self._host_backoff[queued.host] = time.monotonic() + delay
                self._queue.task_done()
                self._metrics.queue_depth = self._queue.qsize()
                asyncio.create_task(self._requeue_after_delay(queued, delay))
                continue

            queued.future.set_result(outcome)
            self._metrics.completed += 1
            self._queue.task_done()
            self._metrics.queue_depth = self._queue.qsize()

    async def _requeue_after_delay(self, queued: _QueuedRequest, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._queue.put(queued.next_attempt())
        self._metrics.queue_depth = self._queue.qsize()

