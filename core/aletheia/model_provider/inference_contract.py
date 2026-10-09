"""Content-free identity and policy contract for one logical LLM inference.

The contract deliberately stores hashes and bounded identities, never prompt or
response bodies.  Physical provider attempts remain in ``runtime_cost_events``;
all attempts for one instance of :class:`InferenceCall` share its logical ID.
"""

from __future__ import annotations

import logging
import hashlib
import json
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


logger = logging.getLogger("aletheia.model_provider.inference_contract")

class OutputMode(StrEnum):
    TEXT = "text"
    STRUCTURED = "structured"


class ToolInterface(StrEnum):
    NONE = "none"
    TYPED_COMMAND_TEXT = "typed-command-text"


@dataclass(frozen=True)
class InferenceCallerSpec:
    caller_id: str
    prompt_template_id: str
    prompt_version: str
    output_mode: OutputMode
    tool_interface: ToolInterface = ToolInterface.NONE
    context_policy_id: str = "caller-owned-v1"


def _spec(
    caller_id: str,
    output_mode: OutputMode,
    *,
    prompt_template_id: str | None = None,
    prompt_version: str = "v1",
    tool_interface: ToolInterface = ToolInterface.NONE,
) -> InferenceCallerSpec:
    return InferenceCallerSpec(
        caller_id=caller_id,
        prompt_template_id=prompt_template_id or caller_id,
        prompt_version=prompt_version,
        output_mode=output_mode,
        tool_interface=tool_interface,
    )


# This is the executable caller inventory.  Production helper call sites must
# pass a literal key from this registry; tests/spec_llm_inference_harness.py
# prevents an unregistered call from silently entering production.
CALLER_SPECS: dict[str, InferenceCallerSpec] = {
    spec.caller_id: spec
    for spec in (
        _spec("agent_shell.general_query", OutputMode.TEXT),
        _spec("agent_shell.entry_planner", OutputMode.STRUCTURED),
        _spec(
            "agent_shell.entry_planner.v2",
            OutputMode.STRUCTURED,
            prompt_template_id="agent_shell.entry_planner",
            prompt_version="v2",
        ),
        _spec("agent_shell.synthesis", OutputMode.TEXT),
        _spec("dreaming.lesson_extraction", OutputMode.STRUCTURED),
        _spec("dreaming.nightly_consolidation", OutputMode.TEXT),
        _spec("learning.hypothesis_causal_red_team", OutputMode.STRUCTURED),
        _spec("lesson_reviewer.judge", OutputMode.STRUCTURED),
        _spec("post_trade_reviewer.review", OutputMode.TEXT),
        _spec("research_agent.note", OutputMode.TEXT),
        _spec("research_worker.methodology", OutputMode.TEXT),
        _spec("research_worker.lens", OutputMode.TEXT),
        _spec("research_worker.claims", OutputMode.TEXT),
        _spec("research_worker.themes", OutputMode.TEXT),
        _spec("research_worker.opportunities", OutputMode.TEXT),
        _spec("risk_critic.review", OutputMode.TEXT),
        _spec("insight_synthesizer.insight", OutputMode.TEXT),
        _spec(
            "operator.llm_session.turn",
            OutputMode.TEXT,
            tool_interface=ToolInterface.TYPED_COMMAND_TEXT,
        ),
        _spec("agent_shell.portfolio_review", OutputMode.STRUCTURED),
        _spec("agent_shell.intent_classification", OutputMode.STRUCTURED),
        _spec("equity_observer.market_read", OutputMode.STRUCTURED),
        _spec("prediction_observer.market_read", OutputMode.STRUCTURED),
        _spec("news_analyzer.analysis", OutputMode.STRUCTURED),
        _spec("startup_gate.provider_probe", OutputMode.TEXT),
        _spec("provider_settings.ping", OutputMode.TEXT),
        _spec("provider_settings.candidate_probe", OutputMode.TEXT),
        # Compatibility for direct helper-level tests.  Executable coverage
        # forbids this caller in application code.
        _spec("unregistered", OutputMode.TEXT),
        _spec("unregistered.structured", OutputMode.STRUCTURED),
        _spec("unregistered.stream", OutputMode.TEXT),
    )
}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def schema_contract(schema: Any) -> tuple[str | None, str | None, dict[str, Any] | None]:
    if schema is None:
        return None, None, None
    payload = schema.model_json_schema()  # type: ignore[union-attr]
    if not isinstance(payload, dict):
        raise TypeError("model_json_schema() must return a dict")
    schema_type = schema if isinstance(schema, type) else type(schema)
    schema_id = f"{schema_type.__module__}.{schema_type.__qualname__}"
    return schema_id, _canonical_hash(payload), payload


