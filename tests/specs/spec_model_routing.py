"""Spec: Agent-tier Model Routing (model_routing_contract).

Guards the two-tier (deep / fast) model routing layer introduced to allow
top-level decision agents to use a stronger model while bulk data-extraction
agents use a cheaper/faster one.

Contract authority:
  docs/design/agent_contracts.md  (§model-routing)

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero MR-* cases.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_CONTRACT = "model_routing_contract"


# ---------------------------------------------------------------------------
# MR-S01 — ModelsConfig exposes deep_provider + fast_provider fields
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "MR-S01", _CONTRACT, "§schema-tier-fields", "L2a",
        "ModelsConfig has deep_provider and fast_provider fields (both str | None, "
        "default None).  None means fall back to default_provider — zero migration cost.",
    ),
)
def test_models_config_has_tier_fields():
    from aletheia.config.schema import ModelsConfig

    cfg = ModelsConfig()
    assert hasattr(cfg, "deep_provider"), "ModelsConfig must have deep_provider field"
    assert hasattr(cfg, "fast_provider"), "ModelsConfig must have fast_provider field"
    assert cfg.deep_provider is None, "deep_provider default must be None"
    assert cfg.fast_provider is None, "fast_provider default must be None"

    # Can be set to a provider name string
    cfg2 = ModelsConfig(deep_provider="anthropic_native", fast_provider="openai_compatible")
    assert cfg2.deep_provider == "anthropic_native"
    assert cfg2.fast_provider == "openai_compatible"


# ---------------------------------------------------------------------------
# MR-S02 — helpers.llm_complete / llm_structured / llm_stream accept provider=
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "MR-S02", _CONTRACT, "§helpers-provider-param", "L2a",
        "helpers.llm_complete, llm_structured, and llm_stream each accept a "
        "keyword-only `provider: str | None = None` parameter so call sites can "
        "select a routing tier without breaking existing callers (None = default).",
    ),
)
def test_helpers_accept_provider_param():
    from aletheia.agents import helpers
    import inspect

    for fn_name in ("llm_complete", "llm_structured", "llm_stream"):
        fn = getattr(helpers, fn_name)
        sig = inspect.signature(fn)
        assert "provider" in sig.parameters, (
            f"helpers.{fn_name} must accept a 'provider' keyword parameter"
        )
        param = sig.parameters["provider"]
        assert param.default is None, (
            f"helpers.{fn_name} provider param default must be None (backward-compat)"
        )


# ---------------------------------------------------------------------------
# MR-S03 — router.get_provider_for_tier() resolves tier → provider name
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "MR-S03", _CONTRACT, "§router-tier-resolver", "L2a",
        "router.get_provider_for_tier(tier) translates 'deep' / 'fast' / None to a "
        "provider name string, falling back to default_provider when the tier field "
        "is unconfigured (None).  Unknown tiers also fall back gracefully.",
    ),
)
def test_get_provider_for_tier_exists_and_resolves():
    from aletheia.model_provider import router as r

    assert hasattr(r, "get_provider_for_tier"), (
        "router must expose get_provider_for_tier()"
    )

    # When deep_provider / fast_provider are not set, resolves to default_provider
    from unittest.mock import patch
    from aletheia.config.schema import ModelsConfig, ProviderConfig

    mock_cfg_obj = type("MockCfg", (), {
        "models": ModelsConfig(
            default_provider="openai_compatible",
            deep_provider=None,
            fast_provider=None,
        )
    })()

    with patch.object(r, "load_config", return_value=mock_cfg_obj):
        assert r.get_provider_for_tier(None) == "openai_compatible"
        assert r.get_provider_for_tier("deep") == "openai_compatible"   # fallback
        assert r.get_provider_for_tier("fast") == "openai_compatible"   # fallback
        assert r.get_provider_for_tier("unknown") == "openai_compatible"  # graceful

    # When tiers are configured, returns the configured name
    mock_cfg_obj2 = type("MockCfg", (), {
        "models": ModelsConfig(
            default_provider="openai_compatible",
            deep_provider="anthropic_native",
            fast_provider="openai_compatible",
        )
    })()

    with patch.object(r, "load_config", return_value=mock_cfg_obj2):
        assert r.get_provider_for_tier("deep") == "anthropic_native"
        assert r.get_provider_for_tier("fast") == "openai_compatible"
        assert r.get_provider_for_tier(None) == "openai_compatible"


# ---------------------------------------------------------------------------
# MR-S04 — Deep-tier agents pass provider= to llm helpers
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "MR-S04", _CONTRACT, "§deep-tier-call-sites", "L2b",
        "The six deep-tier agents (trading_decision, strategy_designer, risk_critic, "
        "market_observer, dreaming_agent, lesson_reviewer) call get_provider_for_tier "
        "and pass its result as provider= to llm_complete / llm_structured.",
    ),
)
def test_deep_tier_agents_use_provider_routing():
    deep_files = [
        _ROOT / "aletheia" / "agent_shell" / "trading_decision.py",
        _ROOT / "aletheia" / "agents" / "strategy_designer.py",
        _ROOT / "aletheia" / "agents" / "risk_critic.py",
        _ROOT / "aletheia" / "agents" / "market_observer.py",
        _ROOT / "aletheia" / "agents" / "dreaming_agent.py",
        _ROOT / "aletheia" / "agents" / "lesson_reviewer.py",
    ]
    for path in deep_files:
        src = path.read_text(encoding="utf-8")
        assert "get_provider_for_tier" in src, (
            f"{path.name} must import and call get_provider_for_tier (deep tier routing)"
        )
        assert 'provider=' in src, (
            f"{path.name} must pass provider= keyword arg to llm_complete/llm_structured"
        )


# ---------------------------------------------------------------------------
# MR-S05 — Fast-tier agent (research_worker) passes provider=
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "MR-S05", _CONTRACT, "§fast-tier-call-sites", "L2b",
        "research_worker.py (the highest-token bulk extraction agent) calls "
        "get_provider_for_tier('fast') and passes it as provider= on all its "
        "llm_complete call sites.",
    ),
)
def test_fast_tier_agent_uses_provider_routing():
    src = (_ROOT / "aletheia" / "agents" / "research_worker.py").read_text(encoding="utf-8")
    assert "get_provider_for_tier" in src, (
        "research_worker must import and call get_provider_for_tier (fast tier routing)"
    )
    assert "'fast'" in src or '"fast"' in src, (
        "research_worker must pass the 'fast' tier string to get_provider_for_tier"
    )
    assert "provider=" in src, (
        "research_worker must pass provider= keyword arg to llm_complete"
    )
