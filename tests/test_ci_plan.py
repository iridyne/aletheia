from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_plan
from scripts.ci_plan import PathChange, plan


BASE = "b" * 40
HEAD = "a" * 40


def _plan(paths, **kwargs):
    return plan(
        event=kwargs.pop("event", "pull_request"),
        base_sha=BASE,
        head_sha=HEAD,
        changes=[
            item if isinstance(item, PathChange) else PathChange(path=item)
            for item in paths
        ],
        **kwargs,
    )


def test_safe_docs_are_zero_ci_but_authoritative_docs_are_full() -> None:
    safe = _plan(["README.md", "docs/references/example.md"])
    assert safe["mode"] == "docs_only"
    assert safe["required"]["trusted_ci"] is False
    # ``github_ci`` / ``github_checks`` are the retired auto-merge fields
    # (PR #3509). If either is re-added without deleting this guard the report
    # goes back to advertising a merge policy that has no consumer.
    assert "github_ci" not in safe
    assert "github_checks" not in safe

    authoritative = _plan(["docs/design/contracts/persistence_recovery_contract.md"])
    assert authoritative["mode"] == "full"
    assert authoritative["required"]["full_ci"] is True
    assert authoritative["review"]["codex_required"] is False

    agents = _plan(["AGENTS.md"])
    assert agents["mode"] == "full"
    assert agents["review"]["codex_required"] is False

    nested_agents = _plan(["scripts/AGENTS.md"])
    assert nested_agents["mode"] == "full"
    assert nested_agents["review"]["codex_required"] is False

    safe_prefix_agents = _plan(["docs/references/AGENTS.md"])
    assert safe_prefix_agents["mode"] == "full"
    assert safe_prefix_agents["review"]["codex_required"] is False


def test_non_safe_docs_file_invalidates_zero_ci() -> None:
    report = _plan(["README.md", "core/aletheia/cli/doctor_cmd.py"])
    assert report["mode"] == "affected"
    assert report["required"]["python_unit"] is True


def test_critical_and_ci_authority_escalate() -> None:
    deep = _plan(["core/aletheia/risk/engine.py"])
    assert deep["mode"] == "deep"
    assert deep["required"]["runtime"] is True
    assert deep["review"]["codex_required"] is False

    full = _plan([".github/workflows/ci.yml"])
    assert full["mode"] == "full"
    assert full["required"]["full_ci"] is True
    assert full["required"]["shadow_backstop"] is False
    assert full["review"]["codex_required"] is False
    assert "github_ci" not in full
    assert "github_checks" not in full


@pytest.mark.parametrize("module", ["credential_routes", "credential_cli", "http_runtime"])
def test_moved_model_boundaries_preserve_deep_validation(module) -> None:
    report = _plan([f"extensions/model_endpoints/{module}.py"])
    assert report["mode"] == "deep"
    for lane in ("runtime", "integration", "specifications", "e2e"):
        assert report["required"][lane] is True


def test_frontend_operator_native_do_not_pull_unrelated_stacks() -> None:
    frontend = _plan(["frontend/src/App.tsx"])
    assert frontend["mode"] == "affected"
    assert frontend["required"]["frontend"] is True
    assert frontend["required"]["python_unit"] is False
    assert frontend["required"]["native"] is False
    assert ["npm", "run", "build"] in [check["command"] for check in frontend["local_checks"]]

    operator = _plan(["operator-shell/src/main.ts"])
    assert operator["required"]["operator"] is True
    assert operator["required"]["frontend"] is False

    native = _plan(["native/aletheia-host/src/main.rs"])
    assert native["required"]["native"] is True
    assert native["required"]["python_unit"] is False


def test_shared_contract_propagates_to_dependents() -> None:
    report = _plan(["core/aletheia/config/schema.py"])
    assert report["mode"] == "affected"
    assert report["required"]["python_unit"] is True
    assert report["required"]["frontend"] is True


def test_unknown_incomplete_and_rename_fail_closed() -> None:
    unknown = _plan(["bin/new-tool"])
    assert unknown["mode"] == "full"
    assert unknown["unknown_paths"] == ["bin/new-tool"]

    incomplete = _plan(["README.md"], changed_files_complete=False)
    assert incomplete["mode"] == "full"
    assert "CHANGED_FILES_INCOMPLETE" in incomplete["reason_codes"]

    renamed = _plan([PathChange(path="docs/references/risk.md", status="renamed", previous_path="core/aletheia/risk/engine.py")])
    assert renamed["mode"] == "deep"
    assert renamed["required"]["integration"] is True


