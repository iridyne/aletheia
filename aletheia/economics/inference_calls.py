"""Durable, content-free records for logical LLM inference calls."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.economics.schema import ensure_economics_schema
from aletheia.model_provider.inference_contract import InferenceCall

_SCHEMA_VERSION = "llm-inference-call-v1"

_SAFE_RESPONSE_SHAPES = frozenset(
    {
        "empty",
        "json_object",
        "json_array",
        "json_string",
        "json_null",
        "json_scalar",
        "markdown_json_object",
        "embedded_json_object",
        "non_json_text",
        "openai_chat_envelope",
        "openai_responses_envelope",
        "anthropic_message_envelope",
    }
)
_SAFE_SCHEMA_ERROR_CODES = frozenset(
    {
        "missing_required",
        "outside_enum",
        "wrong_type",
        "constraint_violation",
        "validation_failed",
    }
)
_STRUCTURED_ATTEMPT_OUTCOMES = frozenset(
    {
        "accepted",
        "provider_error",
        "timeout",
        "cancelled",
        "empty_content",
        "non_object_json",
        "parse_error",
        "truncated_output",
        "schema_invalid",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _runtime_identity(call: InferenceCall) -> tuple[str | None, str | None]:
    environment = call.environment
    return environment.get("agent_run_id"), environment.get("runtime_session_id")


def _safe_failure_diagnostics(value: dict[str, str] | None) -> dict[str, str]:
    """Allow only fixed content-free enums into the durable inference envelope."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, str] = {}
    response_shape = value.get("response_shape")
    if response_shape in _SAFE_RESPONSE_SHAPES:
        safe["response_shape"] = response_shape
    schema_error_code = value.get("schema_error_code")
    if schema_error_code in _SAFE_SCHEMA_ERROR_CODES:
        safe["schema_error_code"] = schema_error_code
    return safe


def record_inference_started(call: InferenceCall, *, started_at: str | None = None) -> bool:
    """Insert one logical call. Measurement failure never blocks inference."""
    try:
        run_id, runtime_session_id = _runtime_identity(call)
        now = started_at or _utcnow()
        db = ensure_economics_schema()
        was_in_tx = db.in_transaction
        db.execute(
            """INSERT OR IGNORE INTO llm_inference_calls (
                logical_call_id,schema_version,run_id,runtime_session_id,caller,
                prompt_template_id,prompt_version,prompt_static_sha256,
                rendered_prompt_sha256,context_adapter_id,tool_policy_id,
                allowed_tools_sha256,output_schema_id,output_schema_sha256,
                requested_provider,temperature,max_tokens,allow_model_fallback,
                status,parse_valid,schema_valid,attempt_count,retry_count,
                fallback_used,final_provider,final_model,error_type,started_at,
                finished_at,metadata_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                call.logical_call_id,
                _SCHEMA_VERSION,
                run_id,
                runtime_session_id,
                call.caller.caller_id,
                call.caller.prompt_template_id,
                call.caller.prompt_version,
                call.prompt_static_sha256,
                call.rendered_prompt_sha256,
                call.context_adapter_id,
                call.tool_policy_id,
                call.allowed_tools_sha256,
                call.output_schema_id,
                call.output_schema_sha256,
                call.requested_provider,
                call.temperature,
                call.max_tokens,
                int(call.allow_model_fallback),
                "running",
                None,
                None,
                0,
                0,
                None,
                None,
                None,
                None,
                now,
                None,
                _json(call.storage_metadata()),
                now,
            ),
        )
        if not was_in_tx:
            db.commit()
        return True
    except Exception:
        return False


def record_structured_attempt_outcome(
    logical_call_id: str,
    structured_attempt_index: int,
    outcome: str,
    *,
    created_at: str | None = None,
) -> bool:
    """Persist one content-free outer structured-attempt result, best effort."""
    if outcome not in _STRUCTURED_ATTEMPT_OUTCOMES or structured_attempt_index < 0:
        return False
    try:
        db = ensure_economics_schema()
        was_in_tx = db.in_transaction
        db.execute(
            """INSERT OR IGNORE INTO llm_structured_attempt_outcomes (
                logical_call_id,structured_attempt_index,outcome,created_at
            ) VALUES (?,?,?,?)""",
            (logical_call_id, structured_attempt_index, outcome, created_at or _utcnow()),
        )
        if not was_in_tx:
            db.commit()
        return True
    except Exception:
        return False


def record_inference_finished(
    call: InferenceCall,
    *,
    status: str,
    response: Any = None,
    parse_valid: bool | None = None,
    schema_valid: bool | None = None,
    error_type: str | None = None,
    failure_diagnostics: dict[str, str] | None = None,
    finished_at: str | None = None,
) -> bool:
    """Finalize a logical call without persisting model output content."""
    if status not in {"success", "error"}:
        raise ValueError(f"invalid logical inference status: {status}")
    try:
        fallback: dict[str, Any] = {}
        raw = getattr(response, "raw", None)
        if isinstance(raw, dict):
            candidate = raw.get("aletheia_model_fallback")
            if isinstance(candidate, dict):
                fallback = candidate
        attempt_count = call.attempt_count
        db = ensure_economics_schema()
        was_in_tx = db.in_transaction
        safe_diagnostics = _safe_failure_diagnostics(failure_diagnostics)
        metadata_json: str | None = None
        if status == "error" and safe_diagnostics:
            row = db.execute(
                "SELECT metadata_json FROM llm_inference_calls WHERE logical_call_id=?",
                (call.logical_call_id,),
            ).fetchone()
            try:
                metadata = json.loads(row["metadata_json"] or "{}") if row else {}
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["structured_failure"] = safe_diagnostics
            metadata_json = _json(metadata)
        db.execute(
            """UPDATE llm_inference_calls SET
                status=?,parse_valid=?,schema_valid=?,attempt_count=?,retry_count=?,
                fallback_used=?,final_provider=?,final_model=?,error_type=?,finished_at=?,
                metadata_json=COALESCE(?, metadata_json)
               WHERE logical_call_id=?""",
            (
                status,
                None if parse_valid is None else int(parse_valid),
                None if schema_valid is None else int(schema_valid),
                attempt_count,
                max(0, attempt_count - 1),
                None
                if not fallback
                else int(bool(fallback.get("fallback_used"))),
                fallback.get("provider") or call.requested_provider,
                fallback.get("final_model") or getattr(response, "model", None),
                str(error_type)[:160] if error_type else None,
                finished_at or _utcnow(),
                metadata_json,
                call.logical_call_id,
            ),
        )
        if not was_in_tx:
            db.commit()
        return True
    except Exception:
        return False


def list_inference_calls(
    *,
    days: int = 7,
    caller: str | None = None,
    evaluation_pair_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return bounded content-free logical calls for audit/evaluation."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    where = ["started_at >= ?"]
    params: list[Any] = [cutoff]
    if caller:
        where.append("caller = ?")
        params.append(caller)
    if evaluation_pair_id:
        where.append(
            "json_valid(metadata_json) AND json_extract(metadata_json,'$.evaluation_pair_id') = ?"
        )
        params.append(evaluation_pair_id)
    params.append(max(1, min(int(limit), 5_000)))
    db = ensure_economics_schema()
    rows = db.execute(
        "SELECT * FROM llm_inference_calls WHERE "
        + " AND ".join(where)
        + " ORDER BY started_at DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["metadata"] = {}
        result.append(item)
    return result
