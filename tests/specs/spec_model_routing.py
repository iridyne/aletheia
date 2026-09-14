"""Executable endpoint/model routing claims for the inference Contract."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_CONTRACT = "llm_inference_harness_contract"


@spec_claim(
    SpecClaim(
        "MR-S01", _CONTRACT, "§Endpoint/model routing", "L2a",
        "Fast and deep are explicit endpoint/model pairs, initially unassigned.",
        status="implemented",
    ),
)
def test_models_config_has_tier_fields():
    from aletheia.config.schema import ModelsConfig, ModelSelection

    cfg = ModelsConfig()
    assert cfg.deep is None and cfg.fast is None
    cfg = ModelsConfig(
        deep={"endpoint_id": "shared", "model_id": "large"},
        fast={"endpoint_id": "shared", "model_id": "small"},
    )
    assert cfg.deep == ModelSelection(endpoint_id="shared", model_id="large")
    assert cfg.fast == ModelSelection(endpoint_id="shared", model_id="small")
    assert "deep_provider" not in cfg.model_dump()
    assert "fast_provider" not in cfg.model_dump()


@spec_claim(
    SpecClaim(
        "MR-S02", _CONTRACT, "§Endpoint/model routing", "L2a",
        "All inference helpers accept an explicit selection or an untiered endpoint override.",
        status="implemented",
    ),
)
def test_helpers_accept_provider_param():
    from aletheia.agents import helpers

    for name in ("llm_complete", "llm_structured", "llm_stream"):
        parameter = inspect.signature(getattr(helpers, name)).parameters["provider"]
        assert parameter.default is None


@spec_claim(
    SpecClaim(
        "MR-S03", _CONTRACT, "§Endpoint/model routing", "L2a",
        "Tier resolution returns its exact pair; missing and unknown tiers never fall back to Default.",
        status="implemented",
    ),
)
def test_get_provider_for_tier_exists_and_resolves(monkeypatch):
    from types import SimpleNamespace
    from aletheia.config.schema import ModelsConfig, ModelSelection, ProviderConfig
    from aletheia.model_provider import router

    cfg = ModelsConfig(
        default_provider="shared",
        providers={"shared": ProviderConfig(model="preferred")},
        deep={"endpoint_id": "shared", "model_id": "large"},
        fast={"endpoint_id": "shared", "model_id": "small"},
    )
    monkeypatch.setattr(router, "load_config", lambda: SimpleNamespace(models=cfg))
    assert router.get_provider_for_tier("deep") == cfg.deep
    assert router.get_provider_for_tier("fast") == cfg.fast
    assert router.get_provider_for_tier(None) == ModelSelection(endpoint_id="shared", model_id="preferred")
    cfg.fast = None
    for tier in ("fast", "unknown"):
        with pytest.raises(ValueError):
            router.get_provider_for_tier(tier)


@spec_claim(
    SpecClaim(
        "MR-S05", _CONTRACT, "§Endpoint/model routing", "L2b",
        "Bulk research passes its captured fast-tier selection to inference helpers.",
        status="implemented",
    ),
)
def test_fast_tier_agent_uses_provider_routing():
    source = (_ROOT / "extensions" / "serenity" / "research_worker.py").read_text(encoding="utf-8")
    assert 'get_provider_for_tier("fast")' in source
    assert "provider=_fast" in source