def test_deeper_test_tiers_do_not_get_unit_only_plans() -> None:
    integration = _plan(["tests/test_data_collectors_integration.py"])
    assert integration["mode"] == "deep"
    assert integration["required"]["integration"] is True

    route_integration = _plan(["tests/test_operator_routes.py"])
    assert route_integration["mode"] == "deep"
    assert route_integration["required"]["integration"] is True

    spec = _plan(["tests/specs/spec_risk_engine.py"])
    assert spec["mode"] == "deep"
    assert spec["required"]["specifications"] is True

    e2e = _plan(["tests/e2e/test_pipeline_guard_promotion.py"])
    assert e2e["mode"] == "deep"
    assert e2e["required"]["e2e"] is True

    simulation = _plan(["tests/simulation/example.py"])
    assert simulation["mode"] == "deep"
    assert simulation["required"]["runtime"] is True



@pytest.mark.parametrize(
    ("git_status", "github_status", "previous_path"),
    [
        ("M", "modified", None),
        ("A", "added", None),
        ("D", "removed", None),
        ("R100", "renamed", "core/aletheia/risk/old_engine.py"),
    ],
)
def test_git_and_github_statuses_produce_identical_plans(
    git_status: str,
    github_status: str,
    previous_path: str | None,
) -> None:
    path = "core/aletheia/risk/engine.py"
    git_report = _plan([PathChange(path, git_status, previous_path)])
    github_report = _plan([PathChange(path, github_status, previous_path)])

    for field in (
        "mode",
        "surfaces",
        "required",
        "reason_codes",
        "changed_files",
        "plan_fingerprint",
    ):
        assert git_report[field] == github_report[field]
    assert git_report["changed_files"][0]["status"] == github_status


def test_ci_git_diff_and_auto_merge_file_list_share_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    git_outputs = iter([
        BASE,
        HEAD,
        "\n".join([
            "M\tscripts/ci_plan.py",
            "A\ttests/new_ci_contract.py",
            "D\tdocs/references/old-ci.md",
            "R100\tdocs/references/before.md\tdocs/references/after.md",
        ]),
    ])
    monkeypatch.setattr(ci_plan, "_git", lambda _args: next(git_outputs))
    _, _, git_changes = ci_plan._git_changes("base", "head")

    github_files = tmp_path / "github-files.json"
    github_files.write_text(json.dumps([
        {"filename": "scripts/ci_plan.py", "status": "modified"},
        {"filename": "tests/new_ci_contract.py", "status": "added"},
        {"filename": "docs/references/old-ci.md", "status": "removed"},
        {
            "filename": "docs/references/after.md",
            "previous_filename": "docs/references/before.md",
            "status": "renamed",
        },
    ]), encoding="utf-8")
    _, _, github_changes = ci_plan._load_changes(SimpleNamespace(
        base_sha=BASE,
        head_sha=HEAD,
        changed_files_json=str(github_files),
        changed_file=[],
    ))

    ci_report = plan(event="pull_request", base_sha=BASE, head_sha=HEAD, changes=git_changes)
    auto_merge_report = plan(
        event="pull_request",
        base_sha=BASE,
        head_sha=HEAD,
        changes=github_changes,
    )
    assert ci_report["plan_fingerprint"] == auto_merge_report["plan_fingerprint"]


def test_operational_scripts_are_full_ci() -> None:
    for path in (
        "scripts/runtime/install-release.sh",
        "scripts/runtime/start-release.ps1",
        "scripts/dev/dev-init.sh",
        "scripts/watch_backend.py",
    ):
        report = _plan([path])
        assert report["mode"] == "full"
        assert report["review"]["codex_required"] is False


def test_coverage_trust_boundaries_require_coverage_without_full_mode() -> None:
    for path in (
        "core/aletheia/analytics/profit_gradient.py",
        "core/aletheia/analytics/profit_gradient_estimators.py",
        "core/aletheia/analytics/profit_gradient_evidence.py",
        "core/aletheia/analytics/position_replay.py",
        "extensions/counterfactual/filter_replay.py",
        "core/aletheia/economics/context_artifacts.py",
        "core/aletheia/execution/reconciliation.py",
        "extensions/learning/evidence_epoch.py",
        "extensions/learning/policy_epochs.py",
        "tests/test_reconciliation.py",
    ):
        report = _plan([path])
        assert report["mode"] == "deep"
        assert report["required"]["coverage"] is True

    coverage_script = _plan(["scripts/coverage_floors.py"])
    assert coverage_script["mode"] == "full"
    assert coverage_script["required"]["coverage"] is True


