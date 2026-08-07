"""Shared helpers for agents: LLM calls with graceful degradation + tool logging."""

import asyncio
import logging
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import StrEnum
import threading
from contextvars import ContextVar

from aletheia.model_provider.base import ModelRequest, StreamChunk
from aletheia.model_provider.base import model_json_schema_payload
from aletheia.model_provider.inference_contract import InferenceCall, build_inference_call
from aletheia.model_provider.router import get_provider
from aletheia.model_provider.fallback import BudgetExceededError, complete_with_fallback

logger = logging.getLogger("aletheia.agents")

# Must stay below the observer loop budget (200s) and observer job timeout (240s).
# The provider may allow 300s for long research generations, but structured observer
# calls need their own total budget across all repair attempts so scheduler cancellation
# never abandons the event-loop thread mid-request.
_LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC = 180.0
_LAST_INFERENCE_CALL_ID: ContextVar[str | None] = ContextVar(
    "aletheia_last_inference_call_id", default=None
)

_PYDANTIC_SCHEMA_ERROR_CODES = {
    "missing": "missing_required",
    "literal_error": "outside_enum",
    "enum": "outside_enum",
}


def _exception_detail(exc: BaseException) -> str:
    """Keep the exception class when libraries raise with an empty message."""
    name = type(exc).__name__
    message = str(exc).strip()
    return f"{name}: {message}" if message else name


class ContextAdapter(StrEnum):
    """Registered context adapters; planned variants stay unavailable until proven."""

    NONE = "none"
    MINIMAL_V1 = "minimal-v1"


@dataclass(frozen=True)
class HarnessContext:
    """Truthful, non-secret model-visible context for the minimal-v1 adapter."""

    adapter_id: str
    observed_at: str
    execution_mode: str
    live_enabled: bool
    agent_run_id: str | None = None
    logical_agent: str | None = None

    def manifest(self) -> dict[str, object]:
        return {
            "model_visible": True,
            "adapter_id": self.adapter_id,
            "observed_at": self.observed_at,
            "execution_mode": self.execution_mode,
            "live_enabled": self.live_enabled,
            "agent_run_id": self.agent_run_id,
            "logical_agent": self.logical_agent,
            "tool_authority_statement": "runtime-enforced:no-live-order",
        }

    def render(self, system: str) -> str:
        identity = " ".join(
            part
            for part in (
                f"agent_run_id={self.agent_run_id}" if self.agent_run_id else None,
                f"logical_agent={self.logical_agent}" if self.logical_agent else None,
            )
            if part
        )
        lines = [
            f"[Aletheia Harness {self.adapter_id}]",
            f"Time: {self.observed_at}",
            f"Trading mode: {self.execution_mode} live_enabled={self.live_enabled}",
            "Tool permissions: enforced by runtime; no:live_order",
        ]
        if identity:
            lines.append(f"Run: {identity}")
        return "\n".join([*lines, "", "[Caller system prompt follows]", system])


def build_harness_context() -> HarnessContext:
    """Build minimal-v1 from current runtime facts; never invent unavailable identity."""
    from aletheia.config.loader import load_config

    cfg = load_config()
    try:
        from aletheia.agent_runtime.run_context import current_agent_run_id, current_economic_attribution

        agent_run_id = current_agent_run_id()
        logical_agent = (current_economic_attribution() or {}).get("logical_agent")
    except Exception:
        agent_run_id = logical_agent = None
    return HarnessContext(
        adapter_id="minimal-v1",
        observed_at=datetime.now(timezone.utc).isoformat(),
        execution_mode=cfg.exchange.paper_execution_mode,
        live_enabled=cfg.live.state != "live_disabled",
        agent_run_id=agent_run_id,
        logical_agent=logical_agent,
    )


def render_context_adapter(system: str, *, adapter: ContextAdapter | str) -> str:
    """Render a registered adapter; `none` preserves the caller prompt exactly."""
    selected = ContextAdapter(adapter)
    if selected is ContextAdapter.NONE:
        return system
    if selected is ContextAdapter.MINIMAL_V1:
        return build_harness_context().render(system)
    raise AssertionError(f"unhandled context adapter: {selected}")


