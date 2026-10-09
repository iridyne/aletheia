#!/usr/bin/env python3
"""Deterministic CI lane planner for Aletheia.

The planner is intentionally stdlib-only and side-effect free except for local
git inspection requested by the CLI. It owns CI depth/risk mapping; workflows
and local entrypoints consume its JSON instead of duplicating path rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ci_plan.v1"
POLICY_VERSION = "2026-09-27.1"
MODES = ("docs_only", "quick", "affected", "deep", "full")
MODE_RANK = {mode: index for index, mode in enumerate(MODES)}
LANES = (
    "quick",
    "python_unit",
    "runtime",
    "integration",
    "specifications",
    "e2e",
    "frontend",
    "operator",
    "native",
    "coverage",
    "trusted_ci",
    "full_ci",
    "shadow_backstop",
)
SAFE_DOC_PREFIXES = (
    "docs/archive/",
    "docs/diagrams/",
    "docs/references/",
    "docs/research/",
)
SAFE_DOC_ASSET_SUFFIXES = (
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".md",
    ".png",
    ".svg",
    ".webp",
)
AUTHORITY_DOC_PREFIXES = (
    "docs/design/contracts/",
    "docs/design/decisions/",
    "docs/design/proposals/",
    "docs/governance/",
    "docs/operations/",
    "docs/product/definition/",
    "docs/product/gates/",
)
FULL_PREFIXES = (
    ".github/workflows/",
    ".github/actions/",
    "migrations/",
    "alembic/",
)
FULL_FILES = {
    ".coveragerc",
    ".release-please-manifest.json",
    "Cargo.lock",
    "Cargo.toml",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "pytest.ini",
    "pytest.toml",
    "release-please-config.json",
    "rust-toolchain",
    "rust-toolchain.toml",
    "scripts/ci/check_change_contract.py",
    "scripts/ci/ci_failure_handoff.py",
    "scripts/ci/ci_local.py",
    "scripts/ci/ci_plan.py",
    "scripts/ci/ci_preflight.py",
    "scripts/ci/ci_yield.py",
    "tests/conftest.py",
    "tox.ini",
    "uv.lock",
}
FULL_TEST_FILES = (
    "tests/test_architecture_fitness.py",
    "tests/test_change_contract_check.py",
    "tests/test_ci_",
    "tests/test_frontend_ci_build_contract.py",
    "tests/specs/spec_codex_shepherd.py",
)
CRITICAL_PREFIXES = (
    "core/aletheia/execution/",
    "core/aletheia/gateway/",
    "core/aletheia/portfolio/",
    "core/aletheia/risk/",
    "core/aletheia/runtime",
    "core/aletheia/server/routes/risk.py",
    "core/aletheia/storage/",
)
CRITICAL_TOKENS = (
    "account",
    "attribution",
    "broker",
    "credential",
    "economics",
    "execution",
    "fill",
    "gateway",
    "identity",
    "outcome",
    "pause",
    "position",
    "profit",
    "reconcil",
    "runtime",
    "scheduler",
    "settlement",
)
INTEGRATION_TEST_TOKENS = (
    "acceptance",
    "broker",
    "daemon",
    "frontdoor",
    "gateway",
    "integration",
    "lifecycle",
    "routes",
    "runtime",
    "scheduler",
    "server",
    "service",
    "shell",
    "status",
    "storage",
    "supervisor",
    "workflow",
    "workbench",
)
PYTHON_CONTRACT_PREFIXES = (
    "core/aletheia/config/",
    "core/aletheia/server/routes/settings.py",
    "core/aletheia/server/routes/ui.py",
    "core/aletheia/data/models.py",
)
COVERAGE_PATHS = (
    "core/aletheia/analytics/profit_gradient.py",
    "core/aletheia/analytics/profit_gradient_estimators.py",
    "core/aletheia/analytics/profit_gradient_evidence.py",
    "core/aletheia/analytics/profit_gradient_evidence_utils.py",
    "core/aletheia/analytics/position_replay.py",
    "core/aletheia/analytics/signal_replay.py",
    "extensions/counterfactual/filter_replay.py",
    "core/aletheia/economics/context_artifacts.py",
    "core/aletheia/execution/reconciliation.py",
    "extensions/learning/evidence_epoch.py",
    "extensions/learning/policy_epochs.py",
    "scripts/ci/coverage_floors.py",
    "tests/test_profit_gradient.py",
    "tests/test_profit_gradient_properties.py",
    "tests/test_profit_gradient_evidence.py",
    "tests/test_p3_evidence_epoch.py",
    "tests/test_filter_replay.py",
    "tests/test_position_replay.py",
    "tests/test_momentum_geometry_replay.py",
    "tests/test_signal_replay.py",
    "tests/test_reconciliation.py",
    "tests/test_oco_reconcile.py",
    "tests/test_cr_ex01_reconcile.py",
    "tests/test_position_reconciler.py",
    "tests/test_scheduler_reconcile.py",
    "tests/test_reconciliation_integrity_incident.py",
    "tests/test_reconciliation_properties.py",
    "tests/test_context_artifacts_properties.py",
    "tests/test_filter_replay_properties.py",
    "tests/test_policy_epoch_properties.py",
    "tests/test_profit_gradient_evidence_properties.py",
)
SURFACE_DEPENDENCIES = {
    "frontend_contract": ("frontend", "python_unit"),
    "storage": ("runtime", "integration"),
    "execution": ("runtime", "integration", "specifications", "e2e"),
    "risk": ("runtime", "integration", "specifications", "e2e"),
    "coverage": ("coverage",),
    "runtime_test": ("runtime",),
}


def _canonical_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized.startswith("r") and normalized[1:].isdigit():
        return "renamed"
    return {
        "a": "added",
        "m": "modified",
        "d": "removed",
        "deleted": "removed",
        "r": "renamed",
    }.get(normalized, normalized)


@dataclass(frozen=True)
class PathChange:
    path: str
    status: str = "modified"
    previous_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _canonical_status(self.status))

    def all_paths(self) -> tuple[str, ...]:
        paths = [self.path]
        if self.previous_path:
            paths.append(self.previous_path)
        return tuple(_normalize_path(path) for path in paths)


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def _mode_at_least(current: str, candidate: str) -> str:
    return candidate if MODE_RANK[candidate] > MODE_RANK[current] else current


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_changes(base_ref: str, head_ref: str) -> tuple[str, str, list[PathChange]]:
    base_sha = _git(["rev-parse", base_ref])
    head_sha = _git(["rev-parse", head_ref])
    diff = _git(["diff", "--name-status", "-M", f"{base_sha}...{head_sha}", "--"])
    changes: list[PathChange] = []
    for line in diff.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) == 3:
            changes.append(PathChange(path=parts[2], status="renamed", previous_path=parts[1]))
        elif len(parts) >= 2:
            changes.append(PathChange(path=parts[-1], status=status.lower()))
    if head_ref == "HEAD" and not _running_in_github_actions():
        untracked = _git(["ls-files", "--others", "--exclude-standard"])
        tracked_paths = {change.path for change in changes}
        for path in untracked.splitlines():
            if path and path not in tracked_paths:
                changes.append(PathChange(path=path, status="added"))
    return base_sha, head_sha, changes


def _default_git_refs(event: str) -> tuple[str, str]:
    if event in {"pull_request", "merge_group"} and _running_in_github_actions() and _looks_like_pr_merge_commit():
        return "HEAD^1", "HEAD^2"
    refs = ["origin/master", "master"]
    for ref in refs:
        try:
            base = _git(["merge-base", "HEAD", ref])
        except (OSError, subprocess.CalledProcessError):
            continue
        if base:
            return base, "HEAD"
    raise RuntimeError("unable to resolve a CI planning base")


def _looks_like_pr_merge_commit() -> bool:
    try:
        parents = _git(["rev-list", "--parents", "-n", "1", "HEAD"]).split()
    except (OSError, subprocess.CalledProcessError):
        return False
    return len(parents) >= 3


def _running_in_github_actions() -> bool:
    return sys.platform != "darwin" and bool(os.environ.get("GITHUB_ACTIONS"))


def _safe_docs_path(path: str) -> bool:
    if path == "README.md":
        return True
    return path.startswith(SAFE_DOC_PREFIXES) and path.endswith(SAFE_DOC_ASSET_SUFFIXES)


def _agents_path(path: str) -> bool:
    return path.rsplit("/", 1)[-1] == "AGENTS.md"


def _docs_path(path: str) -> bool:
    return path == "README.md" or _agents_path(path) or path.startswith("docs/")


def _full_path(path: str) -> bool:
    return (
        path in FULL_FILES
        or path.startswith(FULL_PREFIXES)
        or path.startswith(FULL_TEST_FILES)
        or path.startswith("tests/test_ci_")
        or path.endswith((".lock", ".toml"))
        or "/migrations/" in path
        or path.endswith(("_schema.py", "_schema.json", "openapi.json"))
    )


def _critical_path(path: str) -> bool:
    if path.startswith(CRITICAL_PREFIXES):
        return True
    return path.startswith(("core/aletheia/", "extensions/")) and any(token in path for token in CRITICAL_TOKENS)


def _coverage_path(path: str) -> bool:
    return path in COVERAGE_PATHS


def _operational_script_path(path: str) -> bool:
    return (path.startswith("scripts/") and path.endswith(".py")) or path.startswith(
        ("scripts/runtime/", "scripts/dev/")
    ) or path in {
        "scripts/dev/dev.ps1",
        "scripts/dev/test_ai.ps1",
        "scripts/dev/watch_backend.py",
    }


def _unknown_executable_path(path: str) -> bool:
    known_prefixes = (
        "AGENTS.md",
        "README.md",
        "core/aletheia/",
        "docs/",
        "frontend/",
        "operator-shell/",
        "scripts/",
        "tests/",
    )
    return not path.startswith(known_prefixes)


def _path_surfaces(path: str) -> set[str]:
    surfaces: set[str] = set()
    if _docs_path(path):
        surfaces.add("docs")
    if path.startswith("frontend/"):
        surfaces.add("frontend")
    if path.startswith("operator-shell/"):
        surfaces.add("operator")
    if path.startswith(("core/aletheia/", "tests/")) or path.endswith(".py"):
        surfaces.add("python")
    if path.startswith(PYTHON_CONTRACT_PREFIXES) or path.startswith("scripts/ci/export_openapi_schema.py"):
        surfaces.add("frontend_contract")
    if path.startswith("core/aletheia/storage/"):
        surfaces.add("storage")
    if path.startswith("core/aletheia/execution/"):
        surfaces.add("execution")
    if path.startswith("core/aletheia/risk/"):
        surfaces.add("risk")
    if _coverage_path(path):
        surfaces.add("coverage")
    if path.startswith("tests/simulation/"):
        surfaces.add("runtime_test")
    return surfaces


def _test_tier(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if not path.startswith("tests/"):
        return None
    if path.startswith("tests/e2e/") or name.endswith("_e2e.py") or "_e2e" in name:
        return "e2e"
    if path.startswith("tests/specs/") or name.startswith("spec_") or name == "test_spec_conformance.py":
        return "specifications"
    if path.startswith("tests/integration/") or any(token in name for token in INTEGRATION_TEST_TOKENS):
        return "integration"
    return "python_unit"


def _required_for_mode(mode: str, surfaces: set[str]) -> dict[str, bool]:
    required = {lane: False for lane in LANES}
    if mode == "docs_only":
        return required
    required["quick"] = True
    required["trusted_ci"] = True
    if mode == "quick":
        return required
    for surface in surfaces:
        if surface == "python":
            required["python_unit"] = True
        elif surface == "frontend":
            required["frontend"] = True
        elif surface == "operator":
            required["operator"] = True
        elif surface == "native":
            required["native"] = True
        elif surface == "coverage":
            required["coverage"] = True
        elif surface == "runtime_test":
            required["runtime"] = True
        for dependent in SURFACE_DEPENDENCIES.get(surface, ()):
            required[dependent] = True
    if mode in {"deep", "full"}:
        for lane in ("python_unit", "runtime", "integration", "specifications", "e2e"):
            required[lane] = True
    if mode == "full":
        for lane in ("python_unit", "runtime", "integration", "specifications", "e2e", "frontend", "operator", "native", "coverage", "full_ci"):
            required[lane] = True
    return required


def plan(
    *,
    event: str,
    base_sha: str,
    head_sha: str,
    changes: list[PathChange],
    changed_files_complete: bool = True,
    trusted_labels: list[str] | None = None,
) -> dict[str, Any]:
    mode = "docs_only"
    reason_codes: set[str] = set()
    surfaces: set[str] = set()
    unknown_paths: set[str] = set()
    labels = set(trusted_labels or [])

    if event in {"workflow_dispatch", "release_candidate", "scheduled", "nightly", "shadow"}:
        mode = "full"
        reason_codes.add(f"EVENT_{event.upper()}")

    if not changed_files_complete:
        mode = "full"
        reason_codes.add("CHANGED_FILES_INCOMPLETE")

    if not changes and event not in {"workflow_dispatch", "release_candidate", "scheduled", "nightly", "shadow"}:
        mode = _mode_at_least(mode, "quick")
        reason_codes.add("NO_SOURCE_DIFF")

    for change in changes:
        if change.status == "renamed" and not change.previous_path:
            mode = "full"
            reason_codes.add("RENAME_PREVIOUS_PATH_MISSING")
        if change.status == "removed":
            reason_codes.add("DELETION_REVIEWED")
        for path in change.all_paths():
            # Deleted/renamed pre-layout paths retain their original check lanes.
            # This is diff classification only, not a second source/import owner.
            if path.startswith("aletheia/"):
                path = "core/" + path
            surfaces.update(_path_surfaces(path))
            if _safe_docs_path(path) and not _agents_path(path):
                reason_codes.add("ZERO_CI_DOCS")
                continue
            mode = _mode_at_least(mode, "quick")
            if _docs_path(path):
                if _agents_path(path) or path.startswith(AUTHORITY_DOC_PREFIXES):
                    mode = "full"
                    reason_codes.update({"AUTHORITATIVE_DOCS", "FULL_AUTHORITY_OR_INFRA"})
                else:
                    reason_codes.add("DOCS_QUICK")
                continue
            if _full_path(path):
                mode = "full"
                reason_codes.add("FULL_AUTHORITY_OR_INFRA")
                continue
            if _operational_script_path(path):
                mode = "full"
                reason_codes.add("FULL_OPERATIONAL_SCRIPT")
                continue
            tier = _test_tier(path)
            if path.startswith("tests/simulation/"):
                mode = _mode_at_least(mode, "deep")
                reason_codes.add("RUNTIME_SIMULATION_TEST")
                continue
            if tier in {"integration", "specifications", "e2e"}:
                mode = _mode_at_least(mode, "deep")
                reason_codes.add("TEST_TIER_DEEP")
                continue
            if _coverage_path(path):
                mode = _mode_at_least(mode, "deep")
                reason_codes.add("COVERAGE_TRUST_BOUNDARY")
                continue
            if _critical_path(path):
                mode = _mode_at_least(mode, "deep")
                reason_codes.add("CRITICAL_TRADING_AUTHORITY")
                continue
            if path.startswith(("frontend/", "operator-shell/", "core/aletheia/", "tests/")) or path.endswith(".py"):
                mode = _mode_at_least(mode, "affected")
                reason_codes.add("AFFECTED_COMPONENT")
                continue
            if _unknown_executable_path(path):
                unknown_paths.add(path)
                mode = "full"
                reason_codes.add("UNKNOWN_EXECUTABLE_PATH")

    if "ci:full" in labels:
        mode = "full"
        reason_codes.add("TRUSTED_LABEL_FULL")
    elif "ci:deep" in labels:
        mode = _mode_at_least(mode, "deep")
        reason_codes.add("TRUSTED_LABEL_DEEP")

    required = _required_for_mode(mode, surfaces)

    normalized_changes = [
        {
            "path": _normalize_path(change.path),
            "previous_path": _normalize_path(change.previous_path) if change.previous_path else None,
            "status": change.status,
        }
        for change in sorted(changes, key=lambda item: item.all_paths())
    ]
    fingerprint_payload = {
        "policy_version": POLICY_VERSION,
        "event": event,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files": normalized_changes,
        "changed_files_complete": changed_files_complete,
        "mode": mode,
        "required": required,
        "reason_codes": sorted(reason_codes),
        "surfaces": sorted(surfaces),
    }
    digest = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "event": event,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_files_complete": changed_files_complete,
        "changed_files": normalized_changes,
        "mode": mode,
        "surfaces": sorted(surfaces),
        "unknown_paths": sorted(unknown_paths),
        "required": required,
        "local_checks": _local_checks(required),
        "review": {
            # Codex remains optional feedback; an asynchronous comment is not CI.
            "codex_required": False,
        },
        "reason_codes": sorted(reason_codes),
        "plan_fingerprint": f"sha256:{digest}",
    }


def _local_checks(required: dict[str, bool]) -> list[dict[str, Any]]:
    checks = [
        {"name": "clean tracked worktree", "command": ["git", "diff", "--quiet", "HEAD", "--"]},
        {"name": "git diff whitespace", "command": ["git", "diff", "--check"]},
        {"name": "documentation governance", "command": ["uv", "run", "python", "scripts/ci/check_docs.py"]},
    ]
    if required.get("quick"):
        checks.append({
            "name": "ci workflow contracts", "lane": "quick",
            "command": [
                "uv", "run", "pytest", "tests/test_ci_plan.py", "tests/test_ci_local.py",
                "tests/specs/spec_codex_shepherd.py", "tests/test_ci_yield.py", "-q", "--tb=short",
            ],
        })
    if required.get("full_ci"):
        checks.append({
            "name": "isolated full Python proof", "lane": "full_ci",
            "unavailable_reason": (
                "Full-suite proof requires a credential-isolated Fedora CI identity/container; "
                "this local executor cannot establish that isolation."
            ),
        })
    if required.get("frontend"):
        checks.extend([
            {"name": "frontend settings types", "lane": "frontend", "command": ["npm", "run", "check:settings-types"], "working_directory": "frontend"},
            {"name": "frontend api types", "lane": "frontend", "command": ["npm", "run", "check:api-types"], "working_directory": "frontend"},
            {"name": "frontend tests", "lane": "frontend", "command": ["npm", "test"], "working_directory": "frontend"},
            {"name": "frontend build", "lane": "frontend", "command": ["npm", "run", "build"], "working_directory": "frontend"},
        ])
    if required.get("operator"):
        checks.append({"name": "operator tests", "lane": "operator", "command": ["npm", "test"], "working_directory": "operator-shell"})
    exhaustive_python = all(
        required.get(lane) for lane in ("python_unit", "integration", "specifications", "e2e")
    )
    for lane, marker in (
        ("python_unit", "tier_unit"), ("runtime", "domain_runtime"),
        ("integration", "tier_integration"), ("specifications", "tier_spec"), ("e2e", "tier_e2e"),
    ):
        if required.get(lane):
            check: dict[str, Any] = {"name": f"python {lane} proof", "lane": lane}
            if exhaustive_python:
                check["unavailable_reason"] = (
                    "Combined Python tiers are the full suite, not focused local proof; "
                    "run them in the credential-isolated Fedora executor."
                )
            else:
                check["command"] = ["uv", "run", "pytest", "tests/", "-m", marker, "-q", "--tb=short", "--timeout=120"]
            checks.append(check)
    if required.get("coverage"):
        checks.extend([
            {
                "name": "trust-boundary coverage", "lane": "coverage",
                "command": [
                    "uv", "run", "pytest", *[path for path in COVERAGE_PATHS if path.startswith("tests/")],
                    "--cov", "--cov-report=term-missing:skip-covered", "-q", "--timeout=120",
                ],
            },
            {"name": "coverage floor self-check", "lane": "coverage", "command": ["uv", "run", "python", "scripts/ci/coverage_floors.py", "--selftest"]},
            {"name": "coverage floors", "lane": "coverage", "command": ["uv", "run", "python", "scripts/ci/coverage_floors.py"]},
        ])
    represented = {check.get("lane") for check in checks}
    # trusted_ci is the aggregate verdict, not a separately executable proof lane.
    for lane, needed in required.items():
        if needed and lane != "trusted_ci" and lane not in represented:
            checks.append({
                "name": f"unavailable {lane} proof", "lane": lane,
                "unavailable_reason": "No local executor is registered for this required proof lane.",
            })
    return checks


def _parse_changed_file(value: str) -> PathChange:
    parts = value.split(":")
    if len(parts) == 3:
        return PathChange(status=parts[0], previous_path=parts[1], path=parts[2])
    if len(parts) == 2:
        return PathChange(status=parts[0], path=parts[1])
    return PathChange(path=value)


def _load_changes(args: argparse.Namespace) -> tuple[str, str, list[PathChange]]:
    base_sha = args.base_sha
    head_sha = args.head_sha
    changes: list[PathChange] = []
    if args.changed_files_json:
        data = json.loads(Path(args.changed_files_json).read_text(encoding="utf-8"))
        for item in data:
            changes.append(PathChange(
                path=item.get("filename") or item.get("path") or item["name"],
                previous_path=item.get("previous_filename") or item.get("previous_path"),
                status=item.get("status") or "modified",
            ))
    changes.extend(_parse_changed_file(value) for value in args.changed_file)
    if not changes:
        base_ref, head_ref = (args.base_ref, args.head_ref) if args.base_ref else _default_git_refs(args.event)
        git_base, git_head, changes = _git_changes(base_ref, head_ref)
        base_sha = base_sha or git_base
        head_sha = head_sha or git_head
    elif not base_sha or not head_sha:
        base_ref, head_ref = (args.base_ref, args.head_ref) if args.base_ref else _default_git_refs(args.event)
        base_sha = base_sha or _git(["rev-parse", base_ref])
        head_sha = head_sha or _git(["rev-parse", head_ref])
    return base_sha or "", head_sha or "", changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the deterministic Aletheia CI plan.")
    parser.add_argument("--event", default="local")
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--changed-files-json")
    parser.add_argument("--changed-files-complete", choices=("true", "false"), default="true")
    parser.add_argument("--trusted-label", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        base_sha, head_sha, changes = _load_changes(args)
        report = plan(
            event=args.event,
            base_sha=base_sha,
            head_sha=head_sha,
            changes=changes,
            changed_files_complete=args.changed_files_complete == "true",
            trusted_labels=args.trusted_label,
        )
    except Exception as exc:  # fail closed with a full plan.
        report = plan(
            event=args.event,
            base_sha=args.base_sha or "",
            head_sha=args.head_sha or "",
            changes=[],
            changed_files_complete=False,
            trusted_labels=["ci:full"],
        )
        report["planner_error"] = str(exc)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report.get("planner_error") else 2


if __name__ == "__main__":
    sys.exit(main())