def test_labels_only_escalate() -> None:
    report = _plan(["README.md"], trusted_labels=["ci:deep"])
    assert report["mode"] == "deep"
    assert report["required"]["runtime"] is True


@pytest.mark.parametrize("relative,surface", [
    ("risk/engine.py", "risk"),
    ("execution/gateway.py", "execution"),
    ("storage/repositories.py", "storage"),
])
def test_core_move_preserves_old_new_and_rename_authority_lanes(relative, surface):
    old = "aletheia/" + relative
    new = "core/" + old
    for change in (PathChange(old, "removed"), PathChange(new), PathChange(new, "renamed", old)):
        report = _plan([change])
        assert report["mode"] == "deep"
        assert surface in report["surfaces"]
        assert report["required"]["runtime"] is True


def test_core_coverage_rename_keeps_coverage_gate_and_original_receipt_paths():
    old = "aletheia/analytics/profit_gradient.py"
    new = "core/" + old
    report = _plan([PathChange(new, "renamed", old)])
    assert report["required"]["coverage"] is True
    assert report["changed_files"][0]["path"] == new
    assert report["changed_files"][0]["previous_path"] == old


def test_deep_plan_cannot_reconstitute_full_python_suite_locally():
    report = _plan(["core/aletheia/risk/engine.py"])
    lanes = ("python_unit", "runtime", "integration", "specifications", "e2e")
    checks = {check.get("lane"): check for check in report["local_checks"]}
    for lane in lanes:
        assert report["required"][lane] is True
        assert "unavailable_reason" in checks[lane]
        assert "command" not in checks[lane]


@pytest.mark.parametrize("path", [
    "ops/update/applier.py", "deploy/systemd/aletheia-observer.timer",
])
@pytest.mark.parametrize("status", ["modified", "removed", "renamed"])
def test_deployment_changes_keep_deep_proof_when_removed_or_moved(path, status):
    change = PathChange("docs/references/moved.md", status, path) if status == "renamed" else PathChange(path, status)
    report = _plan([change])
    assert report["required"]["integration"] is True
    assert report["required"]["specifications"] is True
    assert report["required"]["frontend"] is False
    assert report["required"]["native"] is False


@pytest.mark.parametrize("path", [
    "scripts/dev_check.py", "scripts/ci_plan.py", "scripts/ci_local.py",
    "scripts/ci_preflight.py", "tests/test_ci_plan.py", "tests/test_ci_local.py",
    "tests/test_dev_check.py", "tests/test_ci_preflight.py",
    "docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md",
])
def test_verification_changes_use_their_own_checks_without_weakening_mixed_risk(path):
    report = _plan([path])
    commands = [check.get("command", []) for check in report["local_checks"]]
    assert any("tests/test_ci_plan.py" in command for command in commands)
    assert not any(report["required"][lane] for lane in ("python_unit", "frontend", "native", "full_ci"))
    mixed = _plan([path, "core/aletheia/risk/engine.py"])
    assert mixed["required"]["integration"] is True
    assert mixed["required"]["specifications"] is True


@pytest.mark.parametrize("path", [
    "core/aletheia/server/routes/operator.py", "core/aletheia/gateway/operator_shell.py",
])
def test_operator_backend_changes_select_the_typescript_consumer(path):
    report = _plan([path])
    assert report["required"]["operator"] is True
    assert report["required"]["python_unit"] is True
    assert report["required"]["native"] is False


def test_cli_base_diff_retains_deleted_and_renamed_deployment_owners(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    git = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
           "-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    updater = tmp_path / "ops/update/applier.py"
    timer = tmp_path / "deploy/systemd/aletheia-observer.timer"
    updater.parent.mkdir(parents=True)
    timer.parent.mkdir(parents=True)
    updater.write_text("def apply():\n    return 'update'\n")
    timer.write_text("[Timer]\nOnBootSec=60\n")
    for args in (["init"], ["add", "."], ["commit", "-m", "baseline"]):
        subprocess.run([*git, *args], cwd=tmp_path, check=True, capture_output=True)
    base = subprocess.run([*git, "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    updater.rename(tmp_path / "moved.py")
    timer.unlink()
    for args in (["add", "-A"], ["commit", "-m", "move updater and remove timer"]):
        subprocess.run([*git, *args], cwd=tmp_path, check=True, capture_output=True)
    result = subprocess.run(
        [sys.executable, str(Path(ci_plan.__file__).resolve()), "--base-ref", base],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    assert report["required"]["integration"] is True
    assert report["required"]["specifications"] is True
    assert {change["status"] for change in report["changed_files"]} == {"removed", "renamed"}
    assert any(change["previous_path"] == "ops/update/applier.py" for change in report["changed_files"])
