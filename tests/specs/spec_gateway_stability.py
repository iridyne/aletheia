"""Spec: Gateway stability — bound LLM-driven jobs so they cannot leak threads,
and recover transient bad-JSON from the LLM provider under contention.

Background (2026-06-28 incident): market_observer makes one LLM call per instrument;
under provider contention each call can run to the 60s provider timeout, so a large
universe blows the 240s job timeout → _run_async_with_timeout cancels the coroutine
mid-flight → the daemon thread is abandoned and LEAKS, starving the frontdoor.
Two fixes, locked here:
  GS-S01  market_observer uses one run deadline, including prefetch, below job timeout.
  GS-B01  llm_structured retries ONCE on a parse failure (recovers prose-prefixed JSON).
  GS-B02  llm_structured does NOT retry on empty content (reasoning-budget exhaustion).

Claims attach to gateway_stability_contract.

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_MARKET_OBS = _ROOT / "aletheia" / "agents" / "market_observer.py"

_CONTRACT = "gateway_stability_contract"


# ---------------------------------------------------------------------------
# GS-S01 — one market_observer run budget (including prefetch) < job timeout
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-S01", _CONTRACT, "§wall-clock budget", "L2a",
        "market_observer uses one wall-clock deadline across prefetch and the serial "
        "instrument loop, strictly below the scheduler timeout; prefetch may consume "
        "only the remaining budget so teardown keeps an explicit safety margin.",
    ),
)
def test_market_observer_loop_has_wallclock_budget():
    from aletheia.agents import market_observer as mo
    assert hasattr(mo, "_MARKET_OBSERVER_LOOP_BUDGET_SEC"), "loop budget constant missing"
    assert hasattr(mo, "_MARKET_OBSERVER_JOB_TIMEOUT_S"), "job-timeout hint constant missing"
    assert mo._MARKET_OBSERVER_LOOP_BUDGET_SEC < mo._MARKET_OBSERVER_JOB_TIMEOUT_S, (
        "loop budget must be strictly below the job timeout to avoid mid-flight cancel"
    )
    assert (
        mo._MARKET_OBSERVER_JOB_TIMEOUT_S
        - mo._MARKET_OBSERVER_LOOP_BUDGET_SEC
        == mo._MARKET_OBSERVER_TEARDOWN_MARGIN_SEC
    ), "observer budget must reserve its declared teardown margin"
    src = _MARKET_OBS.read_text(encoding="utf-8")
    assert "_run_deadline" in src and "loop_budget_exceeded" in src, (
        "the observer must check one run deadline and break with a logged reason"
    )
    assert "_gather_prefetch_with_deadline" in src and "asyncio.wait_for" in src, (
        "parallel prefetch must be bounded by the remaining run deadline"
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


@spec_claim(
    SpecClaim(
        "GS-S03", _CONTRACT, "§bounded-loop fairness", "L2b",
        "Signal priority keeps the durable next instrument as a mandatory prefix. "
        "After a bounded run, the cursor advances only through the completed canonical "
        "prefix, so a skipped low-score instrument is boundedly reachable.",
    ),
)
def test_market_observer_budget_rotation_is_durable_and_cancellation_safe():
    from aletheia.agents.round_robin import DurableRoundRobin, rotate_from_next

    assert rotate_from_next(["a", "b", "c"], next_key="b", key=str) == ["b", "c", "a"]
    source = _MARKET_OBS.read_text(encoding="utf-8")
    assert "DurableRoundRobin" in source
    assert "_fair_signal_order" in source
    assert "_advance_completed_round_robin_prefix" in source
    assert "completed_attempt" in source
    assert "if completed_attempt:" in source


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


class _DefaultsSchema:
    @staticmethod
    def model_json_schema():
        return {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "items": {"type": "array"},
                "metadata": {"type": "object"},
                "note": {"type": ["string", "null"]},
                "threshold": {"type": "integer", "default": 7},
            },
        }


def _patch_llm(monkeypatch_obj, responses):
    """Patch helpers.complete_with_fallback to yield queued responses in order."""
    from aletheia.agents import helpers
    calls = {"n": 0}

    # helpers now forwards provider_name= too (#161 fix) — accept and ignore.
    async def fake_complete(req, provider=None, provider_name=None):
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
# GS-B02 — empty content with reasoning-budget exhaustion IS retried with more tokens
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B02", _CONTRACT, "§structured retry", "L2b",
        "Empty content with completion_tokens>0 (reasoning-budget exhaustion) is "
        "retried with an increased token budget — reasoning models often exhaust the "
        "budget on the thinking phase and produce empty text; more tokens lets them "
        "complete the output. Still bounded at max_attempts to avoid runaway load.",
    ),
)
def test_llm_structured_retries_empty_with_more_tokens(monkeypatch):
    from aletheia.agents import helpers
    calls = _patch_llm(monkeypatch, [
        _Resp("", completion_tokens=42),   # attempt 0: empty, reasoning exhausted
        _Resp("", completion_tokens=100),  # attempt 1: still empty with more budget
        _Resp('{"x": 1}'),                 # attempt 2: succeeds with 2x budget
    ])
    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))
    assert data == {"x": 1}, f"should recover after retry with more tokens, got err: {err}"
    assert err is None
    assert calls["n"] == 3, "should retry twice (3 total attempts) for empty+reasoning"


# ---------------------------------------------------------------------------
# GS-B03 — two consecutive parse failures surface a parse error (bounded retry)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B03", _CONTRACT, "§structured retry", "L2b",
        "Retry is bounded at two retries (3 total attempts): three consecutive non-JSON "
        "replies return a structured_parse_error (no infinite retry loop).",
    ),
)
def test_llm_structured_retry_is_bounded(monkeypatch):
    from aletheia.agents import helpers
    calls = _patch_llm(monkeypatch, [
        _Resp("no json here"),
        _Resp("still no json"),
        _Resp("nope still not json"),
        _Resp('{"x": 9}'),  # must NOT be reached — retry is bounded at 3 attempts
    ])
    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))
    assert data is None
    assert err is not None and "structured_parse_error" in err
    assert calls["n"] == 3, "must attempt at most 3 times (two retries)"


# ---------------------------------------------------------------------------
# GS-B06 — repair-mode: third attempt sends back the previous bad output
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B06", _CONTRACT, "§structured retry", "L2b",
        "The third (final) retry uses 'repair mode': it sends the model's previous "
        "bad output back and asks to fix it, rather than re-generating from scratch. "
        "This recovers from malformed JSON that the model would otherwise keep repeating.",
    ),
)
def test_llm_structured_repair_mode_on_final_attempt(monkeypatch):
    from aletheia.agents import helpers
    captured_messages = []

    async def fake_complete(req, provider=None, provider_name=None):
        captured_messages.append(req.messages)
        # First two: bad JSON; third: valid (after seeing repair prompt)
        if len(captured_messages) < 3:
            return _Resp("not json at all")
        return _Resp('{"x": 42}')

    monkeypatch.setattr(helpers, "complete_with_fallback", fake_complete)
    monkeypatch.setattr(helpers, "get_provider", lambda provider_name=None: object())
    monkeypatch.setattr(helpers, "_record_llm", lambda ok: None)

    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))
    assert err is None, f"should recover on third attempt, got: {err}"
    assert data == {"x": 42}
    assert len(captured_messages) == 3, "should have made 3 attempts"
    # Third attempt messages should contain "FIX THIS JSON" (repair mode)
    last_msg = captured_messages[2][-1]["content"]
    assert "FIX THIS JSON" in last_msg, (
        "third attempt should use repair-mode prompt with 'FIX THIS JSON'"
    )
    assert "not json at all" in last_msg, (
        "repair-mode prompt must include the previous malformed output"
    )


# ---------------------------------------------------------------------------
# GS-B07 — fallback_to_defaults returns schema defaults on total failure
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-B07", _CONTRACT, "§structured retry", "L2b",
        "When fallback_to_defaults=True and all retries fail, llm_structured returns "
        "a dict filled with schema-appropriate defaults (None/0/[]/{}) instead of None. "
        "Callers use this for non-critical paths where 'something' beats 'nothing'.",
    ),
)
def test_llm_structured_fallback_to_defaults(monkeypatch):
    from aletheia.agents import helpers
    _patch_llm(monkeypatch, [
        _Resp("bad"),
        _Resp("worse"),
        _Resp("terrible"),
    ])
    # Without fallback — returns None
    data_none, err_none = asyncio.run(
        helpers.llm_structured("sys", "user", schema=_DefaultsSchema, fallback_to_defaults=False)
    )
    assert data_none is None
    assert err_none is not None
    assert "fallback" not in err_none

    # With fallback — returns default dict, error string marked with (fallback)
    data_def, err_def = asyncio.run(
        helpers.llm_structured("sys", "user", schema=_DefaultsSchema, fallback_to_defaults=True)
    )
    assert data_def == {
        "count": 0,
        "ratio": 0,
        "enabled": False,
        "items": [],
        "metadata": {},
        "note": None,
        "threshold": 7,
    }
    assert err_def is not None and "fallback" in err_def, (
        "error string must indicate this is a fallback result"
    )


# ---------------------------------------------------------------------------
# GS-S05 / GS-B08 — structured retries have one total budget below observer caps
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-S05", _CONTRACT, "§structured retry wall-clock", "L2a",
        "llm_structured has one total wall-clock budget below the market-observer "
        "loop budget and scheduler timeout, independent of the provider timeout.",
    ),
)
def test_llm_structured_budget_is_below_observer_caps():
    from aletheia.agents import helpers, market_observer
    from aletheia.agent_runtime.scheduler import _ASYNC_JOB_TIMEOUT_SEC

    assert helpers._LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC < market_observer._MARKET_OBSERVER_LOOP_BUDGET_SEC
    assert helpers._LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC < _ASYNC_JOB_TIMEOUT_SEC["market_observer"]
    source = Path(helpers.__file__).read_text(encoding="utf-8")
    assert "asyncio.wait_for" in source


@spec_claim(
    SpecClaim(
        "GS-B08", _CONTRACT, "§structured retry wall-clock", "L2b",
        "A provider call that exceeds the total structured budget is cancelled and "
        "returns structured_timeout instead of waiting for the outer scheduler timeout.",
    ),
)
def test_llm_structured_cancels_hanging_provider_before_job_timeout(monkeypatch):
    from aletheia.agents import helpers

    cancelled = {"value": False}

    async def hang(req, provider=None, provider_name=None):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    monkeypatch.setattr(helpers, "complete_with_fallback", hang)
    monkeypatch.setattr(helpers, "get_provider", lambda provider_name=None: object())
    monkeypatch.setattr(helpers, "_record_llm", lambda ok: None)
    monkeypatch.setattr(helpers, "_LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC", 0.01)

    data, err = asyncio.run(helpers.llm_structured("sys", "user", schema=_Schema))

    assert data is None
    assert err == "llm_unavailable: structured_timeout"
    assert cancelled["value"] is True


# ---------------------------------------------------------------------------
# GS-S04 — data_collectors fans out under a wall-clock budget < its job timeout
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "GS-S04", _CONTRACT, "§data_collectors budget", "L2a",
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


@spec_claim(
    SpecClaim(
        "GS-S12", _CONTRACT, "§data_collectors lane isolation", "L2a",
        "Primary-venue collection and lower-priority market context have separate "
        "scheduler jobs and independent wall-clock deadlines. Context sources may "
        "not inherit an exhausted primary-venue budget, while the shared io executor "
        "continues to serialize primary CCXT access.",
        status="implemented",
    ),
)
def test_data_collector_context_has_an_independent_bounded_lane():
    from aletheia.agent_runtime import scheduler as sch

    assert sch._DC_CONTEXT_DEADLINE_S < sch._ASYNC_JOB_TIMEOUT_SEC[
        "data_collector_context"
    ]
    source = Path(sch.__file__).read_text(encoding="utf-8")
    assert "data_collector_context" in sch._ESSENTIAL_JOBS
    assert "_async_data_collector_context" in source
    assert "asyncio.gather" in inspect.getsource(sch._async_data_collector_context)


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


@spec_claim(
    SpecClaim(
        "GS-B09",
        _CONTRACT,
        "§collector persistence truthfulness",
        "L2b",
        "When the derivatives collector returns funding or open-interest data but its "
        "repository write fails, derivatives health is failed with a stable code and "
        "the enclosing data_collectors job reports a partial failure, never success.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    ("payload_key", "repo_method", "snapshot", "error_code"),
    [
        (
            "funding",
            "insert_funding_rate_snapshot",
            "funding",
            "derivatives_funding_persist_failed",
        ),
        (
            "open_interest",
            "insert_open_interest_snapshot",
            "open_interest",
            "derivatives_open_interest_persist_failed",
        ),
    ],
)
def test_derivatives_persistence_failure_degrades_domain_and_job(
    monkeypatch, payload_key, repo_method, snapshot, error_code
):
    from aletheia.agent_runtime import scheduler as sch
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data.collectors.derivatives import DerivativesCollector
    from aletheia.data.collectors.microstructure import MicrostructureCollector
    from aletheia.data.models import FundingRateSnapshot, OpenInterestSnapshot
    from aletheia.data.symbols import InstrumentId
    from aletheia.storage import repositories as repo

    observed_at = "2026-01-01T00:00:00+00:00"
    snapshots = {
        "funding": FundingRateSnapshot(
            symbol="BTC/USDT",
            exchange="binance",
            funding_rate=0.0001,
            funding_rate_annualized=0.1095,
            observed_at=observed_at,
        ),
        "open_interest": OpenInterestSnapshot(
            symbol="BTC/USDT",
            exchange="binance",
            open_interest_usd=1_000_000.0,
            observed_at=observed_at,
        ),
    }
    instrument = InstrumentId.from_parts("binance", "BTC/USDT", "perp")
    health: list[tuple[str, str, str | None]] = []

    async def collect_microstructure(_self, _instruments):
        return {"snapshots": [], "errors": []}

    async def collect_derivatives(_self, _symbols):
        return {
            "funding": [snapshots[snapshot]] if payload_key == "funding" else [],
            "open_interest": (
                [snapshots[snapshot]] if payload_key == "open_interest" else []
            ),
            "liquidations": [],
            "errors": [],
        }

    def fail_persist(_row):
        raise RuntimeError("forced repository outage")

    monkeypatch.setattr(sch, "_get_observed_instruments", lambda _cfg: [instrument])
    monkeypatch.setattr(
        sch, "_get_derivative_symbols", lambda _instruments: ["BTC/USDT"]
    )
    monkeypatch.setattr(MicrostructureCollector, "collect", collect_microstructure)
    monkeypatch.setattr(DerivativesCollector, "collect", collect_derivatives)
    monkeypatch.setattr(repo, "insert_orderbook_snapshot", lambda _row: None)
    monkeypatch.setattr(repo, "insert_funding_rate_snapshot", lambda _row: None)
    monkeypatch.setattr(repo, "insert_open_interest_snapshot", lambda _row: None)
    monkeypatch.setattr(repo, "insert_liquidation_event", lambda _row: None)
    monkeypatch.setattr(repo, repo_method, fail_persist)
    monkeypatch.setattr(
        sch,
        "_record_collector_health",
        lambda label, status, **kwargs: health.append(
            (label, status, kwargs.get("error_code"))
        ),
    )

    result = asyncio.run(sch._async_data_collectors(AletheiaConfig(), tick=2))

    assert result == {"status": "partial", "error_codes": [error_code]}
    derivative_health = [row for row in health if row[0] == "derivatives"]
    assert derivative_health == [("derivatives", "failed", error_code)]

    job_events: list[tuple[str, str]] = []

    def return_partial(_job_id, awaitable):
        awaitable.close()
        return result

    monkeypatch.setattr(sch, "load_config", lambda: AletheiaConfig())
    monkeypatch.setattr(sch, "_run_async_with_timeout", return_partial)
    monkeypatch.setattr(sch, "_DC_TICK", 0)
    monkeypatch.setattr(
        sch,
        "_record_job_success",
        lambda job_id: job_events.append(("success", job_id)),
    )
    monkeypatch.setattr(
        sch,
        "_record_job_failure",
        lambda job_id, code: job_events.append(("failure", f"{job_id}:{code}")),
    )

    sch._run_data_collectors()

    assert job_events == [("failure", f"data_collectors:{error_code}")]


@spec_claim(
    SpecClaim(
        "GS-B10",
        _CONTRACT,
        "§universe refresh truthfulness",
        "L2b",
        "An enabled Universe refresh that returns no snapshot is logged and counted as "
        "universe_refresh_no_snapshot failure, not completed_no_snapshot success.",
        status="implemented",
    ),
)
def test_universe_refresh_none_is_agent_and_scheduler_failure(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import scheduler as sch
    from aletheia.files import jsonl
    from aletheia.universe.service import UniverseService

    logs: list[dict] = []
    job_events: list[tuple[str, str]] = []

    async def no_snapshot(_self):
        return None

    def run_inline(_job_id, awaitable):
        return asyncio.run(awaitable)

    monkeypatch.setattr(UniverseService, "refresh", no_snapshot)
    monkeypatch.setattr(jsonl, "write_agent_run_log", logs.append)
    monkeypatch.setattr(
        sch,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=False),
            universe=SimpleNamespace(enabled=True),
        ),
    )
    monkeypatch.setattr(sch, "_run_async_with_timeout", run_inline)
    monkeypatch.setattr(sch, "_cleanup_stale_scanner_runs", lambda: None)
    monkeypatch.setattr(
        sch,
        "_record_job_success",
        lambda job_id: job_events.append(("success", job_id)),
    )
    monkeypatch.setattr(
        sch,
        "_record_job_failure",
        lambda job_id, code: job_events.append(("failure", f"{job_id}:{code}")),
    )

    sch._run_universe_refresh()

    assert len(logs) == 1
    assert logs[0]["status"] == "failed"
    assert logs[0]["outputs_created"] == []
    assert logs[0]["errors"] == ["universe_refresh_no_snapshot"]
    assert job_events == [
        ("failure", "universe_refresh:universe_refresh_no_snapshot")
    ]


@spec_claim(
    SpecClaim(
        "GS-B11",
        _CONTRACT,
        "§scheduled agent result truthfulness",
        "L2b",
        "The shared scheduled-agent adapter raises a stable failure when run_agent "
        "returns completed_with_errors or a non-empty errors list; a clean completed "
        "result remains successful.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    "agent_result",
    [
        {"status": "completed_with_errors", "errors": ["provider unavailable"]},
        {"status": "completed", "errors": ["downstream write failed"]},
    ],
)
def test_async_run_rejects_agent_reported_errors(monkeypatch, agent_result):
    from aletheia.agent_runtime import orchestrator
    from aletheia.agent_runtime import scheduler as sch

    async def report_errors(_agent_name, *, trigger_type):
        assert trigger_type == "scheduled"
        return agent_result

    monkeypatch.setattr(orchestrator, "run_agent", report_errors)

    with pytest.raises(
        RuntimeError, match=r"^scheduled_agent_reported_errors:paper_trader$"
    ):
        asyncio.run(sch._async_run("paper_trader"))


def test_async_run_preserves_clean_completed_result(monkeypatch):
    from aletheia.agent_runtime import orchestrator
    from aletheia.agent_runtime import scheduler as sch

    clean = {"status": "completed", "errors": [], "outputs": ["queued:1"]}

    async def report_success(_agent_name, *, trigger_type):
        assert trigger_type == "scheduled"
        return clean

    monkeypatch.setattr(orchestrator, "run_agent", report_success)

    assert asyncio.run(sch._async_run("paper_trader")) == clean
