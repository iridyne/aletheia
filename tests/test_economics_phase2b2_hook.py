"""Phase 2B-2 shadow-mode hook: complete_with_fallback → metadata_json.

Covers the guarded observation path introduced in
`aletheia/model_provider/fallback.py::_observe_would_block` plus the
`_cost_metadata` change that surfaces the observations in the ledger
row's `metadata_json`. All defaults keep the hook silent; the hook
only fires when the operator sets both `economics.enabled=True` and
`economics.budgets_shadow_enabled=True` in config AND at least one
enabled budget crosses.

The default (no config change, no budget declared) MUST leave every
existing test file in the tokenomics suite passing untouched — this
is verified separately by rerunning the full suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aletheia.economics.budgets import add_budget
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import add_catalog_pricing_snapshot
from aletheia.model_provider.base import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StructuredModelRequest,
)
from aletheia.model_provider.fallback import _observe_would_block, complete_with_fallback


class _FakeProvider(ModelProvider):
    name = "phase2b2_fake"

    def __init__(self) -> None:
        self.model = "fake-model"
        self.calls = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            content="ok",
            model=self.model,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def structured(self, request: StructuredModelRequest, schema: type) -> Any:
        return {"ok": True}

    async def healthcheck(self):  # pragma: no cover
        return None


def _seed_pricing() -> None:
    """Pricing so the hook can estimate cost."""
    add_catalog_pricing_snapshot(
        provider="phase2b2_fake",
        model="fake-model",
        input_usd_per_million=100.0,
        output_usd_per_million=100.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test",
        version="catalog-model-family-test",
    )


def _fake_request(*, max_tokens: int = 2_000) -> ModelRequest:
    """Prompt heavy enough to produce a non-trivial cost estimate."""
    return ModelRequest(
        messages=[{"role": "user", "content": "x" * 8_000}],
        max_tokens=max_tokens,
    )


class _FakeEconomics:
    def __init__(self, *, enabled: bool, shadow: bool) -> None:
        self.enabled = enabled
        self.budgets_shadow_enabled = shadow
        self.enforcement_mode = "warn_only"


class _FakeConfig:
    def __init__(self, *, enabled: bool, shadow: bool) -> None:
        self.economics = _FakeEconomics(enabled=enabled, shadow=shadow)


# ── Guarded observation invariants ───────────────────────────────────────────


def test_hook_noop_when_economics_disabled():
    """With EconomicsConfig.enabled=False, the observation function is inert."""
    ensure_economics_schema()
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=0.0001, window="daily")

    request = _fake_request()
    _observe_would_block(
        _FakeConfig(enabled=False, shadow=True), request, _FakeProvider(),
        "phase2b2_fake",
    )
    assert not hasattr(request, "_aletheia_would_block_observations")


def test_hook_noop_when_shadow_flag_off():
    """Even with .enabled=True, .budgets_shadow_enabled=False keeps it silent."""
    ensure_economics_schema()
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=0.0001, window="daily")

    request = _fake_request()
    _observe_would_block(
        _FakeConfig(enabled=True, shadow=False), request, _FakeProvider(),
        "phase2b2_fake",
    )
    assert not hasattr(request, "_aletheia_would_block_observations")


def test_hook_noop_when_no_pricing_snapshot():
    """No pricing → no honest cost estimate → skip. Do not fabricate a number."""
    ensure_economics_schema()
    add_budget(scope="system", subject=None, usd_cap=0.0001, window="daily")

    request = _fake_request()
    _observe_would_block(
        _FakeConfig(enabled=True, shadow=True), request, _FakeProvider(),
        "phase2b2_fake",
    )
    assert not hasattr(request, "_aletheia_would_block_observations")


def test_hook_noop_when_no_budgets_declared():
    """Even fully armed, no budget → no observation."""
    ensure_economics_schema()
    _seed_pricing()

    request = _fake_request()
    _observe_would_block(
        _FakeConfig(enabled=True, shadow=True), request, _FakeProvider(),
        "phase2b2_fake",
    )
    assert not hasattr(request, "_aletheia_would_block_observations")


def test_hook_noop_when_budget_within_cap():
    """Cheap call under a generous budget → no observation."""
    ensure_economics_schema()
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=1_000_000.0, window="daily")

    request = _fake_request()
    _observe_would_block(
        _FakeConfig(enabled=True, shadow=True), request, _FakeProvider(),
        "phase2b2_fake",
    )
    assert not hasattr(request, "_aletheia_would_block_observations")


def test_hook_stores_observations_when_over_cap():
    """Tight cap → observations stashed on request."""
    ensure_economics_schema()
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=0.0001, window="daily")

    request = _fake_request()
    _observe_would_block(
        _FakeConfig(enabled=True, shadow=True), request, _FakeProvider(),
        "phase2b2_fake",
    )
    observations = getattr(request, "_aletheia_would_block_observations", None)
    assert observations is not None
    assert len(observations) == 1
    assert observations[0]["scope"] == "system"
    assert observations[0]["usd_cap"] == pytest.approx(0.0001)
    assert observations[0]["over_by_usd"] > 0


# ── End-to-end: observations reach metadata_json ─────────────────────────────


@pytest.mark.asyncio
async def test_observations_reach_metadata_json_via_tracing_decorator():
    """Observations attached to request surface in ledger row's metadata_json.

    Uses the direct provider path (mirrors test_economic_cost_tracing style)
    so the assertion targets the `_cost_metadata` change in base.py, not
    the fallback loop or the broker. Hook wiring is covered separately by
    the six `_observe_would_block` tests above.
    """
    from aletheia.storage.sqlite import get_db

    ensure_economics_schema()
    provider = _FakeProvider()
    request = _fake_request()

    fake_observations = [
        {
            "policy_id": "budget_test",
            "scope": "system",
            "subject": None,
            "window": "daily",
            "usd_cap": 0.0001,
            "spent_before_call_usd": 0.0,
            "projected_after_call_usd": 0.5,
            "over_by_usd": 0.4999,
        }
    ]
    setattr(request, "_aletheia_would_block_observations", fake_observations)

    await provider.complete(request)

    row = get_db().execute(
        "SELECT metadata_json FROM runtime_cost_events "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    metadata = json.loads(row[0])
    assert metadata.get("would_block") == fake_observations


@pytest.mark.asyncio
async def test_regular_call_omits_would_block_key():
    """When no observations are attached, `would_block` is absent from metadata."""
    from aletheia.storage.sqlite import get_db

    ensure_economics_schema()
    provider = _FakeProvider()
    request = _fake_request()

    await provider.complete(request)

    row = get_db().execute(
        "SELECT metadata_json FROM runtime_cost_events "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    metadata = json.loads(row[0])
    assert "would_block" not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,shadow,tracing,cap,agent,denied", [
    ("hard_block", False, True, .0001, "research_worker", True),
    ("hard_block", True, True, .0001, "research_worker", True),
    ("hard_block", True, False, .0001, "research_worker", True),
    ("hard_block", False, False, .0001, "research_worker", True),
    ("fallback_to_defaults", False, False, .0001, "research_worker", True),
    ("warn_only", False, True, .0001, "research_worker", False),
    ("hard_block", False, True, 1000, "research_worker", False),
    ("hard_block", False, False, .0001, "pi_trading_decision", False),
])
async def test_enforcement_independent_of_observation(monkeypatch, mode, shadow, tracing, cap, agent, denied):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.agent_runtime.run_context import bind_economic_attribution
    from aletheia.model_provider import fallback

    cfg = AletheiaConfig()
    cfg.economics.enabled = True
    cfg.economics.budgets_shadow_enabled = shadow
    cfg.economics.enforcement_mode = mode
    monkeypatch.setattr(fallback, "load_config", lambda: cfg)
    monkeypatch.setenv("ALETHEIA_COST_TRACING", "1" if tracing else "0")
    ensure_economics_schema()
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=cap, window="daily")
    provider = _FakeProvider()
    request = _fake_request()
    with bind_economic_attribution(logical_agent=agent):
        if denied:
            with pytest.raises(fallback.BudgetExceededError, match="llm_budget_exhausted"):
                await complete_with_fallback(request, provider_name=provider.name, provider=provider)
            assert provider.calls == []
        else:
            response = await complete_with_fallback(request, provider_name=provider.name, provider=provider)
            assert response.content == "ok"
            assert provider.calls == [request]
    if not shadow or not tracing:
        assert not hasattr(request, "_aletheia_would_block_observations")


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["pricing", "database"])
async def test_reused_request_clears_stale_denial_when_evaluation_unavailable(monkeypatch, unavailable):
    import sqlite3
    from aletheia.config.schema import AletheiaConfig
    from aletheia.economics import budgets
    from aletheia.model_provider import fallback

    cfg = AletheiaConfig()
    cfg.economics.enabled = True
    cfg.economics.budgets_shadow_enabled = True
    cfg.economics.enforcement_mode = "hard_block"
    monkeypatch.setattr(fallback, "load_config", lambda: cfg)
    db = ensure_economics_schema()
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=.0001, window="daily")
    request = _fake_request()
    provider = _FakeProvider()
    with pytest.raises(fallback.BudgetExceededError):
        await complete_with_fallback(request, provider_name=provider.name, provider=provider)
    assert provider.calls == []
    assert getattr(request, "_aletheia_would_block_observations", None)
    if unavailable == "pricing":
        db.execute("DELETE FROM pricing_snapshots")
        db.commit()
    else:
        def storage_unavailable():
            raise sqlite3.OperationalError("synthetic database unavailable")
        monkeypatch.setattr(budgets, "ensure_economics_schema", storage_unavailable)
    assert _observe_would_block(cfg, request, provider, provider.name) is None
    assert not hasattr(request, "_aletheia_would_block_observations")
    response = await complete_with_fallback(request, provider_name=provider.name, provider=provider)
    assert response.content == "ok"
    assert provider.calls == [request]


def test_evaluated_within_budget_is_distinct_from_unavailable():
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.economics.enabled = True
    cfg.economics.enforcement_mode = "hard_block"
    _seed_pricing()
    add_budget(scope="system", subject=None, usd_cap=1000, window="daily")
    provider = _FakeProvider()
    assert _observe_would_block(cfg, _fake_request(), provider, provider.name) == []
