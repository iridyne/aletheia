from datetime import datetime, timezone

import pytest

from aletheia.economics.tracing import add_catalog_pricing_snapshot, estimate_llm_cost
from aletheia.economics.usage_insights import build_usage_insights
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.inference_calls import (
    record_inference_finished,
    record_inference_started,
)
from aletheia.model_provider.inference_contract import build_inference_call
from aletheia.storage.sqlite import get_db


def _event(
    event_id: str,
    *,
    started_at: str,
    cost: float | None = None,
    cost_status: str = "unknown",
    call_id: str | None = None,
    attempt_index: int = 0,
    status: str = "success",
    usage: str = '{"normalized":{"input_tokens":100,"output_tokens":20}}',
) -> None:
    ensure_economics_schema()
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,latency_ms,usage_json,estimated_cost_usd,
            currency,cost_status,metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            "llm",
            call_id or event_id,
            "provider-a",
            "model-a",
            attempt_index,
            status,
            started_at,
            started_at,
            100 + attempt_index,
            usage,
            cost,
            "USD",
            cost_status,
            "{}",
            started_at,
        ),
    )


def _price(at: str, rate: float, version: str) -> None:
    add_catalog_pricing_snapshot(
        provider="provider-a",
        model="model-a",
        input_usd_per_million=rate,
        output_usd_per_million=rate,
        effective_from=at,
        source="litellm_public_median:test",
        version=f"catalog-model-family-{version}",
    )


def _logical(
    *,
    started_at: str,
    status: str,
    attempts: int = 0,
):
    call = build_inference_call(
        caller="unregistered",
        system="system",
        messages=[{"role": "user", "content": "user"}],
        context_adapter_id="none",
        requested_provider="provider-a",
    )
    record_inference_started(call, started_at=started_at)
    for _ in range(attempts):
        call.claim_attempt_index()
    record_inference_finished(
        call,
        status=status,
        error_type="test_error" if status == "error" else None,
        finished_at=started_at,
    )
    return call


def test_usage_v2_uses_event_effective_price_not_latest_snapshot():
    _price("2026-01-01T00:00:00+00:00", 1, "old")
    _price("2026-01-03T00:00:00+00:00", 10, "new")
    _event(
        "historical",
        started_at="2026-01-02T12:00:00+00:00",
        usage='{"normalized":{"input_tokens":1000000,"output_tokens":0}}',
    )

    data = build_usage_insights(
        days=1,
        as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        group="summary",
    )

    assert data["contract_version"] == 2
    assert data["overview"]["cost_usd"] == 1
    assert (
        get_db().execute("SELECT estimated_cost_usd FROM runtime_cost_events WHERE id='historical'").fetchone()[0]
        is None
    )


def test_usage_v2_cache_pricing_is_exact_and_fails_closed():
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 60,
        "provider_cache_read_input_tokens": 60,
        "provider_cache_creation_input_tokens": 10,
        "reasoning_tokens": None,
        "web_search_requests": None,
    }
    price = {
        "input_usd_per_million": 1,
        "output_usd_per_million": 2,
        "cached_input_usd_per_million": 0.1,
        "cache_creation_input_usd_per_million": 1.5,
    }

    assert estimate_llm_cost(usage, price)[0] == pytest.approx(0.000091)
    del price["cache_creation_input_usd_per_million"]
    cost, status, metadata = estimate_llm_cost(usage, price)
    assert cost is None and status == "unknown"
    assert metadata == {"reason": "positive_special_rate_missing", "field": "cache_creation"}


def test_usage_v2_missing_attempt_evidence_invalidates_totals_bucket_and_row():
    _price("2026-01-01T00:00:00+00:00", 1, "v1")
    _event("complete", started_at="2026-01-02T01:00:00+00:00")
    _event("missing", started_at="2026-01-02T01:10:00+00:00", usage="{}")

    summary = build_usage_insights(
        days=1,
        as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        group="summary",
    )
    operations = build_usage_insights(
        days=1,
        as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        group="operations",
    )

    assert summary["overview"]["cost_usd"] is None
    assert summary["overview"]["total_tokens"] is None
    bucket = next(row for row in summary["trend"] if row["llm_calls"] == 2)
    assert bucket["cost_usd"] is None and bucket["input_tokens"] is None
    assert operations["breakdowns"]["models"][0]["cost_usd"] is None
    assert operations["breakdowns"]["models"][0]["input_tokens"] is None
    assert operations["breakdowns"]["models"][0]["output_tokens"] is None


def test_usage_v2_counts_logical_calls_separately_from_attempts():
    _event(
        "attempt-0",
        started_at="2026-01-02T01:00:00+00:00",
        cost=0.1,
        cost_status="known",
        call_id="logical",
        status="error",
    )
    _event(
        "attempt-1",
        started_at="2026-01-02T01:00:01+00:00",
        cost=0.2,
        cost_status="known",
        call_id="logical",
        attempt_index=1,
    )

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))
    operations = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc), group="operations")

    assert summary["overview"]["llm_calls"] == 1
    assert summary["overview"]["success_rate_pct"] == 100
    assert operations["reliability"] == {
        "attempts": 2,
        "errors": 0,
        "retries": 1,
        "fallbacks": 0,
        "p50_latency_ms": 100.5,
        "p95_latency_ms": 100.95,
    }