def _prepare_context_adapter(
    system: str,
    *,
    include: bool | None = None,
    adapter: ContextAdapter | str | None = None,
) -> tuple[str, str, dict[str, object]]:
    if adapter is None:
        from aletheia.config.loader import load_config

        enabled = (
            load_config().economics.harness_header_default_on
            if include is None
            else include
        )
        selected = ContextAdapter.MINIMAL_V1 if enabled else ContextAdapter.NONE
    else:
        selected = ContextAdapter(adapter)
    if selected is ContextAdapter.NONE:
        return system, selected.value, {"model_visible": False, "adapter_id": selected.value}
    context = build_harness_context()
    return context.render(system), selected.value, context.manifest()


def prepend_environment_header(
    system: str,
    *,
    include: bool | None = None,
    adapter: ContextAdapter | str | None = None,
) -> str:
    """Render a default-off adapter while retaining the legacy bool experiment switch."""
    rendered, _, _ = _prepare_context_adapter(system, include=include, adapter=adapter)
    return rendered


def _record_call_started(call: InferenceCall, artifacts: list[dict] | None, symbol: str | None) -> None:
    from aletheia.economics.inference_calls import record_inference_started

    _LAST_INFERENCE_CALL_ID.set(call.logical_call_id)
    record_inference_started(call)
    if not artifacts:
        return
    from aletheia.economics.context_artifacts import record_context_artifacts

    record_context_artifacts(
        run_id=call.environment.get("agent_run_id"),
        caller=call.caller.caller_id,
        logical_call_id=call.logical_call_id,
        symbol=symbol,
        artifacts=artifacts,
        evaluation_pair_id=call.evaluation.get("evaluation_pair_id"),
        evaluation_arm=call.evaluation.get("evaluation_arm"),
        fixture_id=call.evaluation.get("fixture_id"),
    )


def _record_call_finished(
    call: InferenceCall,
    *,
    status: str,
    response=None,
    parse_valid: bool | None = None,
    schema_valid: bool | None = None,
    error_type: str | None = None,
    failure_diagnostics: dict[str, str] | None = None,
) -> None:
    from aletheia.economics.inference_calls import record_inference_finished

    record_inference_finished(
        call,
        status=status,
        response=response,
        parse_valid=parse_valid,
        schema_valid=schema_valid,
        error_type=error_type,
        failure_diagnostics=failure_diagnostics,
    )


def current_inference_call_id() -> str | None:
    """Return the most recent logical inference in the current async task."""
    return _LAST_INFERENCE_CALL_ID.get()


async def llm_complete(
    system: str,
    user: str,
    *,
    max_tokens: int = 800,
    provider: str | None = None,
    include_harness_header: bool | None = None,
    context_adapter: ContextAdapter | str | None = None,
    caller: str = "unregistered",
    allowed_tool_ids: list[str] | tuple[str, ...] | None = None,
    allow_model_fallback: bool = True,
    context_artifacts: list[dict] | None = None,
    context_symbol: str | None = None,
    evaluation: dict[str, object] | None = None,
) -> tuple[str | None, str | None]:
    """Call the model provider. Returns (content, error).

    Never raises — on any failure returns (None, error_message) so the agent can
    record a clean error and still complete its run.
    provider: explicit provider name (or None → use default_provider).
    """
    # #161 fix: preserve the provider NAME so complete_with_fallback keys health
    # tracking + cooldown + candidate-model resolution against the tier the
    # caller actually asked for (e.g. get_provider_for_tier("deep") →
    # "anthropic"), not cfg.models.default_provider. Without this, a tier
    # provider's cooldown state was being tracked under default_provider's
    # name, so a deep-tier failure would (a) never trip its own model's
    # cooldown, and (b) fall back through default_provider's candidate list —
    # not the tier's.
    provider_name = provider
    rendered_system, adapter_id, context_manifest = _prepare_context_adapter(
        system,
        include=include_harness_header,
        adapter=context_adapter,
    )
    messages = [{"role": "user", "content": user}]
    inference_call = build_inference_call(
        caller=caller,
        system=rendered_system,
        prompt_static_text=system,
        messages=messages,
        context_adapter_id=adapter_id,
        context_manifest=context_manifest,
        requested_provider=provider_name,
        max_tokens=max_tokens,
        allow_model_fallback=allow_model_fallback,
        allowed_tool_ids=allowed_tool_ids,
        evaluation=evaluation,
    )
    _record_call_started(inference_call, context_artifacts, context_symbol)
    try:
        provider_instance = get_provider(provider_name)
        resp = await complete_with_fallback(
            ModelRequest(
                messages=messages,
                system=rendered_system,
                max_tokens=max_tokens,
                inference=inference_call,
                allow_model_fallback=allow_model_fallback,
            ),
            provider=provider_instance,
            provider_name=provider_name,
        )
        _record_call_finished(inference_call, status="success", response=resp)
        _record_llm(True)
        return resp.content, None
    except BudgetExceededError:
        _record_call_finished(
            inference_call, status="error", error_type="llm_budget_exhausted"
        )
        _record_llm(False)
        return None, "llm_budget_exhausted"
    except Exception as e:
        detail = _exception_detail(e)
        _record_call_finished(
            inference_call, status="error", error_type=type(e).__name__
        )
        logger.warning("LLM call failed: %s", detail)
        _record_llm(False)
        return None, f"llm_unavailable: {detail}"


