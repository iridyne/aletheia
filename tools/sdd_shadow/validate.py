"""Deterministic validation for bounded Spec Kit shadow artifacts.

This module intentionally uses only the Python standard library. Agent output is
untrusted input: malformed, incomplete, oversized, or ambiguous JSON is rejected
before it can become workflow context.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MAX_ARTIFACT_BYTES = 256 * 1024
MAX_FINDINGS = 100
MAX_LIST_ITEMS = 200
MAX_TEXT_LENGTH = 4_000

PRECHANGE_STAGES = (
    "authority_resolution",
    "clarification",
    "transaction_durability",
    "concurrency_lifetime",
    "entrypoint",
    "failure_semantics",
    "verification",
    "synthesis",
)
CONVERGENCE_STAGES = (
    "contract_implementation",
    "authority_alignment",
    "specclaim_coverage",
    "scope_entrypoints",
    "failure_recovery",
    "review_evidence",
    "qualification",
    "synthesis",
)

_REQUIRED_KEYS = {
    "stage",
    "verdict",
    "findings",
    "blockers",
    "clarifications",
    "dependencies",
    "non_goals_at_risk",
    "verification_obligations",
    "unknowns",
}
_FINDING_KEYS = {"id", "severity", "class", "evidence_refs", "rationale"}
_ALLOWED_VERDICTS = {
    "GO",
    "HOLD_FOR_CLARIFICATION",
    "BLOCKED_BY_DEPENDENCY",
    "ADVISORY",
    "PASS",
    "HOLD",
    "BLOCKED",
}
_ALLOWED_SEVERITIES = {"info", "low", "medium", "high", "critical"}


class ValidationError(ValueError):
    """Raised when an Agent artifact does not satisfy the bounded contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constants(value: str) -> None:
    raise ValidationError(f"non-finite JSON constant is not allowed: {value}")


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    if not value.strip():
        raise ValidationError(f"{field} must not be empty")
    if len(value) > MAX_TEXT_LENGTH:
        raise ValidationError(f"{field} exceeds {MAX_TEXT_LENGTH} characters")
    return value


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    if len(value) > MAX_LIST_ITEMS:
        raise ValidationError(f"{field} exceeds {MAX_LIST_ITEMS} items")
    return [_text(item, field=f"{field}[{index}]") for index, item in enumerate(value)]


def _finding(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"findings[{index}] must be an object")
    if set(value) != _FINDING_KEYS:
        missing = sorted(_FINDING_KEYS - set(value))
        extra = sorted(set(value) - _FINDING_KEYS)
        detail = []
        if missing:
            detail.append(f"missing {','.join(missing)}")
        if extra:
            detail.append(f"unexpected {','.join(extra)}")
        raise ValidationError(f"findings[{index}] shape invalid ({'; '.join(detail)})")
    finding_id = _text(value["id"], field=f"findings[{index}].id")
    if any(character.isspace() for character in finding_id):
        raise ValidationError(f"findings[{index}].id must not contain whitespace")
    severity = _text(value["severity"], field=f"findings[{index}].severity").lower()
    if severity not in _ALLOWED_SEVERITIES:
        raise ValidationError(f"findings[{index}].severity is not supported: {severity}")
    finding_class = _text(value["class"], field=f"findings[{index}].class")
    evidence_refs = _string_list(value["evidence_refs"], field=f"findings[{index}].evidence_refs")
    rationale = _text(value["rationale"], field=f"findings[{index}].rationale")
    return {
        "id": finding_id,
        "severity": severity,
        "class": finding_class,
        "evidence_refs": evidence_refs,
        "rationale": rationale,
    }


