"""Focused contract tests for the isolated Spec Kit shadow tooling."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.sdd_shadow.launcher import (
    ALLOWED_INTEGRATIONS,
    DirtyCheckoutError,
    _prepare_run,
    run_shadow,
)
from tools.sdd_shadow.validate import (
    CONVERGENCE_STAGES,
    PRECHANGE_STAGES,
    ValidationError,
    validate_artifact,
    validate_artifact_file,
    validate_artifacts,
    load_json,
)

pytestmark = pytest.mark.tier_unit

ROOT = Path(__file__).resolve().parents[1]
SHADOW_ROOT = ROOT / "tools" / "sdd_shadow"
WORKFLOW_PATHS = {
    "prechange": SHADOW_ROOT / "workflow-prechange.yml",
    "convergence": SHADOW_ROOT / "workflow-convergence.yml",
}


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _synthetic_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "sdd-shadow-test")
    _git(repo, "config", "user.email", "sdd-shadow-test@example.invalid")
    (repo / "README.txt").write_text("clean snapshot\n", encoding="utf-8")
    _git(repo, "add", "README.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _contract(tmp_path: Path) -> Path:
    contract = tmp_path / "change-contract.md"
    contract.write_text(
        "## SDD Depth\nD0\n\n## Contract Delta\nNONE\n\n## Non-goals\nNo runtime change.\n",
        encoding="utf-8",
    )
    return contract


def _valid(stage: str, verdict: str = "ADVISORY") -> dict[str, object]:
    return {
        "stage": stage,
        "verdict": verdict,
        "findings": [],
        "blockers": [],
        "clarifications": [],
        "dependencies": [],
        "non_goals_at_risk": [],
        "verification_obligations": [],
        "unknowns": [],
    }


def test_workflows_have_spec_kit_v1_shape_and_all_required_stages() -> None:
    expected = {
        "prechange": PRECHANGE_STAGES,
        "convergence": CONVERGENCE_STAGES,
    }
    for phase, path in WORKFLOW_PATHS.items():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert workflow["schema_version"] == "1.0"
        assert workflow["workflow"]["version"] == "1.0.4"
        assert workflow["workflow"]["id"].startswith("aletheia-sdd-shadow-")
        assert workflow["workflow"]["integration"] == "omp"
        assert workflow["requires"] == {"speckit_version": "1.0.4", "integrations": ["omp"]}
        stages = [step["id"].replace("-", "_") for step in workflow["steps"] if step["type"] == "prompt"]
        assert stages == list(expected[phase])
        assert all(step["type"] in {"prompt", "shell"} for step in workflow["steps"])


def test_workflow_shell_steps_are_fixed_and_only_use_engine_run_id() -> None:
    forbidden = re.compile(r"(?:\$\(|\x60|;|\||&&|>|<|\$\{(?!\{)|\b(?:eval|bash|sh)\b)")
    for path in WORKFLOW_PATHS.values():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in workflow["steps"]:
            if step["type"] != "shell":
                continue
            command = step["run"]
            assert command.startswith("python3 .sdd_shadow_runtime/validate.py ")
            assert "{{ context.run_id }}" in command
            assert not forbidden.search(command.replace("{{ context.run_id }}", ""))
            assert "context.json" not in command



def test_integration_allowlist_is_explicit() -> None:
    assert ALLOWED_INTEGRATIONS == frozenset({"omp"})
    with pytest.raises(Exception, match="allowlisted"):
        # Paths are checked before any disposable project is created.
        run_shadow(
            repo=Path("/not/a/repository"),
            phase="prechange",
            contract=Path("/not/a/contract"),
            integration="community",
            output=Path("/tmp/sdd-shadow-test-output"),
            validate_only=True,
        )


def test_launcher_refuses_dirty_source_checkout(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path)
    contract = _contract(tmp_path)
    (repo / "README.txt").write_text("uncommitted\n", encoding="utf-8")
    with pytest.raises(DirtyCheckoutError):
        run_shadow(
            repo=repo,
            phase="prechange",
            contract=contract,
            output=tmp_path / "results",
            validate_only=True,
        )


def test_archive_snapshot_has_no_git_and_source_stays_unchanged(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path)
    contract = _contract(tmp_path)
    before = {path.relative_to(repo): hashlib.sha256(path.read_bytes()).hexdigest() for path in repo.rglob("*") if path.is_file()}
    prepared, metadata = _prepare_run(
        repo=repo,
        phase="prechange",
        contract=contract,
        base_ref=None,
        head_ref="HEAD",
        runtime_evidence=(),
        review_evidence=(),
    )
    try:
        assert not (prepared.snapshot / ".git").exists()
        assert not metadata["snapshot_has_git"]
        assert (prepared.scratch / ".specify" / "workflows" / "runs").is_dir()
        assert prepared.run_dir.is_relative_to(prepared.scratch / ".shadow")
        assert not prepared.snapshot.resolve().is_relative_to(repo.resolve())
    finally:
        shutil.rmtree(prepared.scratch, ignore_errors=True)
    after = {path.relative_to(repo): hashlib.sha256(path.read_bytes()).hexdigest() for path in repo.rglob("*") if path.is_file()}
    assert after == before
    assert not (repo / ".specify").exists()


def test_validate_only_copies_only_external_bundle_and_manifest(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path)
    contract = _contract(tmp_path)
    output = tmp_path / "results"
    manifest_path = run_shadow(
        repo=repo,
        phase="prechange",
        contract=contract,
        output=output,
        validate_only=True,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path.is_relative_to(output)
    assert manifest["source_checkout_clean"] is True
    assert manifest["non_authoritative"] is True
    assert manifest["isolation"]["snapshot_has_git"] is False
    assert manifest["isolation"]["canonical_checkout_mutated"] is False
    assert manifest["isolation"]["run_state_relative"] == ".specify/workflows/runs"
    assert manifest["isolation"]["agent_output_relative"].startswith(".shadow/")
    assert (manifest_path.parent / "validation.json").is_file()
    assert not (repo / ".shadow").exists()
    assert not (repo / ".specify").exists()


def test_convergence_manifest_freezes_base_merge_and_diff(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path)
    (repo / "changed.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "changed.txt")
    _git(repo, "commit", "-m", "candidate")
    contract = _contract(tmp_path)
    manifest_path = run_shadow(
        repo=repo,
        phase="convergence",
        contract=contract,
        base_ref="HEAD~1",
        output=tmp_path / "results",
        validate_only=True,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_base"]
    assert manifest["merge_base"] == manifest["source_base"]
    assert manifest["changed_files"] == ["changed.txt"]
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["diff_sha256"])


def test_malformed_and_missing_agent_json_fail_closed(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "authority_resolution.json").write_text("{malformed", encoding="utf-8")
    with pytest.raises(ValidationError, match="invalid JSON"):
        validate_artifact_file(artifact_dir / "authority_resolution.json", expected_stage="authority_resolution")
    with pytest.raises(ValidationError, match="missing artifacts"):
        validate_artifacts(artifact_dir, phase="prechange")
    (artifact_dir / "authority_resolution.json").write_text(
        json.dumps({**_valid("authority_resolution"), "unexpected": True}), encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="unexpected"):
        validate_artifact_file(artifact_dir / "authority_resolution.json", expected_stage="authority_resolution")


def test_validator_rejects_duplicate_keys_and_bad_finding() -> None:
    with pytest.raises(ValidationError, match="duplicate JSON key"):
        path = Path(__file__).with_name("_duplicate-shadow-json.tmp")
        try:
            path.write_text('{"stage":"synthesis","stage":"synthesis"}', encoding="utf-8")
            load_json(path)
        finally:
            path.unlink(missing_ok=True)
    bad = _valid("synthesis")
    bad["findings"] = [{"id": "x", "severity": "critical"}]
    with pytest.raises(ValidationError, match="shape invalid"):
        validate_artifact(bad)


def test_2856_fixture_covers_lenses_without_promoting_2836() -> None:
    fixture = json.loads((SHADOW_ROOT / "fixtures" / "retrospective-2856.json").read_text(encoding="utf-8"))
    obligations = fixture["obligations"]
    assert fixture["authoritative"] is False
    assert fixture["bias"] == "hindsight"
    assert fixture["excluded_implementation_scope"] == ["#2836"]
    assert len(obligations) == 13
    assert {item["id"] for item in obligations} == {
        "committed-prefix-resumability",
        "activation-lock-lifetime",
        "full-audit-outbox-drain",
        "recovery-restart-failure",
        "no-restart-settlement-bypass",
        "packaged-recovery-ordering",
        "compaction-skip-semantics",
        "transient-settlement-vs-release-defect",
        "readiness-vs-structural-capture",
        "pre-pull-target-config-boundary",
        "target-re-anchor-inside-activation-barrier",
        "independent-idle-cursor-prerequisite",
        "implementation-only-clean-host-fixture-gap",
    }
    assert {item["lens"] for item in obligations} <= {
        "authority_resolution",
        "clarification",
        "transaction_durability",
        "concurrency_lifetime",
        "entrypoint",
        "failure_semantics",
        "verification",
        "convergence",
    }
    assert any(item.get("implementation_only") for item in obligations)
    assert all("#2836" not in item["evidence"] for item in obligations)