def _extract_json_object(text: str):
    """Extract the first valid JSON object from LLM response text.

    Handles: raw JSON, markdown code fences (```json ... ```), prose before/after
    the JSON block, and nested braces. Returns the parsed object or None.
    """
    # Strategy 1: try direct parse (fastest path for well-behaved models)
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 2: strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: brace-matching extraction — find the first '{' and match
    # its closing '}' accounting for nesting and string literals.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            if in_string:
                escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except (json.JSONDecodeError, TypeError):
                    return None
    return None


def _known_provider_envelope(value: dict) -> str | None:
    """Classify only fixed public protocol envelopes; never retain their values."""
    if value.get("object") == "chat.completion" and isinstance(
        value.get("choices"), list
    ):
        return "openai_chat_envelope"
    if value.get("object") == "response" and (
        isinstance(value.get("output"), list)
        or isinstance(value.get("output_text"), str)
    ):
        return "openai_responses_envelope"
    if (
        value.get("type") == "message"
        and value.get("role") == "assistant"
        and isinstance(value.get("content"), list)
    ):
        return "anthropic_message_envelope"
    return None


def _classify_structured_response(text: str, parsed: object) -> str:
    """Return a bounded content-free response-shape enum.

    The classifier deliberately records no body, value, arbitrary root key,
    length, or hash.  Known envelopes are recognized only by fixed public
    protocol discriminators.
    """
    stripped = text.strip()
    if not stripped:
        return "empty"
    try:
        root = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        if isinstance(parsed, dict):
            if re.search(r"```(?:json)?\s*\n?.*?```", stripped, re.DOTALL):
                return "markdown_json_object"
            return "embedded_json_object"
        return "non_json_text"
    if isinstance(root, dict):
        return _known_provider_envelope(root) or "json_object"
    if isinstance(root, list):
        return "json_array"
    if isinstance(root, str):
        return "json_string"
    if root is None:
        return "json_null"
    return "json_scalar"


