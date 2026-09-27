"""Windows asyncio event loop policy — process-level selector enforcement.

On Windows the default ProactorEventLoop causes two same-root failure classes,
both tracked for days in docs/archive/legacy/issues/incidents/RUNTIME_BEHAVIOR_ISSUES.md:

  §17  Bare asyncio.run() teardown hangs up to 255s in the IOCP drain (broker
       threads, exchange_sync, tpsl checks, backfill jobs). Executor threads get
       abandoned and accumulate, starving the scheduler.
  §23  uvicorn's AcceptEx overlapped operations produce
       "OSError: [WinError 64] The specified network name is no longer available"
       after ~60min, crashing the front door (and, §24, the backend).

SelectorEventLoop is based on select() — no IOCP — so both paths disappear.

Safe for every provider currently in use: aiohttp / httpx / ccxt and the
anthropic_compatible HTTP provider are all pure socket I/O, fully compatible
with SelectorEventLoop. (net.py already uses aiohttp's ThreadedResolver, which
is the correct Windows-Selector DNS approach.)

"""

import asyncio
import sys
import threading


# One reusable loop per calling thread (see `run_coro_from_sync`). Loops stay
# registered for the process lifetime so a later call from the same thread finds
# the same loop instead of a fresh one. Ceiling: a process that churns many
# short-lived bridge threads accumulates one idle loop per thread it has used —
# bounded by that churn, and never freed, which is the price of keeping a
# loop-bound adapter valid across calls.
_THREAD_LOOPS: dict[int, asyncio.AbstractEventLoop] = {}
_BRIDGE_LOOPS: dict[int, asyncio.AbstractEventLoop] = {}
_LOOP_LOCK = threading.Lock()


async def _cancel_leftover_tasks() -> None:
    """Tear down what a completed bridge call left behind.

    ``asyncio.run`` used to close its loop after every call, which cancelled any
    task the coroutine had leaked. Reusing one loop means that teardown must be
    done explicitly, or a leaked retry task would keep running into the next call.
    """
    current = asyncio.current_task()
    leftovers = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    for task in leftovers:
        task.cancel()
    if leftovers:
        await asyncio.gather(*leftovers, return_exceptions=True)


def _caller_thread_loop() -> asyncio.AbstractEventLoop:
    """The reusable loop owned by the calling thread."""
    with _LOOP_LOCK:
        loop = _THREAD_LOOPS.get(threading.get_ident())
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _THREAD_LOOPS[threading.get_ident()] = loop
        return loop


def _bridge_loop() -> asyncio.AbstractEventLoop:
    """The reusable loop that drives work for a caller thread already running one.

    The caller's loop cannot run this coroutine without re-entrancy, so a
    dedicated loop thread runs it — but the same one on every call, because a
    loop-bound adapter (``CCXTUTAAdapter.connect`` raises
    ``cross_loop_adapter_reuse``) must not be moved between loops.
    """
    with _LOOP_LOCK:
        loop = _BRIDGE_LOOPS.get(threading.get_ident())
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever,
                name="aletheia-sync-bridge",
                daemon=True,
            ).start()
            _BRIDGE_LOOPS[threading.get_ident()] = loop
        return loop


def run_coro_from_sync(coro, *, timeout: float | None = None):
    """Run a coroutine from synchronous code, even inside a running event loop.

    The event loop is reused across calls from the same calling thread: adapters,
    aiohttp sessions and other loop-bound objects created by one call must remain
    valid in the next one. ``asyncio.run`` — which closes its loop at the end of
    every call — silently invalidated them (``cross_loop_adapter_reuse``).

    Callers with no running loop keep executing on their own thread; callers with
    one are driven by a per-thread bridge loop thread. Leftover tasks from a
    completed call are cancelled before the next call starts.

    A coroutine already running on the bridge loop must not call this function —
    the same loop cannot be driven from its own thread. No current caller does
    (bridged coroutines await, they never re-enter the bridge).

    The loop is not closed per call, so async generators a call abandons are
    finalized at garbage collection rather than at the end of the call; task
    teardown (above) is what remains per call.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running.is_running():
        bridge = _bridge_loop()
        asyncio.run_coroutine_threadsafe(_cancel_leftover_tasks(), bridge).result(
            timeout=timeout
        )
        future = asyncio.run_coroutine_threadsafe(coro, bridge)
        return future.result(timeout=timeout)
    loop = _caller_thread_loop()
    loop.run_until_complete(_cancel_leftover_tasks())
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(_cancel_leftover_tasks())


def apply_windows_selector_policy() -> None:
    """Set the process-wide WindowsSelectorEventLoopPolicy.

    Idempotent; a no-op on non-Windows platforms. MUST be called before any
    asyncio.run() call or event loop creation in the process — so callers
    invoke it at module-import time of each process entry point, before
    anything touches asyncio.
    """
    if sys.platform != "win32":
        return
    import asyncio

    if not isinstance(
        asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
    ):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
