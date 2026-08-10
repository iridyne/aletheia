"""Paired, paper-only context effectiveness evaluation.

The economics ledger receives only aggregate/content-free facts. Raw outputs are
written solely to an owner-selected review packet outside the repository.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from aletheia.economics.inference_calls import list_inference_calls
from aletheia.economics.schema import ensure_economics_schema

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEARNING_RUBRIC_VERSION = "learning-b-owner-v1"
_TECHNICAL_RUBRIC_VERSION = "technical-context-owner-v1"
_ABLATION_RUBRIC_VERSION = "context-ablation-owner-v1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_fixture(
    path: Path, *, treatment_field: str, fixture_label: str
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"fixture_id", "symbol", "market_context", treatment_field}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"{fixture_label} fixture missing field: {missing[0]}")
    if not all(isinstance(payload[key], str) and payload[key].strip() for key in required):
        raise ValueError(f"{fixture_label} fixture fields must be non-empty strings")
    return payload


def _review_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError:
        return resolved
    raise ValueError("raw context review packet must be outside the repository")


def _provider_preflight(provider_name: str | None) -> tuple[str | None, str | None]:
    from aletheia.config.loader import load_config

    cfg = load_config()
    resolved = str(provider_name or cfg.models.default_provider or "").strip()
    if not resolved:
        return None, "provider_not_configured"
    profile = cfg.models.providers.get(resolved)
    if profile is not None and not getattr(profile, "enabled", False):
        return None, "provider_profile_disabled"
    try:
        from aletheia.model_provider.router import get_provider

        provider = get_provider(resolved)
    except Exception as exc:
        return None, f"provider_unavailable:{type(exc).__name__}"
    model = str(getattr(provider, "model", "") or "").strip()
    if not model:
        return None, "provider_model_not_configured"
    return resolved, None


def _usage_for_call(call_id: str) -> dict[str, Any]:
    db = ensure_economics_schema()
    rows = db.execute(
        "SELECT model_or_tool,status,usage_json FROM runtime_cost_events "
        "WHERE event_type='llm' AND call_id=? ORDER BY attempt_index",
        (call_id,),
    ).fetchall()
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    models: list[str] = []
    usage_available_attempts = 0
    for row in rows:
        models.append(str(row["model_or_tool"] or ""))
        try:
            normalized = json.loads(row["usage_json"] or "{}").get("normalized") or {}
        except (TypeError, json.JSONDecodeError):
            normalized = {}
        input_tokens = normalized.get("input_tokens")
        output_tokens = normalized.get("output_tokens")
        usage_available = all(
            isinstance(value, (int, float)) and value >= 0
            for value in (input_tokens, output_tokens)
        )
        if usage_available:
            usage_available_attempts += 1
        for field in ("input_tokens", "output_tokens"):
            value = normalized.get(field)
            if isinstance(value, (int, float)) and value >= 0:
                totals[field] += int(value)
        total_tokens = normalized.get("total_tokens")
        if isinstance(total_tokens, (int, float)) and total_tokens >= 0:
            totals["total_tokens"] += int(total_tokens)
        elif usage_available:
            totals["total_tokens"] += int(input_tokens) + int(output_tokens)
    return {
        **totals,
        "physical_attempts": len(rows),
        "usage_available_attempts": usage_available_attempts,
        "models": models,
        "all_attempts_succeeded": bool(rows) and all(row["status"] == "success" for row in rows),
    }


def _pair_metrics(pair_id: str) -> dict[str, Any]:
    calls = list_inference_calls(evaluation_pair_id=pair_id, days=3650, limit=10)
    by_arm: dict[str, dict[str, Any]] = {}
    for call in calls:
        metadata = call.get("metadata") or {}
        arm = metadata.get("evaluation_arm")
        if arm not in {"control", "treatment"}:
            continue
        usage = _usage_for_call(call["logical_call_id"])
        final_model = call.get("final_model") or (usage["models"][-1] if usage["models"] else None)
        by_arm[arm] = {
            "logical_call_id": call["logical_call_id"],
            "caller": call.get("caller"),
            "blind_label": metadata.get("blind_label"),
            "fixture_id": metadata.get("fixture_id"),
            "modality_id": metadata.get("modality_id"),
            "status": call["status"],
            "parse_valid": call["parse_valid"],
            "schema_valid": call["schema_valid"],
            "retry_count": call["retry_count"],
            "fallback_used": bool(call["fallback_used"]),
            "final_provider": call.get("final_provider") or call.get("requested_provider"),
            "final_model": final_model,
            **usage,
        }
    valid = set(by_arm) == {"control", "treatment"}
    reasons: list[str] = []
    if valid:
        for arm, metrics in by_arm.items():
            if metrics["status"] != "success" or metrics["schema_valid"] != 1:
                reasons.append(f"{arm}_not_schema_valid")
            if metrics["fallback_used"]:
                reasons.append(f"{arm}_fallback_used")
            if metrics["physical_attempts"] < 1:
                reasons.append(f"{arm}_missing_attempt_telemetry")
            elif metrics["usage_available_attempts"] != metrics["physical_attempts"]:
                reasons.append(f"{arm}_incomplete_token_telemetry")
        identities = {
            (metrics["final_provider"], metrics["final_model"])
            for metrics in by_arm.values()
        }
        if len(identities) != 1:
            reasons.append("provider_or_model_drift")
    else:
        reasons.append("missing_pair_arm")
    return {"valid_pair": valid and not reasons, "invalid_reasons": reasons, "arms": by_arm}


async def _run_context_pair(
    *,
    fixture_path: Path,
    review_output: Path,
    provider_name: str | None = None,
    treatment_field: str,
    treatment_heading: str,
    fixture_label: str,
    pair_prefix: str,
    protocol: str,
    rubric_version: str,
    treatment_artifact_kind: str,
    treatment_artifact_suffix: str,
    treatment_artifact_source: str,
    treatment_trust_class: str,
    modality_id: str | None = None,
) -> dict[str, Any]:
    """Run one frozen control/treatment pair and create a blinded review packet."""
    fixture = _load_fixture(
        fixture_path,
        treatment_field=treatment_field,
        fixture_label=fixture_label,
    )
    review_path = _review_path(review_output)
    resolved_provider, blocker = _provider_preflight(provider_name)
    if blocker:
        return {
            "status": "blocked",
            "blocker": blocker,
            "fixture_id": fixture["fixture_id"],
            "effectiveness_conclusion": None,
        }

    from aletheia.agents.helpers import llm_structured
    from aletheia.agents.market_observer import MarketRead, _SYSTEM_PROMPT

    pair_id = pair_prefix + uuid4().hex[:16]
    arms = ["control", "treatment"]
    secrets.SystemRandom().shuffle(arms)
    label_by_arm = {arm: label for arm, label in zip(arms, ("A", "B"), strict=True)}
    outputs_by_label: dict[str, dict[str, Any]] = {}
    call_errors: dict[str, str] = {}

    for arm in arms:
        is_technical_pair = treatment_field == "technical_context"
        treatment = fixture[treatment_field] if arm == "treatment" else ""
        rendered_context = fixture["market_context"]
        if treatment:
            rendered_context += f"\n\n## {treatment_heading}\n" + treatment
        user = (
            f"/no_think\nMarket data for {fixture['symbol']}:\n{rendered_context}"
            "\n\nReturn the market read JSON."
        )
        evaluation = {
            "evaluation_pair_id": pair_id,
            "evaluation_arm": arm,
            "fixture_id": fixture["fixture_id"],
            "blind_label": label_by_arm[arm],
        }
        if modality_id:
            evaluation["modality_id"] = modality_id
        artifacts = [
            {
                "kind": "environment",
                "stable_id": f"fixture:{fixture['fixture_id']}:market",
                "source": "frozen_evaluation_fixture",
                "selected_reason": "paired_market_context",
                "trust_class": "first_party_fixture",
                "freshness_status": "unknown",
                "rendered_chars": len(fixture["market_context"]),
            },
            {
                "kind": treatment_artifact_kind,
                "stable_id": (
                    f"technical-context-v1:fixture:{fixture['fixture_id']}"
                    if is_technical_pair
                    else f"fixture:{fixture['fixture_id']}:{treatment_artifact_suffix}"
                ),
                "source": treatment_artifact_source,
                "selected_reason": "paired_treatment" if treatment else "control_omission",
                "trust_class": treatment_trust_class,
                "freshness_status": "unknown",
                "rendered_chars": len(treatment),
            },
        ]
        call_kwargs = {
            "system": _SYSTEM_PROMPT,
            "user": user,
            "schema": MarketRead,
            "max_tokens": int(fixture.get("max_tokens") or 2048),
            "provider": resolved_provider,
            "allow_model_fallback": False,
            "context_artifacts": artifacts,
            "context_symbol": fixture["symbol"],
            "evaluation": evaluation,
            "reasoning_effort": "none",
            "context_adapter": (
                "technical-context-v1"
                if is_technical_pair and arm == "treatment"
                else "none"
            ),
        }
        if treatment_field == "learning_context" and arm == "control":
            result, error = await llm_structured(
                **call_kwargs, caller="evaluation.learning_b.control"
            )
        elif treatment_field == "learning_context":
            result, error = await llm_structured(
                **call_kwargs, caller="evaluation.learning_b.treatment"
            )
        elif treatment_field == "technical_context" and arm == "control":
            result, error = await llm_structured(
                **call_kwargs, caller="evaluation.technical_context.control"
            )
        elif treatment_field == "technical_context":
            result, error = await llm_structured(
                **call_kwargs, caller="evaluation.technical_context.treatment"
            )
        elif arm == "control":
            result, error = await llm_structured(
                **call_kwargs, caller="evaluation.context_ablation.control"
            )
        else:
            result, error = await llm_structured(
                **call_kwargs, caller="evaluation.context_ablation.treatment"
            )
        if error or result is None:
            call_errors[arm] = error or "empty_result"
        else:
            outputs_by_label[label_by_arm[arm]] = result

    metrics = _pair_metrics(pair_id)
    if call_errors or not metrics["valid_pair"]:
        return {
            "status": "invalid_pair",
            "evaluation_pair_id": pair_id,
            "fixture_id": fixture["fixture_id"],
            "modality_id": modality_id,
            "errors": call_errors,
            "metrics": metrics,
            "effectiveness_conclusion": None,
        }

    packet = {
        "protocol": protocol,
        "evaluation_pair_id": pair_id,
        "fixture_id": fixture["fixture_id"],
        "modality_id": modality_id,
        "rubric_version": rubric_version,
        "instructions": (
            "Score A and B independently. Do not inspect the economics database until "
            "both scores are fixed. Delete this packet after importing aggregate scores."
        ),
        "rubric": fixture.get("rubric")
        or {
            "quality_score": "integer 1-5 for fixture-grounded usefulness",
            "grounded_use": "yes|no|not_applicable",
            "context_harm": "true when stale/wrong anchoring harms the answer",
        },
        "presentations": [
            {"label": label, "output": outputs_by_label[label]} for label in ("A", "B")
        ],
    }
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    review_path.chmod(0o600)
    return {
        "status": "awaiting_owner_review",
        "evaluation_pair_id": pair_id,
        "fixture_id": fixture["fixture_id"],
        "modality_id": modality_id,
        "review_packet": str(review_path),
        "metrics": metrics,
        "effectiveness_conclusion": None,
    }


async def run_learning_b_pair(
    *,
    fixture_path: Path,
    review_output: Path,
    provider_name: str | None = None,
) -> dict[str, Any]:
    return await _run_context_pair(
        fixture_path=fixture_path,
        review_output=review_output,
        provider_name=provider_name,
        treatment_field="learning_context",
        treatment_heading="Learning Advisory",
        fixture_label="Learning B",
        pair_prefix="learning_b_",
        protocol="learning-b-owner-blind-review-v1",
        rubric_version=_LEARNING_RUBRIC_VERSION,
        treatment_artifact_kind="learning",
        treatment_artifact_suffix="learning",
        treatment_artifact_source="owner_authorized_evaluation_fixture",
        treatment_trust_class="learned_advisory",
    )


async def run_technical_context_pair(
    *,
    fixture_path: Path,
    review_output: Path,
    provider_name: str | None = None,
) -> dict[str, Any]:
    return await _run_context_pair(
        fixture_path=fixture_path,
        review_output=review_output,
        provider_name=provider_name,
        treatment_field="technical_context",
        treatment_heading="Technical Context",
        fixture_label="Technical context",
        pair_prefix="technical_context_",
        protocol="technical-context-owner-blind-review-v1",
        rubric_version=_TECHNICAL_RUBRIC_VERSION,
        # Reuse the current durable artifact vocabulary; the stable ID retains
        # the semantic distinction without a schema migration.
        treatment_artifact_kind="environment",
        treatment_artifact_suffix="technical",
        treatment_artifact_source="frozen_technical_evaluation_fixture",
        treatment_trust_class="deterministic_derived_fact",
    )


async def run_context_ablation_pair(
    *,
    fixture_path: Path,
    modality_id: str,
    review_output: Path,
    provider_name: str | None = None,
) -> dict[str, Any]:
    """Compare one frozen base context with the same case plus one modality."""
    modality = modality_id.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", modality):
        raise ValueError(f"invalid context modality id: {modality_id}")
    return await _run_context_pair(
        fixture_path=fixture_path,
        review_output=review_output,
        provider_name=provider_name,
        treatment_field="modality_context",
        treatment_heading=modality.replace("_", " ").title(),
        fixture_label="Context ablation",
        pair_prefix="context_ablation_",
        protocol="context-ablation-owner-blind-review-v1",
        rubric_version=_ABLATION_RUBRIC_VERSION,
        treatment_artifact_kind=(
            modality if modality in {"news", "learning"} else "environment"
        ),
        treatment_artifact_suffix=modality,
        treatment_artifact_source="owner_authorized_evaluation_fixture",
        treatment_trust_class="first_party_fixture",
        modality_id=modality,
    )


def _record_context_pair_review(
    *,
    pair_id: str,
    scores_path: Path,
    rubric_version: str,
    pair_label: str,
    pair_prefix: str,
    expected_callers: dict[str, str],
    reject_ungrounded_treatment: bool = False,
) -> dict[str, Any]:
    """Persist aggregate blind scores; never copy review-packet outputs."""
    payload = json.loads(scores_path.read_text(encoding="utf-8"))
    if not pair_id.startswith(pair_prefix):
        raise ValueError(f"{pair_label} pair identity mismatch")
    if payload.get("rubric_version") != rubric_version:
        raise ValueError(f"{pair_label} rubric version mismatch")
    scores = payload.get("scores") or {}
    metrics = _pair_metrics(pair_id)
    if not metrics["valid_pair"]:
        raise ValueError(f"{pair_label} pair is invalid")
    if any(
        metrics["arms"][arm].get("caller") != expected_callers[arm]
        for arm in ("control", "treatment")
    ):
        raise ValueError(f"{pair_label} caller identity mismatch")
    by_label = {
        arm_metrics["blind_label"]: arm
        for arm, arm_metrics in metrics["arms"].items()
    }
    if set(by_label) != {"A", "B"} or set(scores) != {"A", "B"}:
        raise ValueError("scores must contain exactly blinded labels A and B")

    normalized: dict[str, dict[str, Any]] = {}
    for label, arm in by_label.items():
        item = scores[label]
        quality = int(item["quality_score"])
        grounded = str(item["grounded_use"])
        harm = bool(item["context_harm"])
        if not 1 <= quality <= 5:
            raise ValueError("quality_score must be between 1 and 5")
        if grounded not in {"yes", "no", "not_applicable"}:
            raise ValueError("grounded_use must be yes, no, or not_applicable")
        normalized[arm] = {
            "quality_score": quality,
            "grounded_use": grounded,
            "context_harm": harm,
        }

    control = normalized["control"]
    treatment = normalized["treatment"]
    delta = treatment["quality_score"] - control["quality_score"]
    gate_failures: list[str] = []
    if reject_ungrounded_treatment:
        if treatment["context_harm"]:
            gate_failures.append("treatment_context_harm")
        if treatment["grounded_use"] != "yes":
            gate_failures.append("treatment_not_grounded")
    verdict = (
        "control_better"
        if gate_failures
        else "treatment_better" if delta > 0
        else "control_better" if delta < 0
        else "tie"
    )
    calls = metrics["arms"]
    fixture_id = calls["control"].get("fixture_id") or "unknown"
    modality_id = calls["control"].get("modality_id")
    db = ensure_economics_schema()
    was_in_tx = db.in_transaction
    db.execute(
        """INSERT INTO llm_context_pair_reviews (
            evaluation_pair_id,fixture_id,rubric_version,reviewer,
            control_call_id,treatment_call_id,control_quality_score,
            treatment_quality_score,control_grounded_use,treatment_grounded_use,
            control_context_harm,treatment_context_harm,verdict,reviewed_at,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            pair_id,
            fixture_id,
            rubric_version,
            str(payload.get("reviewer") or "owner")[:80],
            calls["control"]["logical_call_id"],
            calls["treatment"]["logical_call_id"],
            control["quality_score"],
            treatment["quality_score"],
            control["grounded_use"],
            treatment["grounded_use"],
            int(control["context_harm"]),
            int(treatment["context_harm"]),
            verdict,
            _utcnow(),
            json.dumps(
                {
                    "quality_delta": delta,
                    "gate_failures": gate_failures,
                    "modality_id": modality_id,
                },
                sort_keys=True,
            ),
        ),
    )
    if not was_in_tx:
        db.commit()
    return {
        "status": "review_recorded",
        "evaluation_pair_id": pair_id,
        "fixture_id": fixture_id,
        "modality_id": modality_id,
        "quality_delta": delta,
        "verdict": verdict,
        "gate_failures": gate_failures,
        "control": control,
        "treatment": treatment,
    }