def _classify_schema_validation_error(exc: BaseException) -> str:
    """Map validation failures to bounded codes without retaining values/keys."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            items = errors(include_input=False, include_url=False)
        except TypeError:
            try:
                items = errors()
            except Exception:  # noqa: BLE001 - classification must stay best effort
                items = []
        except Exception:  # noqa: BLE001 - classification must stay best effort
            items = []
        if isinstance(items, list) and items and isinstance(items[0], dict):
            item = items[0]
            raw_type = str(item.get("type") or "")
            code = _PYDANTIC_SCHEMA_ERROR_CODES.get(raw_type)
            if code is None:
                code = (
                    "wrong_type"
                    if raw_type.endswith(("_type", "_parsing"))
                    else "constraint_violation"
                    if raw_type
                    else "validation_failed"
                )
            return code

    message = str(exc).lower()
    mappings = (
        ("missing required field", "missing_required"),
        ("outside enum", "outside_enum"),
        ("expected ", "wrong_type"),
    )
    return next(
        (mapped for marker, mapped in mappings if marker in message),
        "constraint_violation" if message else "validation_failed",
    )


def _validate_json_schema_value(
    value: object,
    schema: dict,
    *,
    root: dict,
    path: str = "$",
) -> None:
    """Validate the JSON-Schema subset used by Aletheia's lightweight shims."""
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: object = root
        for part in ref[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                raise ValueError(f"{path}: unresolved schema reference")
            target = target[part]
        if not isinstance(target, dict):
            raise ValueError(f"{path}: invalid schema reference")
        _validate_json_schema_value(value, target, root=root, path=path)
        return

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: value does not match const")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is outside enum")

    for combinator in ("anyOf", "oneOf"):
        variants = schema.get(combinator)
        if isinstance(variants, list):
            matches = 0
            for variant in variants:
                try:
                    _validate_json_schema_value(value, variant, root=root, path=path)
                    matches += 1
                except (TypeError, ValueError):
                    pass
            if matches == 0 or (combinator == "oneOf" and matches != 1):
                raise ValueError(f"{path}: value does not satisfy {combinator}")
            return
    variants = schema.get("allOf")
    if isinstance(variants, list):
        for variant in variants:
            _validate_json_schema_value(value, variant, root=root, path=path)

    expected = schema.get("type")
    if isinstance(expected, list):
        errors = []
        for expected_type in expected:
            try:
                _validate_json_schema_value(
                    value, {**schema, "type": expected_type}, root=root, path=path
                )
                return
            except (TypeError, ValueError) as exc:
                errors.append(exc)
        raise ValueError(f"{path}: value does not match allowed types") from errors[-1]
    if expected == "null":
        if value is not None:
            raise ValueError(f"{path}: expected null")
        return
    if expected == "object" or "properties" in schema:
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected object")
        required = schema.get("required") or []
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"{path}: missing required field {missing[0]}")
        properties = schema.get("properties") or {}
        for name, child_schema in properties.items():
            if name in value and isinstance(child_schema, dict):
                _validate_json_schema_value(
                    value[name], child_schema, root=root, path=f"{path}.{name}"
                )
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(f"{path}: unexpected field {sorted(extras)[0]}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected array")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValueError(f"{path}: array is too short")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"{path}: array is too long")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_json_schema_value(
                    item, item_schema, root=root, path=f"{path}[{index}]"
                )
        return
    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path}: expected string")
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ValueError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ValueError(f"{path}: string is too long")
        return
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path}: expected integer")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path}: expected number")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path}: expected boolean")
        return
    if expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:  # type: ignore[operator]
            raise ValueError(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:  # type: ignore[operator]
            raise ValueError(f"{path}: value is above maximum")


def _validate_structured_payload(data: dict, schema: object, schema_dict: dict) -> None:
    validator = getattr(schema, "model_validate", None)
    if callable(validator):
        validator(data)
        return
    _validate_json_schema_value(data, schema_dict, root=schema_dict)


async def llm_structured(
    system: str,
    user: str,
    *,
    schema,
    max_tokens: int = 2048,
    provider: str | None = None,
    fallback_to_defaults: bool = False,
    include_harness_header: bool | None = None,
    context_adapter: ContextAdapter | str | None = None,
    caller: str = "unregistered.structured",
    allow_model_fallback: bool = True,
    context_artifacts: list[dict] | None = None,
    context_symbol: str | None = None,
    evaluation: dict[str, object] | None = None,
    deadline: float | None = None,
) -> tuple[dict | None, str | None]:
    """Call the provider's structured() and return (parsed_dict, error).

    Never raises — absorbs provider errors AND the JSON-parse failure in the
    provider impl, returning (None, error) so the caller can fall back cleanly.
    `schema` is a Pydantic model class (uses model_json_schema()).
    provider: explicit provider name (or None → use default_provider).
    fallback_to_defaults: if True and all retries fail, return a dict filled
        with schema defaults / None values instead of None. Use this only for
        non-critical paths where "best effort" is better than nothing.
    deadline: absolute monotonic deadline (loop.time()) for the entire call
        including all retry attempts.  When provided, takes precedence over the
        global _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC ceiling so that callers
        can enforce a tighter per-symbol or per-task budget without changing
        the global default.
    """
    # #161 fix (mirrors llm_complete): thread the tier's provider name into
    # complete_with_fallback so its fallback/health/cooldown keying matches the
    # provider actually in use, not cfg.models.default_provider.
    provider_name = provider
    schema_dict: dict = {}
    inference_call: InferenceCall | None = None
    inference_finished = False
    last_response = None
    last_response_shape: str | None = None
    last_schema_error_code: str | None = None

    def _failure(error: str) -> tuple[dict | None, str]:
        nonlocal inference_finished
        if inference_call is not None and not inference_finished:
            failure_diagnostics: dict[str, str] = {}
            if error.startswith(
                ("structured_parse_error", "structured_schema_invalid")
            ):
                if last_response_shape:
                    failure_diagnostics["response_shape"] = last_response_shape
                if error.startswith("structured_schema_invalid"):
                    if last_schema_error_code:
                        failure_diagnostics["schema_error_code"] = last_schema_error_code
            _record_call_finished(
                inference_call,
                status="error",
                response=last_response,
                parse_valid=not error.startswith("structured_parse_error"),
                schema_valid=False if error.startswith("structured_schema_invalid") else None,
                error_type=error.split(":", 1)[0],
                failure_diagnostics=failure_diagnostics or None,
            )
            inference_finished = True
        if fallback_to_defaults:
            return _schema_defaults_dict(schema_dict), f"{error}(fallback)"
        return None, error

    try:
        provider_instance = get_provider(provider_name)
        schema_dict = model_json_schema_payload(schema)
        schema_json = json.dumps(schema_dict)
        base_messages = [
            {"role": "user", "content": user},
            {
                "role": "user",
                "content": (
                    f"Respond with ONLY a raw JSON object matching this schema (no markdown fences, "
                    f"no explanation, no text before or after — just the JSON): {schema_json}"
                ),
            },
        ]
        # Prepend /no_think for Qwen3-series reasoning models: without it the model
        # exhausts the token budget on internal reasoning and returns empty text output.
        # Other models ignore this prefix safely.
        caller_system, adapter_id, context_manifest = _prepare_context_adapter(
            system,
            include=include_harness_header,
            adapter=context_adapter,
        )
        structured_system = f"/no_think\n{caller_system}" if caller_system else "/no_think"
        inference_call = build_inference_call(
            caller=caller,
            system=structured_system,
            prompt_static_text=f"/no_think\n{system}" if system else "/no_think",
            messages=base_messages,
            context_adapter_id=adapter_id,
            context_manifest=context_manifest,
            schema=schema,
            requested_provider=provider_name,
            temperature=0.3,
            max_tokens=max_tokens,
            allow_model_fallback=allow_model_fallback,
            evaluation=evaluation,
        )
        _record_call_started(inference_call, context_artifacts, context_symbol)
        loop = asyncio.get_running_loop()
        if deadline is None:
            deadline = loop.time() + _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC
        # Never exceed the global ceiling even when the caller passes a deadline.
        deadline = min(deadline, loop.time() + _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC)

        # 3 attempts with escalating token budget + corrective strategy:
        #   attempt 0: base max_tokens at temperature 0.3, standard prompt
        #   attempt 1: +50% tokens, temperature 0, stronger JSON-only prompt
        #   attempt 2: 2x tokens, temperature 0, repair-mode prompt (send back previous bad output)
        last_bad_output = ""
        last_err = "structured_parse_error: no valid JSON object found in response"
        max_attempts = 3
        for attempt in range(max_attempts):
            # Escalate token budget on each retry
            attempt_max_tokens = max_tokens
            if attempt == 1:
                attempt_max_tokens = int(max_tokens * 1.5)
            elif attempt >= 2:
                attempt_max_tokens = max_tokens * 2

            messages = list(base_messages)
            if attempt == 1:
                # Stronger corrective prompt + emphasize immediate JSON output
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "IMPORTANT: Your previous reply was invalid. "
                            "Output ONLY the JSON object now. "
                            "Do NOT include any thinking, explanation, or markdown. "
                            "Start your reply with '{' and end with '}'. Nothing else."
                        ),
                    }
                )
            elif attempt >= 2:
                # Final attempt: repair mode — show the model its previous bad output
                # and ask it to fix it. Simpler task than generating from scratch.
                repair_hint = last_bad_output[:500] if last_bad_output else "(empty)"
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"FIX THIS JSON. Your previous output was:\n"
                            f"```\n{repair_hint}\n```\n"
                            f"Reply with ONLY the corrected JSON. Start with '{{' and end with '}}'. "
                            f"No extra text, no explanation."
                        ),
                    }
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "LLM structured call exhausted %.0fs total wall-clock budget", _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC
                )
                _record_llm(False)
                return _failure("llm_unavailable: structured_timeout")
            try:
                resp = await asyncio.wait_for(
                    complete_with_fallback(
                        ModelRequest(
                            messages=messages,
                            system=structured_system,
                            temperature=0.3 if attempt == 0 else 0.0,
                            max_tokens=attempt_max_tokens,
                            inference=inference_call,
                            allow_model_fallback=allow_model_fallback,
                        ),
                        provider=provider_instance,
                        provider_name=provider_name,
                    ),
                    timeout=remaining,
                )
                last_response = resp
            except TimeoutError:
                logger.warning(
                    "LLM structured call exceeded %.0fs total wall-clock budget", _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC
                )
                _record_llm(False)
                return _failure("llm_unavailable: structured_timeout")
            content = (resp.content or "").strip()
            usage = (resp.raw or {}).get("usage", {}) if resp is not None else {}
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
            last_bad_output = content
            last_schema_error_code = None

            if not content:
                last_response_shape = "empty"
                if output_tokens > 0:
                    # Reasoning model exhausted budget on thinking phase → retry with more
                    if attempt < max_attempts - 1:
                        next_budget = int(max_tokens * (1.5 if attempt == 0 else 2))
                        logger.warning(
                            "LLM structured call: empty content with %d output_tokens "
                            "(reasoning model exhausted budget) — retrying with %d tokens "
                            "(attempt %d/%d)",
                            output_tokens,
                            next_budget,
                            attempt + 1,
                            max_attempts,
                        )
                        continue
                    logger.warning(
                        "LLM structured call: empty content with %d output_tokens — "
                        "reasoning model exhausted budget on reasoning phase",
                        output_tokens,
                    )
                    return _failure("structured_empty_content")
                if attempt < max_attempts - 1:
                    logger.warning(
                        "LLM structured call: empty content, no output_tokens — retrying (attempt %d/%d)",
                        attempt + 1,
                        max_attempts,
                    )
                    continue
                logger.warning("LLM structured call: empty content, no completion tokens")
                _record_llm(False)
                return _failure("llm_unavailable: empty_response")

            data = _extract_json_object(content)
            last_response_shape = _classify_structured_response(content, data)
            if isinstance(data, dict):
                try:
                    _validate_structured_payload(data, schema, schema_dict)
                except (TypeError, ValueError) as exc:
                    last_schema_error_code = _classify_schema_validation_error(exc)
                    last_err = f"structured_schema_invalid: {last_schema_error_code}"
                    if attempt < max_attempts - 1:
                        logger.warning(
                            "LLM structured call: schema validation failed (attempt %d/%d) — retrying",
                            attempt + 1,
                            max_attempts,
                        )
                        continue
                    break
                _record_call_finished(
                    inference_call,
                    status="success",
                    response=resp,
                    parse_valid=True,
                    schema_valid=True,
                )
                inference_finished = True
                _record_llm(True)
                return data, None
            if data is not None:
                last_err = "structured_non_dict"
                if attempt < max_attempts - 1:
                    logger.warning(
                        "LLM structured call: JSON value is not an object (attempt %d/%d) — retrying",
                        attempt + 1,
                        max_attempts,
                    )
                    continue
                break

            # Parse failed — check if output was truncated (near token limit)
            truncated = output_tokens > 0 and output_tokens >= attempt_max_tokens * 0.9
            if truncated and attempt < max_attempts - 1:
                logger.warning(
                    "LLM structured call: parse failure, output likely truncated "
                    "(%d/%d tokens used) — retrying with more tokens (attempt %d/%d)",
                    output_tokens,
                    attempt_max_tokens,
                    attempt + 1,
                    max_attempts,
                )
                continue

            # Parse failure, not obviously truncated → corrective retry
            if attempt < max_attempts - 1:
                logger.warning(
                    "LLM structured call: parse failure (attempt %d/%d), content_len=%d — retrying",
                    attempt + 1,
                    max_attempts,
                    len(content),
                )
                continue
            last_err = "structured_parse_error: no valid JSON object found in response"

        logger.warning("LLM structured call: no valid JSON after %d attempts", max_attempts)
        return _failure(last_err)
    except json.JSONDecodeError as e:
        # Bad JSON but the LLM did respond — not an outage, don't trip health counter
        logger.warning("LLM structured call: JSON parse failed: %s", e)
        return _failure(f"structured_parse_error: {e}")
    except Exception as e:
        detail = _exception_detail(e)
        logger.warning("LLM structured call failed: %s", detail)
        _record_llm(False)
        return _failure(f"llm_unavailable: {detail}")


