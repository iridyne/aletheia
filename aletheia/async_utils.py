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

⚠️ FUTURE CONSTRAINT — read before enabling a CLI provider:
SelectorEventLoop on Windows does NOT support asyncio subprocess. If
claude_cli or codex_cli providers are ever re-enabled (both are
enabled=false in config.yaml today), their asyncio.create_subprocess_exec()
calls will raise NotImplementedError. The 8 call sites live in
model_provider/{claude_cli,codex_cli,codex_auth}.py and must first be
rewritten to run subprocess work in a thread via synchronous subprocess.run.
"""

import sys


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