def record_learning_b_review(*, pair_id: str, scores_path: Path) -> dict[str, Any]:
    return _record_context_pair_review(
        pair_id=pair_id,
        scores_path=scores_path,
        rubric_version=_LEARNING_RUBRIC_VERSION,
        pair_label="Learning B",
        pair_prefix="learning_b_",
        expected_callers={
            "control": "evaluation.learning_b.control",
            "treatment": "evaluation.learning_b.treatment",
        },
    )


def record_technical_context_review(
    *, pair_id: str, scores_path: Path
) -> dict[str, Any]:
    return _record_context_pair_review(
        pair_id=pair_id,
        scores_path=scores_path,
        rubric_version=_TECHNICAL_RUBRIC_VERSION,
        pair_label="Technical context",
        pair_prefix="technical_context_",
        expected_callers={
            "control": "evaluation.technical_context.control",
            "treatment": "evaluation.technical_context.treatment",
        },
        reject_ungrounded_treatment=True,
    )


def record_context_ablation_review(
    *, pair_id: str, scores_path: Path
) -> dict[str, Any]:
    return _record_context_pair_review(
        pair_id=pair_id,
        scores_path=scores_path,
        rubric_version=_ABLATION_RUBRIC_VERSION,
        pair_label="Context ablation",
        pair_prefix="context_ablation_",
        expected_callers={
            "control": "evaluation.context_ablation.control",
            "treatment": "evaluation.context_ablation.treatment",
        },
        reject_ungrounded_treatment=True,
    )


def summarize_learning_b_reviews() -> dict[str, Any]:
    db = ensure_economics_schema()
    rows = db.execute(
        "SELECT * FROM llm_context_pair_reviews "
        "WHERE substr(evaluation_pair_id,1,11)='learning_b_' ORDER BY reviewed_at"
    ).fetchall()
    deltas = []
    for row in rows:
        deltas.append(int(row["treatment_quality_score"]) - int(row["control_quality_score"]))
    return {
        "reviewed_pairs": len(rows),
        "treatment_better": sum(delta > 0 for delta in deltas),
        "control_better": sum(delta < 0 for delta in deltas),
        "ties": sum(delta == 0 for delta in deltas),
        "mean_quality_delta": (sum(deltas) / len(deltas)) if deltas else None,
        "treatment_context_harm": sum(int(row["treatment_context_harm"]) for row in rows),
        "effectiveness_conclusion": None,
    }