def _schema_defaults_dict(schema_dict: dict) -> dict:
    """Build a safe default dict from a JSON schema's properties.

    Fills each field with None / empty list / empty dict depending on type.
    Used for fallback_to_defaults mode so callers get a structurally-valid
    (if content-free) result even when the LLM is unusable.
    """
    result: dict = {}
    props = schema_dict.get("properties", {})
    for key, prop in props.items():
        if "default" in prop:
            result[key] = prop["default"]
            continue
        typ = prop.get("type", "string")
        if isinstance(typ, list):
            typ = next((item for item in typ if item != "null"), "string")
        if typ == "array":
            result[key] = []
        elif typ == "object":
            result[key] = {}
        elif typ == "integer" or typ == "number":
            result[key] = 0
        elif typ == "boolean":
            result[key] = False
        else:
            result[key] = None
    return result


async def llm_stream(
    system: str,
    user: str,
    *,
    max_tokens: int = 800,
    provider: str | None = None,
    include_harness_header: bool | None = None,
    context_adapter: ContextAdapter | str | None = None,
    caller: str = "unregistered.stream",
) -> AsyncIterator[StreamChunk]:
    """Stream response chunks from the provider.

    Yields StreamChunk objects. On failure yields a single chunk with done=True.
    Callers should iterate with `async for chunk in llm_stream(...)`.
    provider: explicit provider name (or None → use default_provider).
    """
    provider_name = provider
    rendered_system, adapter_id, context_manifest = _prepare_context_adapter(
        system,
        include=include_harness_header,
        adapter=context_adapter,
    )
    messages = [{"role": "user", "content": user}]
    inference_call = build_inference_call(
        caller=caller,
        system=rendered_system,
        prompt_static_text=system,
        messages=messages,
        context_adapter_id=adapter_id,
        context_manifest=context_manifest,
        requested_provider=provider_name,
        max_tokens=max_tokens,
    )
    _record_call_started(inference_call, None, None)
    try:
        provider_instance = get_provider(provider_name)
        from aletheia.config.loader import load_config
        from aletheia.model_provider.broker import brokered_stream

        resolved_name = provider_name or load_config().models.default_provider
        async for chunk in brokered_stream(
            resolved_name,
            provider_instance,
            ModelRequest(
                messages=messages,
                system=rendered_system,
                max_tokens=max_tokens,
                inference=inference_call,
            ),
        ):
            yield chunk
        _record_call_finished(inference_call, status="success")
        _record_llm(True)
    except Exception as e:
        _record_call_finished(
            inference_call, status="error", error_type=type(e).__name__
        )
        logger.warning("LLM stream failed: %s", _exception_detail(e))
        _record_llm(False)
        yield StreamChunk(content="", done=True)


