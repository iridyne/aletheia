"""Process-wide inference admission, rate limiting, and observability.

All model completions run on one broker-owned asyncio loop.  Callers may live on
the Uvicorn loop, APScheduler worker loops, or ad-hoc CLI loops; crossing into a
single persistent loop gives Provider-level limits one real source of truth and
is also the prerequisite for safely reusing async HTTP clients.

The broker accepts coroutine *factories*, not already-created coroutines.  A
request rejected while the queue is full therefore cannot leak an un-awaited
coroutine, and a caller cancellation can cancel the exact rate-wait or provider
operation currently owned by the request.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from enum import IntEnum
import itertools
import logging
import math
import threading
import time
from typing import Any, TypeVar

from aletheia.model_provider.base import ModelRequest, ModelResponse, StreamChunk
from aletheia.observability.llm_metrics import record

logger = logging.getLogger("aletheia.model_provider.broker")
_DEFAULT_EXECUTOR_SHUTDOWN_TIMEOUT_SEC = 4.0

T = TypeVar("T")


class InferencePriority(IntEnum):
    """Lower values receive admission first within one Provider."""

    CRITICAL = 0
    INTERACTIVE = 10
    TRADING = 20
    OBSERVER = 40
    DEFAULT = 50
    BACKGROUND = 80
    HEALTH = 90


_CURRENT_PRIORITY: ContextVar[InferencePriority] = ContextVar(
    "aletheia_inference_priority",
    default=InferencePriority.DEFAULT,
)


@contextmanager
def inference_priority(priority: InferencePriority):
    """Apply an inference priority to every completion in the current context."""

    token = _CURRENT_PRIORITY.set(priority)
    try:
        yield
    finally:
        _CURRENT_PRIORITY.reset(token)


class InferenceBrokerError(RuntimeError):
    """Base class for honest broker admission/execution failures."""


class InferenceQueueFull(InferenceBrokerError):
    """The bounded Provider queue has no capacity for this priority."""


class InferenceTimeout(InferenceBrokerError):
    """The total queue + rate-limit + inference deadline expired."""


class InferenceBrokerClosed(InferenceBrokerError):
    """The process-wide broker is shutting down or unavailable."""


class InferenceRateLimitExceeded(InferenceBrokerError):
    """One request can never fit inside the configured Provider TPM budget."""


@dataclass(frozen=True)
class ProviderPolicy:
    max_concurrency: int = 2
    requests_per_minute: int = 60
    tokens_per_minute: int = 120_000
    queue_capacity: int = 64
    reserved_high_priority_slots: int = 8
    timeout_sec: float = 300.0
    # Tests use a short window; production always loads 60 seconds from config.
    rate_window_sec: float = 60.0

    def normalized(self) -> "ProviderPolicy":
        capacity = max(1, int(self.queue_capacity))
        return ProviderPolicy(
            max_concurrency=max(1, int(self.max_concurrency)),
            requests_per_minute=max(0, int(self.requests_per_minute)),
            tokens_per_minute=max(0, int(self.tokens_per_minute)),
            queue_capacity=capacity,
            reserved_high_priority_slots=min(
                capacity,
                max(0, int(self.reserved_high_priority_slots)),
            ),
            timeout_sec=max(0.01, float(self.timeout_sec)),
            rate_window_sec=max(0.01, float(self.rate_window_sec)),
        )


def load_provider_policy(provider_name: str) -> ProviderPolicy:
    """Load one Provider policy without exposing any credential fields."""

    from aletheia.config.loader import load_config

    cfg = load_config()
    pcfg = cfg.models.providers.get(provider_name)
    if pcfg is None:
        return ProviderPolicy()
    return ProviderPolicy(
        max_concurrency=pcfg.inference_max_concurrency,
        requests_per_minute=pcfg.inference_requests_per_minute,
        tokens_per_minute=pcfg.inference_tokens_per_minute,
        queue_capacity=pcfg.inference_queue_capacity,
        reserved_high_priority_slots=pcfg.inference_reserved_high_priority_slots,
        timeout_sec=pcfg.inference_timeout_sec,
    ).normalized()


def estimate_request_tokens(request: ModelRequest) -> int:
    """Conservative character-based reservation used before usage is known."""

    chars = len(request.system or "")
    for message in request.messages:
        chars += len(str(message.get("role") or ""))
        chars += len(str(message.get("content") or ""))
    input_estimate = max(1, math.ceil(chars / 4))
    return input_estimate + max(0, int(request.max_tokens))


def _actual_usage_tokens(value: Any) -> int:
    if not isinstance(value, ModelResponse):
        return 0
    usage = value.usage or {}
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)):
        return max(0, int(total))
    parts = (
        usage.get("prompt_tokens", usage.get("input_tokens", 0)),
        usage.get("completion_tokens", usage.get("output_tokens", 0)),
    )
    return sum(max(0, int(part)) for part in parts if isinstance(part, (int, float)))


@dataclass
class _Metrics:
    submitted: int = 0
    started: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    timed_out: int = 0
    rejected_queue_full: int = 0
    rejected_rate_limit: int = 0
    active: int = 0
    queued: int = 0
    outstanding: int = 0
    peak_active: int = 0
    peak_outstanding: int = 0
    queue_wait_count: int = 0
    queue_wait_total_ms: float = 0.0
    queue_wait_max_ms: float = 0.0
    rate_wait_count: int = 0
    rate_wait_total_ms: float = 0.0
    request_duration_count: int = 0
    request_duration_total_ms: float = 0.0
    request_duration_max_ms: float = 0.0
    reserved_tokens: int = 0
    actual_tokens: int = 0

    def snapshot(self) -> dict[str, Any]:
        result = asdict(self)
        result["queue_wait_avg_ms"] = round(
            self.queue_wait_total_ms / self.queue_wait_count, 3
        ) if self.queue_wait_count else 0.0
        result["rate_wait_avg_ms"] = round(
            self.rate_wait_total_ms / self.rate_wait_count, 3
        ) if self.rate_wait_count else 0.0
        result["request_duration_avg_ms"] = round(
            self.request_duration_total_ms / self.request_duration_count, 3
        ) if self.request_duration_count else 0.0
        for key in (
            "queue_wait_total_ms",
            "queue_wait_max_ms",
            "rate_wait_total_ms",
            "request_duration_total_ms",
            "request_duration_max_ms",
        ):
            result[key] = round(float(result[key]), 3)
        return result


@dataclass
class _Item:
    provider_name: str
    priority: InferencePriority
    sequence: int
    submitted_at: float
    deadline: float
    reserved_tokens: int
    operation_factory: Callable[[], Awaitable[Any]]
    result: asyncio.Future[Any]
    execution_task: asyncio.Task[Any] | None = None

    def cancel_execution(self) -> None:
        if self.execution_task is not None and not self.execution_task.done():
            self.execution_task.cancel()


class _SlidingWindowLimiter:
    def __init__(self, policy: ProviderPolicy) -> None:
        self.policy = policy
        self._events: deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self.policy.rate_window_sec
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    async def acquire(self, tokens: int) -> float:
        if self.policy.tokens_per_minute and tokens > self.policy.tokens_per_minute:
            raise InferenceRateLimitExceeded(
                f"request reserves {tokens} tokens but provider TPM limit is "
                f"{self.policy.tokens_per_minute}"
            )

        waited = 0.0
        while True:
            now = time.monotonic()
            self._prune(now)
            request_ok = (
                self.policy.requests_per_minute == 0
                or len(self._events) < self.policy.requests_per_minute
            )
            used_tokens = sum(event_tokens for _, event_tokens in self._events)
            token_ok = (
                self.policy.tokens_per_minute == 0
                or used_tokens + tokens <= self.policy.tokens_per_minute
            )
            if request_ok and token_ok:
                self._events.append((now, tokens))
                return waited

            if not self._events:
                # Defensive: the only impossible single-request case was handled
                # above, so a short yield is enough to avoid a busy loop.
                sleep_for = min(0.05, self.policy.rate_window_sec)
            else:
                sleep_for = max(
                    0.001,
                    self._events[0][0] + self.policy.rate_window_sec - now,
                )
            started = time.monotonic()
            await asyncio.sleep(sleep_for)
            waited += time.monotonic() - started


@dataclass
class _ProviderState:
    name: str
    policy: ProviderPolicy
    queue: asyncio.PriorityQueue[tuple[int, int, _Item]]
    limiter: _SlidingWindowLimiter
    metrics: _Metrics = field(default_factory=_Metrics)
    workers: list[asyncio.Task[None]] = field(default_factory=list)
    retiring: bool = False
    pending_policy: ProviderPolicy | None = None
    drained: asyncio.Event = field(default_factory=asyncio.Event)


class InferenceBroker:
    """Thread-safe facade over a broker-owned persistent asyncio loop."""

    def __init__(
        self,
        *,
        policy_loader: Callable[[str], ProviderPolicy] = load_provider_policy,
        thread_name: str = "aletheia-inference-broker",
    ) -> None:
        self._policy_loader = policy_loader
        self._thread_name = thread_name
        self._start_lock = threading.Lock()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._states: dict[str, _ProviderState] = {}
        self._sequence = itertools.count()
        self._closing = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._loop)

    def start(self) -> None:
        if self.running:
            return
        with self._start_lock:
            if self.running:
                return
            self._closing = False
            self._started.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()
            if not self._started.wait(timeout=5):
                raise InferenceBrokerClosed("inference broker loop did not start")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._started.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(
                # AbstractEventLoop's stub omits the Python 3.12+ concrete timeout.
                loop.shutdown_default_executor(
                    timeout=_DEFAULT_EXECUTOR_SHUTDOWN_TIMEOUT_SEC  # pyright: ignore[reportCallIssue]
                )
            )
            loop.close()
            self._loop = None

    async def _state(
        self,
        provider_name: str,
        policy: ProviderPolicy,
        *,
        deadline: float,
    ) -> _ProviderState:
        while (state := self._states.get(provider_name)) is not None:
            if self._closing:
                raise InferenceBrokerClosed("inference broker is shutting down")
            if not state.retiring:
                return state
            if state.metrics.outstanding:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InferenceTimeout(
                        f"provider {provider_name} policy reload exceeded total deadline"
                    )
                try:
                    await asyncio.wait_for(state.drained.wait(), timeout=remaining)
                except TimeoutError as exc:
                    raise InferenceTimeout(
                        f"provider {provider_name} policy reload exceeded total deadline"
                    ) from exc
                continue
            for worker in state.workers:
                worker.cancel()
            await asyncio.gather(*state.workers, return_exceptions=True)
            # Several submissions can await the same drain event. Only the first
            # one may retire this exact generation; later waiters must reuse the
            # replacement state instead of removing it.
            if self._states.get(provider_name) is not state:
                continue
            self._states.pop(provider_name, None)
            policy = state.pending_policy or policy
            break
        normalized = policy.normalized()
        state = _ProviderState(
            name=provider_name,
            policy=normalized,
            queue=asyncio.PriorityQueue(maxsize=normalized.queue_capacity),
            limiter=_SlidingWindowLimiter(normalized),
        )
        state.drained.set()
        self._states[provider_name] = state
        state.workers = [
            asyncio.create_task(
                self._provider_worker(state, index),
                name=f"inference:{provider_name}:{index}",
            )
            for index in range(normalized.max_concurrency)
        ]
        return state

    @staticmethod
    def _has_capacity(state: _ProviderState, priority: InferencePriority) -> bool:
        metrics = state.metrics
        capacity = state.policy.queue_capacity
        if metrics.outstanding >= capacity:
            return False
        low_priority_limit = capacity - state.policy.reserved_high_priority_slots
        if priority > InferencePriority.TRADING and metrics.outstanding >= low_priority_limit:
            return False
        return True

    async def submit(
        self,
        provider_name: str,
        operation_factory: Callable[[], Awaitable[T]],
        *,
        reserved_tokens: int = 0,
        priority: InferencePriority | None = None,
        timeout_sec: float | None = None,
        policy: ProviderPolicy | None = None,
    ) -> T:
        """Submit from any caller loop and await one broker-owned operation."""

        self.start()
        if self._closing:
            raise InferenceBrokerClosed("inference broker is shutting down")
        loop = self._loop
        if loop is None:
            raise InferenceBrokerClosed("inference broker loop unavailable")
        resolved_policy = (policy or self._policy_loader(provider_name)).normalized()
        # CRITICAL has the numeric value 0, so truthiness would accidentally
        # replace an explicit critical request with the ambient/default lane.
        resolved_priority = priority if priority is not None else _CURRENT_PRIORITY.get()
        resolved_timeout = timeout_sec or resolved_policy.timeout_sec
        concurrent_future = asyncio.run_coroutine_threadsafe(
            self._submit_in_loop(
                provider_name,
                operation_factory,
                reserved_tokens=max(0, int(reserved_tokens)),
                priority=resolved_priority,
                timeout_sec=max(0.01, float(resolved_timeout)),
                policy=resolved_policy,
            ),
            loop,
        )
        try:
            return await asyncio.wrap_future(concurrent_future)
        except asyncio.CancelledError:
            concurrent_future.cancel()
            raise

    async def _submit_in_loop(
        self,
        provider_name: str,
        operation_factory: Callable[[], Awaitable[T]],
        *,
        reserved_tokens: int,
        priority: InferencePriority,
        timeout_sec: float,
        policy: ProviderPolicy,
    ) -> T:
        if self._closing:
            raise InferenceBrokerClosed("inference broker is shutting down")
        submitted_at = time.monotonic()
        state = await self._state(
            provider_name,
            policy,
            deadline=submitted_at + timeout_sec,
        )
        metrics = state.metrics
        if not self._has_capacity(state, priority):
            metrics.rejected_queue_full += 1
            raise InferenceQueueFull(
                f"provider {provider_name} inference queue is full "
                f"({metrics.outstanding}/{state.policy.queue_capacity})"
            )

        result: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        item = _Item(
            provider_name=provider_name,
            priority=priority,
            sequence=next(self._sequence),
            submitted_at=submitted_at,
            deadline=submitted_at + timeout_sec,
            reserved_tokens=reserved_tokens,
            operation_factory=operation_factory,
            result=result,
        )
        metrics.submitted += 1
        metrics.queued += 1
        metrics.outstanding += 1
        state.drained.clear()
        metrics.reserved_tokens += reserved_tokens
        metrics.peak_outstanding = max(metrics.peak_outstanding, metrics.outstanding)
        state.queue.put_nowait((int(priority), item.sequence, item))

        try:
            async with asyncio.timeout_at(item.deadline):
                return await result
        except TimeoutError as exc:
            metrics.timed_out += 1
            item.cancel_execution()
            if not result.done():
                result.cancel()
            raise InferenceTimeout(
                f"provider {provider_name} inference exceeded {timeout_sec:.3f}s total deadline"
            ) from exc
        except asyncio.CancelledError:
            metrics.cancelled += 1
            item.cancel_execution()
            if not result.done():
                result.cancel()
            raise

    async def _provider_worker(self, state: _ProviderState, _index: int) -> None:
        while True:
            _priority, _sequence, item = await state.queue.get()
            metrics = state.metrics
            metrics.queued = max(0, metrics.queued - 1)
            if item.result.cancelled():
                metrics.outstanding = max(0, metrics.outstanding - 1)
                if not metrics.outstanding:
                    state.drained.set()
                state.queue.task_done()
                continue

            queue_wait_ms = (time.monotonic() - item.submitted_at) * 1000
            metrics.queue_wait_count += 1
            metrics.queue_wait_total_ms += queue_wait_ms
            metrics.queue_wait_max_ms = max(metrics.queue_wait_max_ms, queue_wait_ms)
            metrics.started += 1
            metrics.active += 1
            metrics.peak_active = max(metrics.peak_active, metrics.active)
            started = time.monotonic()
            try:
                remaining = item.deadline - time.monotonic()
                if remaining <= 0:
                    raise InferenceTimeout("deadline expired before rate-limit admission")

                rate_task = asyncio.create_task(state.limiter.acquire(item.reserved_tokens))
                item.execution_task = rate_task
                try:
                    rate_wait = await asyncio.wait_for(rate_task, timeout=remaining)
                except TimeoutError as exc:
                    raise InferenceTimeout("deadline expired during rate-limit wait") from exc
                if rate_wait > 0:
                    metrics.rate_wait_count += 1
                    metrics.rate_wait_total_ms += rate_wait * 1000

                remaining = item.deadline - time.monotonic()
                if remaining <= 0:
                    raise InferenceTimeout("deadline expired before provider execution")
                operation_task = asyncio.create_task(item.operation_factory())
                item.execution_task = operation_task
                try:
                    value = await asyncio.wait_for(operation_task, timeout=remaining)
                except TimeoutError as exc:
                    raise InferenceTimeout("provider execution exceeded total deadline") from exc
                metrics.completed += 1
                metrics.actual_tokens += _actual_usage_tokens(value)
                if not item.result.done():
                    item.result.set_result(value)
            except InferenceRateLimitExceeded as exc:
                metrics.rejected_rate_limit += 1
                metrics.failed += 1
                if not item.result.done():
                    item.result.set_exception(exc)
            except InferenceTimeout as exc:
                metrics.timed_out += 1
                if not item.result.done():
                    item.result.set_exception(exc)
            except asyncio.CancelledError:
                if self._closing:
                    if not item.result.done():
                        item.result.set_exception(InferenceBrokerClosed("broker shutdown"))
                    raise
                if not item.result.done():
                    metrics.cancelled += 1
                    item.result.cancel()
            except BaseException as exc:  # noqa: BLE001 - preserve provider exception semantics
                metrics.failed += 1
                if not item.result.done():
                    item.result.set_exception(exc)
            finally:
                duration_ms = (time.monotonic() - started) * 1000
                metrics.request_duration_count += 1
                metrics.request_duration_total_ms += duration_ms
                metrics.request_duration_max_ms = max(
                    metrics.request_duration_max_ms,
                    duration_ms,
                )
                metrics.active = max(0, metrics.active - 1)
                metrics.outstanding = max(0, metrics.outstanding - 1)
                if not metrics.outstanding:
                    state.drained.set()
                item.execution_task = None
                state.queue.task_done()

    async def snapshot(self) -> dict[str, Any]:
        if not self.running:
            return {"running": False, "providers": {}}
        loop = self._loop
        if loop is None:
            return {"running": False, "providers": {}}
        if asyncio.get_running_loop() is loop:
            return self._snapshot_in_loop()
        future = asyncio.run_coroutine_threadsafe(self._snapshot_coro(), loop)
        return await asyncio.wrap_future(future)

    async def run_auxiliary(self, operation_factory: Callable[[], Awaitable[T]]) -> T:
        """Run non-inference Provider maintenance on the broker-owned loop."""

        self.start()
        if self._closing:
            raise InferenceBrokerClosed("inference broker is shutting down")
        loop = self._loop
        if loop is None:
            raise InferenceBrokerClosed("inference broker loop unavailable")
        if asyncio.get_running_loop() is loop:
            return await operation_factory()
        future = asyncio.run_coroutine_threadsafe(operation_factory(), loop)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def _snapshot_coro(self) -> dict[str, Any]:
        return self._snapshot_in_loop()

    def _snapshot_in_loop(self) -> dict[str, Any]:
        providers = {}
        for name, state in sorted(self._states.items()):
            providers[name] = {
                "status": "reloading" if state.retiring else "running",
                "policy": asdict(state.policy),
                "metrics": state.metrics.snapshot(),
            }
        return {"running": True, "providers": providers}

    async def invalidate_provider(self, provider_name: str) -> None:
        """Apply changed policy on the next request after current work drains."""

        if not self.running:
            return
        loop = self._loop
        if loop is None:
            return
        pending_policy = self._policy_loader(provider_name).normalized()
        future = asyncio.run_coroutine_threadsafe(
            self._invalidate_provider_in_loop(provider_name, pending_policy),
            loop,
        )
        await asyncio.wrap_future(future)

    async def _invalidate_provider_in_loop(
        self,
        provider_name: str,
        pending_policy: ProviderPolicy,
    ) -> None:
        state = self._states.get(provider_name)
        if state is None:
            return
        state.retiring = True
        state.pending_policy = pending_policy
        if state.metrics.outstanding:
            # Preserve admitted work while the old queue drains. New submissions
            # wait outside that queue, and the first waiter atomically installs
            # the pending policy.
            logger.info(
                "draining inference policy for %s: %d outstanding",
                provider_name,
                state.metrics.outstanding,
            )
            return
        for worker in state.workers:
            worker.cancel()
        await asyncio.gather(*state.workers, return_exceptions=True)
        if self._states.get(provider_name) is state:
            self._states.pop(provider_name, None)

    async def aclose(self) -> None:
        if not self.running:
            return
        self._closing = True
        loop = self._loop
        thread = self._thread
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown_in_loop(), loop)
        await asyncio.wrap_future(future)
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            await asyncio.to_thread(thread.join, 5)
        # Keep ownership if shutdown is still unwinding.  Dropping this
        # reference would let a later start create a second broker thread.
        if thread is None or not thread.is_alive():
            self._thread = None

    async def _shutdown_in_loop(self) -> None:
        for state in self._states.values():
            state.drained.set()
            while not state.queue.empty():
                try:
                    _priority, _sequence, item = state.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not item.result.done():
                    item.result.set_exception(InferenceBrokerClosed("broker shutdown"))
                state.queue.task_done()
            for worker in state.workers:
                worker.cancel()
        await asyncio.gather(
            *(worker for state in self._states.values() for worker in state.workers),
            return_exceptions=True,
        )
        self._states.clear()


inference_broker = InferenceBroker()


async def brokered_complete(
    provider_name: str,
    provider: Any,
    request: ModelRequest,
    *,
    priority: InferencePriority | None = None,
    timeout_sec: float | None = None,
) -> ModelResponse:
    # Observability: count every real completion attempt per provider/model.
    # provider.model is the exact model being requested (including cloned
    # fallback models set by complete_with_fallback), so the key matches the
    # "provider_name/model" identifiers already used in Aletheia's logs.
    provider_id = f"{provider_name}/{getattr(provider, 'model', '') or 'unknown'}"
    try:
        response = await inference_broker.submit(
            provider_name,
            lambda: provider.complete(request),
            reserved_tokens=estimate_request_tokens(request),
            priority=priority,
            timeout_sec=timeout_sec,
        )
    except BaseException:
        # Record the failed attempt, then re-raise so callers (fallback,
        # cooldown, startup gate) keep their original error semantics.
        record(provider_id, False)
        raise
    record(provider_id, True)
    return response


# Caps in-flight stream chunks per brokered stream. 256 is far above a normal
# consumer's lag while still bounding worst-case memory per connection.
_STREAM_QUEUE_MAXSIZE = 256
# A consumer that has not drained a single chunk for this long is treated as
# gone; the producer errors out instead of blocking a broker slot forever.
_STREAM_PUT_TIMEOUT_SEC = 120.0


async def brokered_stream(
    provider_name: str,
    provider: Any,
    request: ModelRequest,
    *,
    priority: InferencePriority | None = None,
    timeout_sec: float | None = None,
    broker: InferenceBroker | None = None,
) -> AsyncIterator[StreamChunk]:
    """Stream chunks while retaining one broker admission until stream close."""

    target = broker or inference_broker
    caller_loop = asyncio.get_running_loop()
    # Previously an unbounded queue fed by put_nowait: a slow SSE consumer let
    # the entire LLM response accumulate in memory with nothing to push back.
    # _consume runs on the BROKER's loop/thread (submit() dispatches it via
    # run_coroutine_threadsafe), so awaiting a cross-loop put blocks the
    # producer rather than the consumer -- genuine backpressure, and no
    # self-deadlock because the two loops are distinct.
    chunks: asyncio.Queue[StreamChunk | object] = asyncio.Queue(
        maxsize=_STREAM_QUEUE_MAXSIZE
    )
    sentinel = object()

    async def _consume() -> None:
        async for chunk in provider.stream(request):
            await asyncio.wait_for(
                asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(chunks.put(chunk), caller_loop)
                ),
                timeout=_STREAM_PUT_TIMEOUT_SEC,
            )

    task = asyncio.create_task(
        target.submit(
            provider_name,
            _consume,
            reserved_tokens=estimate_request_tokens(request),
            priority=priority,
            timeout_sec=timeout_sec,
        )
    )

    def _signal_end(_task: "asyncio.Task") -> None:
        """Deliver the terminator even when the queue is saturated.

        Now that the queue is bounded, put_nowait can raise QueueFull -- and a
        dropped sentinel would hang the consumer loop below forever. This
        callback runs on caller_loop (the task was created there), so falling
        back to a scheduled awaitable put is safe.

        Args:
            _task: The finished broker task (unused; only completion matters).
        """
        try:
            chunks.put_nowait(sentinel)
        except asyncio.QueueFull:
            caller_loop.create_task(chunks.put(sentinel))

    task.add_done_callback(_signal_end)
    try:
        while True:
            item = await chunks.get()
            if item is sentinel:
                break
            yield item  # type: ignore[misc]
        await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


def priority_for_agent(agent_name: str, trigger_type: str) -> InferencePriority:
    if trigger_type == "manual":
        return InferencePriority.INTERACTIVE
    if agent_name in {
        "paper_trader",
        "pi_trading_decision",
        "position_manager",
    }:
        return InferencePriority.TRADING
    if agent_name in {"market_observer", "equity_observer", "prediction_observer"}:
        return InferencePriority.OBSERVER
    if agent_name in {
        "dreaming_agent",
        "dreaming_daily",
        "post_trade_reviewer",
        "risk_critic",
        "insight_synthesizer",
        "research_agent",
    }:
        return InferencePriority.BACKGROUND
    return InferencePriority.DEFAULT