def capture_environment(*, scope: str = "agent") -> dict[str, Any]:
    """Capture non-secret runtime facts without making them model-visible."""
    try:
        from aletheia.config.loader import load_config

        cfg = load_config()
        permission_mode = (
            cfg.operator.permission_mode if scope == "operator" else cfg.agent.permission_mode
        )
        environment: dict[str, Any] = {
            "permission_mode": _bounded(permission_mode),
            "paper_execution_mode": _bounded(cfg.exchange.paper_execution_mode),
            "live_state": _bounded(cfg.live.state),
        }
    except Exception as exc:
        logger.warning("inference_contract: environment capture unavailable (%s)", type(exc).__name__)
        environment = {}
    try:
        from aletheia.agent_runtime.run_context import (
            current_agent_run_id,
            current_economic_attribution,
        )

        environment["agent_run_id"] = _bounded(current_agent_run_id())
        attribution = current_economic_attribution() or {}
        environment["logical_agent"] = _bounded(attribution.get("logical_agent"))
        # Content-free, explicit causal identities only. These are captured
        # from the caller's bound context; missing downstream entities remain
        # absent rather than being guessed from time or surrounding rows.
        for key in (
            "evaluation_id",
            "opportunity_id",
            "decision_id",
            "proposal_id",
            "planning_generation_id",
        ):
            environment[key] = _bounded(attribution.get(key))
    except Exception:
        logger.debug("inference_contract: agent-run economic-attribution capture failed; environment fields left absent", exc_info=True)
    try:
        from aletheia.agent_runtime.runtime_session import current_provenance

        provenance = current_provenance() or {}
        environment["runtime_session_id"] = _bounded(
            provenance.get("runtime_session_id")
        )
    except Exception:
        logger.debug("inference_contract: runtime-session provenance capture failed; runtime_session_id left absent", exc_info=True)
    return {key: value for key, value in environment.items() if value is not None}


@dataclass
class InferenceCall:
    logical_call_id: str
    caller: InferenceCallerSpec
    prompt_static_sha256: str
    rendered_prompt_sha256: str
    system_chars: int
    message_chars: int
    context_adapter_id: str
    context_manifest: dict[str, Any]
    environment: dict[str, Any]
    tool_policy_id: str
    allowed_tool_ids: tuple[str, ...]
    allowed_tools_sha256: str
    output_schema_id: str | None
    output_schema_sha256: str | None
    requested_provider: str | None
    temperature: float
    max_tokens: int
    allow_model_fallback: bool
    evaluation: dict[str, str] = field(default_factory=dict)
    resolved_provider: str | None = field(default=None, init=False)
    last_attempted_model: str | None = field(default=None, init=False)
    _attempt_index: int = field(default=0, init=False, repr=False)
    _attempt_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def set_resolved_provider(self, provider: str | None) -> None:
        with self._attempt_lock:
            self.resolved_provider = _bounded(provider)

    def record_attempt_identity(self, provider: str | None, model: str | None) -> None:
        with self._attempt_lock:
            self.resolved_provider = _bounded(provider) or self.resolved_provider
            self.last_attempted_model = _bounded(model)

    def claim_attempt_index(self) -> int:
        with self._attempt_lock:
            index = self._attempt_index
            self._attempt_index += 1
        return index

    @property
    def attempt_count(self) -> int:
        with self._attempt_lock:
            return self._attempt_index

    def attempt_metadata(self) -> dict[str, Any]:
        return {
            "logical_call_id": self.logical_call_id,
            "caller": self.caller.caller_id,
            "prompt_template_id": self.caller.prompt_template_id,
            "prompt_version": self.caller.prompt_version,
            "rendered_prompt_sha256": self.rendered_prompt_sha256,
            "context_adapter_id": self.context_adapter_id,
            "tool_policy_id": self.tool_policy_id,
            "allowed_tools_sha256": self.allowed_tools_sha256,
            "output_schema_id": self.output_schema_id,
            "output_schema_sha256": self.output_schema_sha256,
            **self.evaluation,
        }

    def storage_metadata(self) -> dict[str, Any]:
        return {
            "context_manifest": self.context_manifest,
            "environment": self.environment,
            "tool_interface": self.caller.tool_interface.value,
            "allowed_tool_ids": list(self.allowed_tool_ids),
            "context_policy_id": self.caller.context_policy_id,
            "system_chars": self.system_chars,
            "message_chars": self.message_chars,
            **self.evaluation,
        }


