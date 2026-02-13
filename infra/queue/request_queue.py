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
import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional


@dataclass
class FakeResponse:
    """Lightweight response container for tests and adapters."""

    status: int
    headers: Dict[str, Any] = field(default_factory=dict)
    payload: Any = None


@dataclass
class BackoffEvent:
    host: str
    attempt: int
    delay: float
    retry_after: Optional[float]
    status: int


class QueueMetrics:
    def __init__(self) -> None:
        self.queue_depths: Dict[str, int] = defaultdict(int)
        self.wait_times: Dict[str, list[float]] = defaultdict(list)
        self.backoff_events: list[BackoffEvent] = []

    def record_depth(self, host: str, depth: int) -> None:
        self.queue_depths[host] = depth

    def record_wait_time(self, host: str, wait_time: float) -> None:
        self.wait_times[host].append(wait_time)

    def record_backoff(
        self, host: str, attempt: int, delay: float, retry_after: Optional[float], status: int
    ) -> None:
        self.backoff_events.append(BackoffEvent(host, attempt, delay, retry_after, status))


@dataclass
class _RequestTask:
    operation: Callable[[], Awaitable[Any]]
    future: asyncio.Future
    enqueued_at: float
    attempt: int = 0
    max_attempts: int = 5


@dataclass
class _HostState:
    queue: asyncio.Queue[_RequestTask]
    retry_after: float = 0.0
    backoff_attempts: int = 0
    workers: list[asyncio.Task] = field(default_factory=list)


class RateLimitExceeded(Exception):
    pass


class RequestQueue:
    def __init__(
        self,
        *,
        default_concurrency: int = 1,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
        jitter: float = 0.25,
        metrics: Optional[QueueMetrics] = None,
        randomizer: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._default_concurrency = max(1, default_concurrency)
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._jitter = jitter
        self._metrics = metrics or QueueMetrics()
        self._randomizer = randomizer
        self._host_states: Dict[str, _HostState] = {}
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
            if queued.future.cancelled():
                self._queue.task_done()
                self._metrics.queue_depth = self._queue.qsize()
                continue
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
                self._try_set_future_exception(queued.future, exc)
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

            if self._try_set_future_result(queued.future, outcome):
                self._metrics.completed += 1
            self._queue.task_done()
            self._metrics.queue_depth = self._queue.qsize()

    async def _requeue_after_delay(self, queued: _QueuedRequest, delay: float) -> None:
        await asyncio.sleep(delay)
        if queued.future.cancelled():
            return
        await self._queue.put(queued.next_attempt())
        self._metrics.queue_depth = self._queue.qsize()

    def _try_set_future_result(
        self,
        future: asyncio.Future[RequestOutcome],
        outcome: RequestOutcome,
    ) -> bool:
        if future.done():
            return False
        try:
            future.set_result(outcome)
        except asyncio.InvalidStateError:
            return False
        return True

    def _try_set_future_exception(
        self,
        future: asyncio.Future[RequestOutcome],
        exc: Exception,
    ) -> None:
        if future.done():
            return
        try:
            future.set_exception(exc)
        except asyncio.InvalidStateError:
            return
    async def enqueue(
        self,
        host: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        max_attempts: int = 5,
    ) -> Any:
        if self._closed:
            raise RuntimeError("RequestQueue is closed")

        state = self._ensure_host(host)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        task = _RequestTask(operation=operation, future=future, enqueued_at=time.monotonic(), max_attempts=max_attempts)
        await state.queue.put(task)
        self._metrics.record_depth(host, state.queue.qsize())
        return await future

    def _ensure_host(self, host: str) -> _HostState:
        if host in self._host_states:
            return self._host_states[host]

        queue: asyncio.Queue[_RequestTask] = asyncio.Queue()
        state = _HostState(queue=queue)
        self._host_states[host] = state

        for _ in range(self._default_concurrency):
            worker = asyncio.create_task(self._worker(host, state))
            state.workers.append(worker)
        return state

    async def _worker(self, host: str, state: _HostState) -> None:
        while not self._closed:
            task: _RequestTask = await state.queue.get()
            wait_time = time.monotonic() - task.enqueued_at
            self._metrics.record_wait_time(host, wait_time)
            await self._respect_retry_after(state)
            await self._execute_task(host, state, task)
            state.queue.task_done()
            self._metrics.record_depth(host, state.queue.qsize())

    async def _execute_task(self, host: str, state: _HostState, task: _RequestTask) -> None:
        task.attempt += 1
        try:
            response = await task.operation()
        except Exception as exc:  # pragma: no cover - passthrough for unexpected errors
            if not task.future.done():
                task.future.set_exception(exc)
            return

        if self._is_rate_limited(response):
            await self._handle_backoff(host, state, task, response)
            return

        state.backoff_attempts = 0
        state.retry_after = 0.0
        if not task.future.done():
            task.future.set_result(response)

    async def _handle_backoff(self, host: str, state: _HostState, task: _RequestTask, response: FakeResponse) -> None:
        headers = {k.lower(): v for k, v in getattr(response, "headers", {}).items()}
        retry_after_header = headers.get("retry-after") or headers.get("x-ratelimit-reset-after")
        retry_after_seconds = float(retry_after_header) if retry_after_header is not None else None
        state.backoff_attempts += 1
        calculated_backoff = min(self._max_backoff, self._base_backoff * (2 ** (state.backoff_attempts - 1)))
        delay = max(calculated_backoff, retry_after_seconds or 0)
        jitter = self._randomizer(0, self._jitter)
        delay_with_jitter = min(self._max_backoff, delay + jitter)
        retry_after_deadline = time.monotonic() + delay_with_jitter
        state.retry_after = max(state.retry_after, retry_after_deadline)
        self._metrics.record_backoff(host, task.attempt, delay_with_jitter, retry_after_seconds, getattr(response, "status", 0))

        if task.attempt >= task.max_attempts:
            if not task.future.done():
                task.future.set_exception(RateLimitExceeded(f"Max attempts exceeded for host {host}"))
            return

        # Requeue the task after waiting
        task.enqueued_at = time.monotonic() + delay_with_jitter
        asyncio.create_task(self._requeue_after_delay(state.queue, task, delay_with_jitter))

    async def _requeue_after_delay(self, queue: asyncio.Queue[_RequestTask], task: _RequestTask, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._closed:
            return
        task.enqueued_at = time.monotonic()
        await queue.put(task)
        # depth recorded when worker processes task

    async def _respect_retry_after(self, state: _HostState) -> None:
        if state.retry_after <= 0:
            return
        now = time.monotonic()
        if now < state.retry_after:
            await asyncio.sleep(state.retry_after - now)

    def _is_rate_limited(self, response: Any) -> bool:
        status = getattr(response, "status", None)
        headers = {k.lower(): v for k, v in getattr(response, "headers", {}).items()}
        secondary_header = headers.get("x-ratelimit-remaining") == "0" or headers.get("x-secondary-rate-limit")
        return status == 429 or secondary_header is not None

    async def close(self) -> None:
        self._closed = True
        for state in self._host_states.values():
            for worker in state.workers:
                worker.cancel()
            for worker in state.workers:
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    async def __aenter__(self) -> "RequestQueue":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
