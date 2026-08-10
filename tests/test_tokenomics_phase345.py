"""Focused Phase 3/4 tokenomics contracts: opt-in denial and detect-only scans."""
from types import SimpleNamespace

import pytest

from aletheia.model_provider.base import ModelRequest
from aletheia.model_provider.fallback import BudgetExceededError, _enforce_budget_if_configured
from aletheia.model_provider.guardrails import detect_prompt_secrets
from aletheia.agents.helpers import (
    ContextAdapter,
    _prepare_context_adapter,
    build_harness_context,
    prepend_environment_header,
    render_context_adapter,
)


def _request() -> ModelRequest:
    request = ModelRequest(messages=[{"role": "user", "content": "safe prompt"}])
    setattr(request, "_aletheia_would_block_observations", [{"policy_id": "budget_test"}])
    return request


def test_budget_enforcement_is_dormant_by_default():
    _enforce_budget_if_configured(
        SimpleNamespace(economics=SimpleNamespace(enabled=False, enforcement_mode="hard_block")),
        _request(),
    )


def test_budget_warn_only_never_denies():
    _enforce_budget_if_configured(
        SimpleNamespace(economics=SimpleNamespace(enabled=True, enforcement_mode="warn_only")),
        _request(),
    )


def test_hard_block_is_explicit_for_non_safety_callers(monkeypatch):
    import aletheia.agent_runtime.run_context as context

    monkeypatch.setattr(context, "current_economic_attribution", lambda: {"logical_agent": "research_worker"})
    with pytest.raises(BudgetExceededError, match="llm_budget_exhausted"):
        _enforce_budget_if_configured(
            SimpleNamespace(economics=SimpleNamespace(enabled=True, enforcement_mode="hard_block")),
            _request(),
        )


@pytest.mark.parametrize("logical_agent", ["pi_trading_decision", "risk_critic", "paper_trader", "agent_shell/trading_decision"])
def test_safety_callers_remain_warn_only_in_every_mode(monkeypatch, logical_agent):
    import aletheia.agent_runtime.run_context as context

    monkeypatch.setattr(context, "current_economic_attribution", lambda: {"logical_agent": logical_agent})
    _enforce_budget_if_configured(
        SimpleNamespace(economics=SimpleNamespace(enabled=True, enforcement_mode="hard_block")),
        _request(),
    )


def test_header_is_default_off_and_opt_in_is_context_only(monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(
        economics=SimpleNamespace(harness_header_default_on=False),
        exchange=SimpleNamespace(paper_execution_mode="paper_internal"),
        live=SimpleNamespace(state="live_disabled"),
    ))
    assert prepend_environment_header("caller prompt") == "caller prompt"
    rendered = prepend_environment_header("caller prompt", include=True)
    assert rendered.endswith("[Caller system prompt follows]\ncaller prompt")
    assert "live_enabled=False" in rendered


def test_context_adapter_registry_preserves_disabled_prompts(monkeypatch):
    assert render_context_adapter("caller prompt", adapter=ContextAdapter.NONE) == "caller prompt"
    with pytest.raises(ValueError):
        render_context_adapter("caller prompt", adapter="operator-v1")

    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(
        economics=SimpleNamespace(harness_header_default_on=False),
        exchange=SimpleNamespace(paper_execution_mode="paper_internal"),
        live=SimpleNamespace(state="live_disabled"),
    ))
    assert prepend_environment_header("caller prompt", adapter="none") == "caller prompt"
    assert "Harness minimal-v1" in prepend_environment_header(
        "caller prompt", adapter="minimal-v1"
    )


def test_technical_context_adapter_links_dynamic_artifact_without_static_prompt_churn():
    artifact = {
        "stable_id": "technical-context-v1:bitget:BTC/USDT:perp:abc",
    }
    rendered, adapter_id, manifest = _prepare_context_adapter(
        "caller prompt",
        adapter="technical-context-v1",
        context_artifacts=[artifact],
    )
    assert rendered == "caller prompt"
    assert adapter_id == "technical-context-v1"
    assert manifest["artifact_ids"] == [artifact["stable_id"]]
    with pytest.raises(ValueError, match="requires a linked technical artifact"):
        _prepare_context_adapter("caller prompt", adapter="technical-context-v1")


def test_minimal_context_has_a_versioned_truthful_contract(monkeypatch):
    import aletheia.agent_runtime.run_context as context
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(
        exchange=SimpleNamespace(paper_execution_mode="paper_internal"),
        live=SimpleNamespace(state="live_disabled"),
    ))
    monkeypatch.setattr(context, "current_agent_run_id", lambda: "run-123")
    monkeypatch.setattr(context, "current_economic_attribution", lambda: {"logical_agent": "market_observer"})

    rendered = build_harness_context().render("caller prompt")
    assert "Harness minimal-v1" in rendered
    assert "logical_agent=market_observer" in rendered
    assert "agent_run_id=run-123" in rendered
    assert "no:live_order" in rendered


def test_detect_only_scanner_never_returns_secret_content():
    findings = detect_prompt_secrets(["key sk-abcdefghijklmnopqrstuv and ~/.secret.key"])
    assert {item["rule_id"] for item in findings} == {"openai_api_key", "secret_key_filename"}
    assert all(set(item) == {"rule_id", "match_count"} for item in findings)
    assert "abcdefghijkl" not in repr(findings)


def test_public_documentation_text_does_not_match():
    assert detect_prompt_secrets(["Documentation explains API-key handling without a credential."]) == []