def test_zero_attempt_failure_is_one_completed_call_with_confirmed_zero_usage():
    _logical(started_at="2026-01-02T01:00:00+00:00", status="error")

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))
    operations = build_usage_insights(
        days=1,
        as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        group="operations",
    )

    assert summary["overview"] == {
        "cost_usd": 0.0,
        "total_tokens": 0,
        "llm_calls": 1,
        "success_rate_pct": 0.0,
        "avg_cost_per_call_usd": 0.0,
    }
    assert operations["reliability"]["attempts"] == 0
    assert operations["reliability"]["errors"] == 1


def test_declared_attempt_missing_fails_closed_but_keeps_logical_call_count():
    call = _logical(
        started_at="2026-01-02T01:00:00+00:00",
        status="success",
        attempts=2,
    )
    _event(
        "only-physical-attempt",
        started_at="2026-01-02T01:00:00+00:00",
        cost=0.1,
        cost_status="known",
        call_id=call.logical_call_id,
    )

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))

    assert summary["overview"]["llm_calls"] == 1
    assert summary["overview"]["success_rate_pct"] == 100
    assert summary["overview"]["cost_usd"] is None
    assert summary["overview"]["total_tokens"] is None


def test_running_logical_call_is_excluded_from_completed_kpis():
    _logical(started_at="2026-01-02T01:00:00+00:00", status="error")
    running = build_inference_call(
        caller="unregistered",
        system="system",
        messages=[{"role": "user", "content": "running"}],
        context_adapter_id="none",
        requested_provider="provider-a",
    )
    record_inference_started(running, started_at="2026-01-02T02:00:00+00:00")

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))

    assert summary["overview"]["llm_calls"] == 1


def test_logical_call_keeps_retry_attempt_that_crosses_window_end():
    call = _logical(
        started_at="2026-01-02T23:59:59+00:00",
        status="success",
        attempts=2,
    )
    _event(
        "boundary-attempt-0",
        started_at="2026-01-02T23:59:59+00:00",
        cost=0.1,
        cost_status="known",
        call_id=call.logical_call_id,
        attempt_index=0,
        status="error",
    )
    _event(
        "boundary-attempt-1",
        started_at="2026-01-03T00:00:01+00:00",
        cost=0.2,
        cost_status="known",
        call_id=call.logical_call_id,
        attempt_index=1,
    )

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))

    assert summary["overview"]["llm_calls"] == 1
    assert summary["overview"]["cost_usd"] == pytest.approx(0.3)
    assert sum(row["llm_calls"] or 0 for row in summary["trend"]) == 1


def test_usage_v2_preserves_persisted_normalized_cache_counters():
    _event(
        "cached",
        started_at="2026-01-02T01:00:00+00:00",
        cost=0.1,
        cost_status="known",
        usage=(
            '{"normalized":{"input_tokens":100,"output_tokens":20,'
            '"cached_input_tokens":60,"provider_cache_read_input_tokens":60}}'
        ),
    )

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))

    assert summary["token_composition"]["cache_read_tokens"] == 60


def test_usage_v2_compares_only_a_fully_observed_previous_window():
    ensure_economics_schema()
    get_db().execute(
        "INSERT INTO runtime_sessions (session_id,started_at,ended_at,economics_tracing_enabled) VALUES (?,?,?,?)",
        ("comparison", "2026-01-01T00:00:00+00:00", "2026-01-03T00:00:00+00:00", 1),
    )
    _event("previous", started_at="2026-01-01T12:00:00+00:00", cost=1, cost_status="known")
    _event("current", started_at="2026-01-02T12:00:00+00:00", cost=2, cost_status="known")

    summary = build_usage_insights(days=1, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc))

    assert summary["window"]["granularity"] == "hour"
    assert summary["comparison"] == {
        "complete": True,
        "cost_usd_change_pct": 100,
        "total_tokens_change_pct": 0,
        "llm_calls_change_pct": 0,
        "avg_cost_per_call_usd_change_pct": 100,
        "success_rate_change_pct_points": 0,
    }


def test_usage_v2_has_only_summary_and_operations():
    summary = build_usage_insights(days=1, group="summary")
    operations = build_usage_insights(days=1, group="operations")

    assert set(summary) == {"contract_version", "window", "overview", "comparison", "token_composition", "trend"}
    assert set(operations) == {"contract_version", "window", "breakdowns", "reliability", "active_cooldowns", "budgets"}
    with pytest.raises(ValueError, match="summary or operations"):
        build_usage_insights(days=1, group="analysis")
