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