def _record_llm(success: bool) -> None:
    """Feed the health monitor's consecutive-failure counter. Never raises.

    Local import avoids any import-time cycle with agent_runtime.health.
    """
    try:
        from aletheia.agent_runtime.health import record_llm_success, record_llm_failure

        (record_llm_success if success else record_llm_failure)()
    except Exception:
        pass


# Per-run step counters. agent_run_steps.step_number must be monotonic within a run;
# keeping the counter in-process avoids a SELECT-MAX round-trip on every tool call.
# Lost on restart, which is fine: a step trace only matters for currently-running agents.
_step_counters: dict[str, int] = {}
_TELEMETRY_FLUSH_SIZE = 16
_TELEMETRY_MAX_PENDING_PER_RUN = 256
_telemetry_buffers: dict[str, list[dict]] = {}
_telemetry_lock = threading.Lock()
_telemetry_metrics = {"buffered": 0, "flushed": 0, "flush_failures": 0, "dropped": 0}


def _next_step(run_id: str) -> int:
    with _telemetry_lock:
        n = _step_counters.get(run_id, 0) + 1
        _step_counters[run_id] = n
        return n


def _release_run_counter(run_id: str) -> None:
    """Drop the per-run step counter after the run finishes (orchestrator calls this)."""
    flush_run_telemetry(run_id)
    with _telemetry_lock:
        _step_counters.pop(run_id, None)


