"""Executable claims for the framework-wide LLM Inference Harness Contract."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aletheia.model_provider.inference_contract import (
    CALLER_SPECS,
    caller_matrix_markdown,
)
from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_CONTRACT = "llm_inference_harness_contract"
_CONTRACT_DOC = _ROOT / "docs" / "design" / "contracts" / "llm_inference_harness_contract.md"
_HELPER_NAMES = {"llm_complete", "llm_structured", "llm_stream"}


@pytest.mark.asyncio
@spec_claim(
    SpecClaim(
        "LLMH-S08", _CONTRACT, "§Failure and recovery semantics", "L2a",
        "Fallback candidates come only from explicit ordered configuration, never discovery.",
        status="implemented",
    ),
)
async def test_fallback_candidates_are_explicit_and_bounded(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import fallback

    cfg = AletheiaConfig()
    pcfg = cfg.models.providers["endpoint"] = ProviderConfig(model="primary")
    monkeypatch.setattr(fallback, "load_config", lambda: cfg)
    discovery = AsyncMock(return_value=[{"slug": "unconfigured"}])
    monkeypatch.setattr(fallback.provider_router, "list_models_cached", discovery)
    provider = SimpleNamespace(model="primary")
    assert await fallback._candidate_models("endpoint", provider) == ["primary"]
    pcfg.fallback_models = ["primary", " m2 ", "m2", "@other:m3", ""]
    assert await fallback._candidate_models("endpoint", provider) == ["primary", "m2", "@other:m3"]
    pcfg.fallback_models = [f"m{i}" for i in range(10)]
    assert await fallback._candidate_models("endpoint", provider) == [
        "primary", *pcfg.fallback_models[:fallback._MAX_MODELS_PER_REQUEST - 1],
    ]
    pcfg.model_fallback_enabled = False
    assert await fallback._candidate_models("endpoint", provider) == ["primary"]
    discovery.assert_not_awaited()


@spec_claim(
    SpecClaim(
        "LLMH-S07", _CONTRACT, "§Entry points", "L2a",
        "Retired subprocess and proprietary model transports have no implementation, "
        "registry entry or login route; the fixed-provider mutation endpoint is retired.",
        status="implemented",
    ),
)
def test_retired_model_transports_have_no_active_entrypoint():
    from aletheia.model_provider.registry import PROVIDER_TYPES
    from aletheia.model_provider.router import PROVIDER_REGISTRY
    from aletheia.server.routes.model import router

    retired = {"codex_cli", "claude_cli", "pi_cli", "openai_native", "anthropic_native", "databricks"}
    assert retired.isdisjoint(PROVIDER_TYPES)
    assert retired.isdisjoint(PROVIDER_REGISTRY)
    for module in retired | {"cli_harness", "codex_auth", "oauth", "openai_oauth", "claude_oauth"}:
        assert not (_ROOT / "aletheia" / "model_provider" / f"{module}.py").exists()
    retired_paths = (
        "/api/model/codex/", "/api/model/openai-oauth/", "/api/model/claude-oauth/",
        "/api/settings/model-integrations/",
    )
    assert not any(route.path.startswith(retired_paths) for route in router.routes)
    assert "/api/settings/model" not in {route.path for route in router.routes}


def _production_helper_calls() -> list[tuple[Path, ast.Call]]:
    calls: list[tuple[Path, ast.Call]] = []
    for path in (_ROOT / "aletheia").rglob("*.py"):
        if path == _ROOT / "aletheia" / "agents" / "helpers.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _HELPER_NAMES:
                    calls.append((path, node))
    return calls


@spec_claim(
    SpecClaim(
        "LLMH-S01",
        _CONTRACT,
        "§Entry points",
        "L2a",
        "Every production helper call declares a literal registered caller identity.",
        status="implemented",
    ),
)
def test_every_production_helper_call_is_registered():
    failures: list[str] = []
    for path, node in _production_helper_calls():
        keyword = next((item for item in node.keywords if item.arg == "caller"), None)
        relative = path.relative_to(_ROOT)
        if keyword is None or not isinstance(keyword.value, ast.Constant):
            failures.append(f"{relative}:{node.lineno}: missing literal caller")
            continue
        caller = keyword.value.value
        if not isinstance(caller, str) or caller.startswith("unregistered"):
            failures.append(f"{relative}:{node.lineno}: invalid caller {caller!r}")
        elif caller not in CALLER_SPECS:
            failures.append(f"{relative}:{node.lineno}: unknown caller {caller!r}")
    assert not failures, "\n".join(failures)


@spec_claim(
    SpecClaim(
        "LLMH-S02",
        _CONTRACT,
        "§Current caller coverage matrix",
        "L2a",
        "The Contract caller matrix is rendered from the executable caller registry.",
        status="implemented",
    ),
)
def test_contract_caller_matrix_matches_registry():
    text = _CONTRACT_DOC.read_text(encoding="utf-8")
    rendered = text.split("<!-- BEGIN GENERATED CALLER MATRIX -->\n", 1)[1]
    rendered = rendered.split("\n<!-- END GENERATED CALLER MATRIX -->", 1)[0]
    assert rendered == caller_matrix_markdown()


@spec_claim(
    SpecClaim(
        "LLMH-S03",
        _CONTRACT,
        "§Durable state and consumers",
        "L2a",
        "Logical inference storage is content-free and links physical attempts and context artifacts.",
        status="implemented",
    ),
)
def test_logical_storage_is_content_free_and_linked():
    schema = (_ROOT / "aletheia" / "economics" / "schema.py").read_text(
        encoding="utf-8"
    )
    logical = schema.split("CREATE TABLE IF NOT EXISTS llm_inference_calls", 1)[1]
    logical = logical.split(");", 1)[0]
    assert "logical_call_id TEXT PRIMARY KEY" in logical
    assert "rendered_prompt_sha256" in logical
    assert "output_schema_sha256" in logical
    assert "prompt_text" not in logical and "response_text" not in logical
    assert "logical_call_id TEXT" in schema.split(
        "CREATE TABLE IF NOT EXISTS llm_context_artifacts", 1
    )[1]


@spec_claim(
    SpecClaim(
        "LLMH-S04",
        _CONTRACT,
        "§Tool control",
        "L2b",
        "Agent and Agent Shell tool ledgers carry the logical inference origin without changing permission checks.",
        status="implemented",
    ),
)
def test_tool_ledgers_carry_inference_origin_and_keep_permission_checks():
    migrations = (_ROOT / "aletheia" / "storage" / "migrations.py").read_text(
        encoding="utf-8"
    )
    executor = (_ROOT / "aletheia" / "agent_shell" / "executor.py").read_text(
        encoding="utf-8"
    )
    assert '("inference_call_id", "agent_tool_calls", "TEXT")' in migrations
    assert '("inference_call_id", "agent_shell_commands", "TEXT")' in migrations
    assert "check_permission(" in executor
    assert "worker_allowed" in executor
    assert "requires_confirmation" in executor
    assert "inference_call_id: str | None" in executor


@spec_claim(
    SpecClaim(
        "LLMH-S05",
        _CONTRACT,
        "§Entry points",
        "L2a",
        "Every application-level physical ModelRequest carries a logical inference envelope.",
        status="implemented",
    ),
)
def test_application_model_requests_carry_inference_envelope():
    failures: list[str] = []
    application_roots = (
        _ROOT / "aletheia" / "agents",
        _ROOT / "aletheia" / "agent_runtime",
        _ROOT / "aletheia" / "agent_shell",
        _ROOT / "aletheia" / "analytics",
        _ROOT / "aletheia" / "data",
        _ROOT / "aletheia" / "server",
    )
    for root in application_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ModelRequest"
                ):
                    continue
                if not any(item.arg == "inference" for item in node.keywords):
                    failures.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    assert not failures, "ModelRequest without inference envelope:\n" + "\n".join(failures)


@spec_claim(
    SpecClaim(
        "LLMH-S06",
        _CONTRACT,
        "§Failure and recovery semantics",
        "L2a",
        "Structured failure diagnostics persist only fixed content-free enums, "
        "never response content, values, arbitrary keys, field names, lengths, or hashes.",
        status="implemented",
    ),
)
def test_structured_failure_diagnostics_are_content_free_enums():
    from aletheia.economics.inference_calls import _safe_failure_diagnostics

    assert _safe_failure_diagnostics(
        {
            "response_shape": "json_object",
            "schema_error_code": "missing_required",
            "response_text": "must never persist",
            "parsed_root_keys": "must never persist",
        }
    ) == {
        "response_shape": "json_object",
        "schema_error_code": "missing_required",
    }
    assert _safe_failure_diagnostics(
        {
            "response_shape": "unregistered-shape",
            "schema_error_code": "unregistered-error",
        }
    ) == {}