def validate_artifact(data: Any, *, expected_stage: str | None = None) -> dict[str, Any]:
    """Validate and return a normalized bounded artifact.

    Unknown top-level keys and malformed nested values are rejected so later
    stages cannot accidentally consume model-specific, unreviewed data.
    """

    if not isinstance(data, dict):
        raise ValidationError("artifact must be a JSON object")
    if set(data) != _REQUIRED_KEYS:
        missing = sorted(_REQUIRED_KEYS - set(data))
        extra = sorted(set(data) - _REQUIRED_KEYS)
        detail = []
        if missing:
            detail.append(f"missing {','.join(missing)}")
        if extra:
            detail.append(f"unexpected {','.join(extra)}")
        raise ValidationError(f"artifact shape invalid ({'; '.join(detail)})")

    stage = _text(data["stage"], field="stage")
    allowed_stages = set(PRECHANGE_STAGES) | set(CONVERGENCE_STAGES)
    if stage not in allowed_stages:
        raise ValidationError(f"unsupported stage: {stage}")
    if expected_stage is not None and stage != expected_stage:
        raise ValidationError(f"expected stage {expected_stage}, got {stage}")

    verdict = _text(data["verdict"], field="verdict")
    if verdict not in _ALLOWED_VERDICTS:
        raise ValidationError(f"unsupported verdict: {verdict}")
    findings = data["findings"]
    if not isinstance(findings, list):
        raise ValidationError("findings must be a list")
    if len(findings) > MAX_FINDINGS:
        raise ValidationError(f"findings exceeds {MAX_FINDINGS} items")

    return {
        "stage": stage,
        "verdict": verdict,
        "findings": [_finding(item, index) for index, item in enumerate(findings)],
        "blockers": _string_list(data["blockers"], field="blockers"),
        "clarifications": _string_list(data["clarifications"], field="clarifications"),
        "dependencies": _string_list(data["dependencies"], field="dependencies"),
        "non_goals_at_risk": _string_list(data["non_goals_at_risk"], field="non_goals_at_risk"),
        "verification_obligations": _string_list(
            data["verification_obligations"], field="verification_obligations"
        ),
        "unknowns": _string_list(data["unknowns"], field="unknowns"),
    }


def load_json(path: Path) -> Any:
    """Read one bounded JSON document with duplicate/non-finite rejection."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValidationError(f"cannot read artifact {path}: {exc}") from exc
    if size <= 0:
        raise ValidationError(f"artifact is empty: {path}")
    if size > MAX_ARTIFACT_BYTES:
        raise ValidationError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"cannot decode artifact {path}: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constants,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValidationError(f"invalid JSON artifact {path}: {exc}") from exc


def validate_artifact_file(path: Path, *, expected_stage: str | None = None) -> dict[str, Any]:
    """Validate one JSON artifact file and return its normalized value."""

    return validate_artifact(load_json(path), expected_stage=expected_stage)


def stages_for_phase(phase: str) -> tuple[str, ...]:
    if phase == "prechange":
        return PRECHANGE_STAGES
    if phase == "convergence":
        return CONVERGENCE_STAGES
    raise ValidationError(f"unsupported phase: {phase}")


def validate_artifacts(artifact_dir: Path, *, phase: str) -> dict[str, dict[str, Any]]:
    """Validate exactly one artifact per stage; reject missing and extra JSON files."""

    if not artifact_dir.is_dir():
        raise ValidationError(f"artifact directory is missing: {artifact_dir}")
    stages = stages_for_phase(phase)
    expected_names = {f"{stage}.json" for stage in stages}
    actual_names = {path.name for path in artifact_dir.glob("*.json")}
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    if missing:
        raise ValidationError(f"missing artifacts: {', '.join(missing)}")
    if extra:
        raise ValidationError(f"unexpected artifacts: {', '.join(extra)}")
    return {
        stage: validate_artifact_file(artifact_dir / f"{stage}.json", expected_stage=stage)
        for stage in stages
    }


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Spec Kit shadow artifacts")
    parser.add_argument("--phase", choices=("prechange", "convergence"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--stage", help="validate one stage; otherwise validate the complete phase")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate a run directory from a fixed, repository-owned shell step."""

    args = _cli_parser().parse_args(argv)
    try:
        if args.stage is None:
            artifacts = validate_artifacts(args.run_dir, phase=args.phase)
        else:
            if args.stage not in stages_for_phase(args.phase):
                raise ValidationError(f"stage is not valid for {args.phase}: {args.stage}")
            artifacts = {args.stage: validate_artifact_file(args.run_dir / f"{args.stage}.json", expected_stage=args.stage)}
    except ValidationError as exc:
        print(f"sdd-shadow validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"phase": args.phase, "stages": list(artifacts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