def _buffer_telemetry(run_id: str, event: dict) -> None:
    should_flush = False
    with _telemetry_lock:
        pending = _telemetry_buffers.setdefault(run_id, [])
        if len(pending) >= _TELEMETRY_MAX_PENDING_PER_RUN:
            _telemetry_metrics["dropped"] += 1
            return
        event["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        pending.append(event)
        _telemetry_metrics["buffered"] += 1
        should_flush = len(pending) >= _TELEMETRY_FLUSH_SIZE
    if should_flush:
        flush_run_telemetry(run_id)


def flush_run_telemetry(run_id: str) -> int:
    """Flush one run's bounded telemetry buffer; retain it on transient failure."""

    with _telemetry_lock:
        events = _telemetry_buffers.pop(run_id, [])
    if not events:
        return 0
    try:
        from aletheia.storage.repositories import insert_agent_telemetry_batch

        count = insert_agent_telemetry_batch(events)
        with _telemetry_lock:
            _telemetry_metrics["flushed"] += count
        return count
    except Exception:
        logger.exception("Agent telemetry batch flush failed for run %s", run_id)
        with _telemetry_lock:
            _telemetry_metrics["flush_failures"] += 1
            existing = _telemetry_buffers.setdefault(run_id, [])
            available = max(0, _TELEMETRY_MAX_PENDING_PER_RUN - len(existing))
            existing[:0] = events[:available]
            _telemetry_metrics["dropped"] += max(0, len(events) - available)
        return 0


def agent_telemetry_snapshot() -> dict[str, int]:
    with _telemetry_lock:
        return {
            **_telemetry_metrics,
            "pending": sum(len(events) for events in _telemetry_buffers.values()),
            "runs_pending": len(_telemetry_buffers),
        }


def flush_all_agent_telemetry() -> int:
    with _telemetry_lock:
        run_ids = list(_telemetry_buffers)
    return sum(flush_run_telemetry(run_id) for run_id in run_ids)


def log_tool(
    run_id: str | None,
    tool_name: str,
    tool_class: str,
    permitted: bool,
    output: str | None = None,
    input_data: str | None = None,
    inference_call_id: str | None = None,
) -> None:
    """Best-effort tool-call logging (never raises).

    Writes only to `agent_tool_calls` — the structured tool ledger. The old
    duplicate write to `agent_run_steps` doubled every tool-call payload on
    disk; `/api/agent-runs/{id}/steps` now UNIONs both tables at read time so
    the reasoning trace continues to include tool events without persisting
    each one twice. Non-tool reasoning steps still go through `record_step`.
    """
    if not run_id:
        return
    _buffer_telemetry(
        run_id,
        {
            "kind": "tool",
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_class": tool_class,
            "permitted": permitted,
            "output_data": output,
            "input_data": input_data,
            "inference_call_id": inference_call_id or current_inference_call_id(),
        },
    )


def record_step(run_id: str | None, action: str, input_data: str | None = None, output_data: str | None = None) -> None:
    """Record a non-tool reasoning step (e.g. 'considered N lessons', 'skipped: neutral bias').

    Use this for decision points that aren't tool calls — context the reasoning trace
    needs to make sense. Best-effort, never raises.
    """
    if not run_id:
        return
    _buffer_telemetry(
        run_id,
        {
            "kind": "step",
            "run_id": run_id,
            "step_number": _next_step(run_id),
            "action": action,
            "input_data": input_data,
            "output_data": output_data,
        },
    )


def observer_llm_deadline(
    run_deadline_monotonic: float | None = None,
    per_symbol_cap_sec: float = 45.0,
) -> float:
    """Return an asyncio loop.time() deadline for a single observer LLM call.

    When run_deadline_monotonic is provided, caps at the tighter of
    per_symbol_cap_sec and the remaining run budget.  When omitted (or None),
    simply grants per_symbol_cap_sec from now.

    Use this helper in equity_observer, prediction_observer, and stock_observer
    to enforce the same per-call cap as market_observer without duplicating the
    monotonic-to-loop-time conversion.

    Args:
        run_deadline_monotonic: run-level deadline as time.monotonic().
            Pass None (default) when there is no run-level deadline to enforce.
        per_symbol_cap_sec: maximum wall-clock for this LLM call (default 45s).

    Returns:
        asyncio loop.time()-based absolute deadline for llm_structured(deadline=).
    """
    import asyncio
    import time as _time

    loop = asyncio.get_event_loop()
    if run_deadline_monotonic is None:
        return loop.time() + per_symbol_cap_sec
    remaining_s = max(1.0, run_deadline_monotonic - _time.monotonic())
    return loop.time() + min(per_symbol_cap_sec, remaining_s)