def build_inference_call(
    *,
    caller: str,
    system: str | None,
    prompt_static_text: str | None = None,
    messages: list[dict[str, str]],
    context_adapter_id: str,
    context_manifest: dict[str, Any] | None = None,
    schema: Any = None,
    requested_provider: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    allow_model_fallback: bool = True,
    allowed_tool_ids: list[str] | tuple[str, ...] | None = None,
    evaluation: dict[str, Any] | None = None,
) -> InferenceCall:
    spec = CALLER_SPECS.get(caller)
    if spec is None:
        raise ValueError(f"unregistered inference caller: {caller}")
    expected_mode = OutputMode.STRUCTURED if schema is not None else OutputMode.TEXT
    if spec.output_mode is not expected_mode:
        raise ValueError(
            f"inference caller {caller} expects {spec.output_mode.value}, got {expected_mode.value}"
        )
    schema_id, schema_sha256, _ = schema_contract(schema)
    allowed = tuple(sorted({str(item) for item in (allowed_tool_ids or ()) if item}))
    if spec.tool_interface is ToolInterface.NONE and allowed:
        raise ValueError(f"inference caller {caller} has no model tool interface")
    tool_policy_id = (
        "typed-command-allowlist-v1"
        if spec.tool_interface is ToolInterface.TYPED_COMMAND_TEXT
        else "provider-native-tools-none-v1"
    )
    safe_evaluation = {
        str(key): str(value)[:160]
        for key, value in (evaluation or {}).items()
        if key in {
            "evaluation_pair_id",
            "evaluation_arm",
            "fixture_id",
            "blind_label",
            "modality_id",
        }
        and value is not None
    }
    normalized_messages = [
        {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
        for item in messages
    ]
    return InferenceCall(
        logical_call_id="inference_" + uuid4().hex[:20],
        caller=spec,
        prompt_static_sha256=_canonical_hash(
            system if prompt_static_text is None else prompt_static_text
        ),
        rendered_prompt_sha256=_canonical_hash(
            {"system": system or "", "messages": normalized_messages}
        ),
        system_chars=len(system or ""),
        message_chars=sum(len(item["content"]) for item in normalized_messages),
        context_adapter_id=context_adapter_id,
        context_manifest=context_manifest or {},
        environment=capture_environment(
            scope="operator" if caller.startswith("operator.") else "agent"
        ),
        tool_policy_id=tool_policy_id,
        allowed_tool_ids=allowed,
        allowed_tools_sha256=_canonical_hash(allowed),
        output_schema_id=schema_id,
        output_schema_sha256=schema_sha256,
        requested_provider=_bounded(requested_provider),
        temperature=float(temperature),
        max_tokens=int(max_tokens),
        allow_model_fallback=bool(allow_model_fallback),
        evaluation=safe_evaluation,
    )


def caller_matrix_markdown() -> str:
    """Render the normative caller inventory used by the Contract document."""
    header = (
        "| Caller ID | Prompt template | Version | Output | Model tool interface |\n"
        "| --- | --- | --- | --- | --- |"
    )
    rows = []
    for caller_id, spec in sorted(CALLER_SPECS.items()):
        if caller_id.startswith("unregistered"):
            continue
        rows.append(
            f"| `{caller_id}` | `{spec.prompt_template_id}` | `{spec.prompt_version}` "
            f"| `{spec.output_mode.value}` | `{spec.tool_interface.value}` |"
        )
    return "\n".join([header, *rows])
