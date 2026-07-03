"""Spec: Gateway stability — bound LLM-driven jobs so they cannot leak threads,
and recover transient bad-JSON from the LLM provider under contention.

Background (2026-06-28 incident): market_observer makes one LLM call per instrument;
under provider contention each call can run to the 60s provider timeout, so a large
universe blows the 240s job timeout → _run_async_with_timeout cancels the coroutine
mid-flight → the daemon thread is abandoned and LEAKS, starving the frontdoor.
Two fixes, locked here:
  GS-S01  market_observer caps the per-instrument loop wall-clock BELOW the job timeout.
  GS-B01  llm_structured retries ONCE on a parse failure (recovers prose-prefixed JSON).
  GS-B02  llm_structured does NOT retry on empty content (reasoning-budget exhaustion).

Claims attach to gateway_stability_contract.

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_MARKET_OBS = _ROOT / "aletheia" / "agents" / "market_observer.py"

_CONTRACT = "gateway_stability_contract"


# ---------------------------------------------------------------------------
# GS-S01 — market_observer loop wall-clock budget < job timeout
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-S01", _CONTRACT, "§wall-clock budget", "L2a",
        "market_observer caps its per-instrument loop wall-clock strictly below the "
        "scheduler job timeout, and breaks out when the budget is hit — so the "
        "coroutine returns before cancellation and the daemon thread is not abandoned.",
    ),
)
def test_market_observer_loop_has_wallclock_budget():
    from aletheia.agents import market_observer as mo
    assert hasattr(mo, "_MARKET_OBSERVER_LOOP_BUDGET_SEC"), "loop budget constant missing"
    assert hasattr(mo, "_MARKET_OBSERVER_JOB_TIMEOUT_S"), "job-timeout hint constant missing"
    assert mo._MARKET_OBSERVER_LOOP_BUDGET_SEC < mo._MARKET_OBSERVER_JOB_TIMEOUT_S, (
        "loop budget must be strictly below the job timeout to avoid mid-flight cancel"
    )
    src = _MARKET_OBS.read_text(encoding="utf-8")
    assert "_loop_deadline" in src and "loop_budget_exceeded" in src, (
        "per-instrument loop must check a wall-clock deadline and break with a logged reason"
    )
    # The job-timeout hint must match the scheduler's actual configured value.
    from aletheia.agent_runtime.scheduler import _ASYNC_JOB_TIMEOUT_SEC
    assert mo._MARKET_OBSERVER_JOB_TIMEOUT_S == _ASYNC_JOB_TIMEOUT_SEC["market_observer"], (
        "job-timeout hint drifted from scheduler _ASYNC_JOB_TIMEOUT_SEC['market_observer']"
    )


# ---------------------------------------------------------------------------
# GS-S02 — market_observer has a module logger (the budget branch logs a warning)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-S02", _CONTRACT, "§wall-clock budget", "L2a",
        "market_observer defines a module-level `logger`. The budget-exceeded branch "
        "calls logger.warning(); without a module logger that branch NameErrors at "
        "runtime (only when the budget is hit — invisible to non-executing tests).",
    ),
)
def test_market_observer_has_module_logger():
    import logging
    from aletheia.agents import market_observer as mo
    assert isinstance(getattr(mo, "logger", None), logging.Logger), (
        "market_observer must define a module-level logging.Logger named 'logger'"
    )


# ---------------------------------------------------------------------------
# Fake LLM plumbing for behavioral tests (mock only the network boundary)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, content, completion_tokens=10):
        self.content = content
        self.raw = {"usage": {"completion_tokens": completion_tokens}}


class _Schema:
    @staticmethod
    def model_json_schema():
        return {"type": "object", "properties": {"x": {"type": "integer"}}}


def _patch_llm(monkeypatch_obj, responses):
    """Patch helpers.complete_with_fallback to yield queued responses in order."""
    from aletheia.agents import helpers
    calls = {"n": 0}

    async def fake_complete(req, provider=None):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    monkeypatch_obj.setattr(helpers, "complete_with_fallback", fake_complete)
    monkeypatch_obj.setattr(helpers, "get_provider", lambda provider_name=None: object())
    monkeypatch_obj.setattr(helpers, "_record_llm", lambda ok: None)
    return calls


# ---------------------------------------------------------------------------
# GS-B01 — one corrective retry recovers a transient parse failure
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B01", _CONTRACT, "§structured retry", "L2b",
        "When the first LLM reply is non-JSON prose but the retry returns valid JSON, "
        "llm_structured recovers and returns the parsed dict (not a parse error).",
    ),
)
def test_llm_structured_retries_and_recovers(monkeypatch):
    from aletheia.agents import helpers
    calls = _patch_llm(monkeypatch, [
        _Resp("Sure! Here is the analysis you asked for, no JSON though."),  # attempt 0: prose
        _Resp('{"x": 7}'),                                                   # attempt 1: valid
    ])
    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))
    assert err is None, f"should recover after retry, got error: {err}"
    assert data == {"x": 7}, f"should return parsed dict, got {data}"
    assert calls["n"] == 2, "should have retried exactly once"


# ---------------------------------------------------------------------------
# GS-B02 — empty content is NOT retried (retry can't help, only adds load)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B02", _CONTRACT, "§structured retry", "L2b",
        "Empty content with completion_tokens>0 (reasoning-budget exhaustion) returns "
        "structured_empty_content immediately WITHOUT a retry — retry cannot help and "
        "would only add provider load under contention.",
    ),
)
def test_llm_structured_does_not_retry_empty(monkeypatch):
    from aletheia.agents import helpers
    calls = _patch_llm(monkeypatch, [
        _Resp("", completion_tokens=42),  # empty but tokens spent → reasoning exhaustion
        _Resp('{"x": 1}'),                # would succeed IF retried — must NOT be reached
    ])
    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))
    assert data is None
    assert err == "structured_empty_content", f"unexpected err: {err}"
    assert calls["n"] == 1, "empty content must NOT trigger a retry"


# ---------------------------------------------------------------------------
# GS-B03 — two consecutive parse failures surface a parse error (bounded retry)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B03", _CONTRACT, "§structured retry", "L2b",
        "Retry is bounded at one: two consecutive non-JSON replies return a "
        "structured_parse_error (no infinite retry loop).",
    ),
)
def test_llm_structured_retry_is_bounded(monkeypatch):
    from aletheia.agents import helpers
    calls = _patch_llm(monkeypatch, [
        _Resp("no json here"),
        _Resp("still no json"),
        _Resp('{"x": 9}'),  # must NOT be reached — retry is bounded at 2 attempts
    ])
    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))
    assert data is None
    assert err is not None and "structured_parse_error" in err
    assert calls["n"] == 2, "must attempt at most twice (one retry)"


# ---------------------------------------------------------------------------
# GS-S03 — data_collectors fans out under a wall-clock budget < its job timeout
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-S03", _CONTRACT, "§data_collectors budget", "L2a",
        "data_collectors bounds its collector fan-out: a wall-clock budget strictly "
        "below the 45s job timeout, via _bounded() per-collector timeouts — so one "
        "stalled collector can't blow the job and abandon a thread (every-2min job).",
    ),
)
def test_data_collectors_has_bounded_fanout():
    from aletheia.agent_runtime import scheduler as sch
    assert hasattr(sch, "_DC_DEADLINE_S") and hasattr(sch, "_bounded"), (
        "data_collectors must expose _DC_DEADLINE_S budget + _bounded() wrapper"
    )
    assert sch._DC_DEADLINE_S < sch._ASYNC_JOB_TIMEOUT_SEC["data_collectors"], (
        "wall-clock budget must be below the job timeout to avoid mid-flight cancel"
    )
    src = Path(sch.__file__).read_text(encoding="utf-8")
    assert "await micro.collect(symbols)" not in src, "microstructure collect must be _bounded"
    assert "await deriv.collect(symbols)" not in src, "derivatives collect must be _bounded"


# ---------------------------------------------------------------------------
# GS-B04 — _bounded caps a hanging collector and never hangs the tick
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B04", _CONTRACT, "§data_collectors budget", "L2b",
        "_bounded returns a fast collector's result, caps a hanging one at its timeout "
        "(returns None, does NOT hang), and skips when the wall-clock deadline passed.",
    ),
)
def test_bounded_caps_hanging_collector():
    import time
    from aletheia.agent_runtime.scheduler import _bounded

    async def fast():
        return {"ok": 1}

    async def hang():
        # Block on an un-set Event (not asyncio.sleep — conftest no-ops long sleeps);
        # wait_for must cancel this at the cap.
        await asyncio.Event().wait()
        return {"never": 1}

    async def scenario():
        dl = time.monotonic() + 40
        r_fast = await _bounded(fast(), "fast", 5.0, dl)
        t0 = time.monotonic()
        r_hang = await _bounded(hang(), "hang", 1.0, dl)
        elapsed = time.monotonic() - t0
        r_skip = await _bounded(fast(), "skip", 5.0, time.monotonic() - 1)  # deadline passed
        return r_fast, r_hang, elapsed, r_skip

    r_fast, r_hang, elapsed, r_skip = asyncio.run(scenario())
    assert r_fast == {"ok": 1}, "fast collector result must pass through"
    assert r_hang is None, "hanging collector must be capped to None"
    assert elapsed < 3.0, f"hang must be cut at ~1s cap, took {elapsed:.1f}s"
    assert r_skip is None, "past-deadline collector must be skipped"
