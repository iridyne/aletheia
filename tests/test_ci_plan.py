from __future__ import annotations

import json
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
    assert safe["github_ci"] == "none"
    assert safe["required"]["trusted_ci"] is False

    authoritative = _plan(["docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md"])
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
    report = _plan(["README.md", "aletheia/cli/doctor_cmd.py"])
    assert report["mode"] == "affected"
    assert report["required"]["python_unit"] is True


def test_critical_and_ci_authority_escalate() -> None:
    deep = _plan(["aletheia/risk/engine.py"])
    assert deep["mode"] == "deep"
    assert deep["required"]["runtime"] is True
    assert deep["review"]["codex_required"] is False

    full = _plan([".github/workflows/ci.yml"])
    assert full["mode"] == "full"
    assert full["required"]["full_ci"] is True
    assert full["required"]["shadow_backstop"] is False
    assert full["review"]["codex_required"] is False
    assert full["github_checks"] == [
        "Verify Change Contract",
        "Trusted CI verdict",
        "Full CI verdict",
    ]


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
    report = _plan(["aletheia/config/schema.py"])
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

    renamed = _plan([PathChange(path="docs/references/risk.md", status="renamed", previous_path="aletheia/risk/engine.py")])
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


def test_policy_changes_themselves_are_full_and_fingerprint_is_stable() -> None:
    left = _plan(["scripts/ci_plan.py"])
    right = _plan(["scripts/ci_plan.py"])
    assert left["mode"] == "full"
    assert left["plan_fingerprint"] == right["plan_fingerprint"]


@pytest.mark.parametrize(
    ("git_status", "github_status", "previous_path"),
    [
        ("M", "modified", None),
        ("A", "added", None),
        ("D", "removed", None),
        ("R100", "renamed", "aletheia/risk/old_engine.py"),
    ],
)
def test_git_and_github_statuses_produce_identical_plans(
    git_status: str,
    github_status: str,
    previous_path: str | None,
) -> None:
    path = "aletheia/risk/engine.py"
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
        "aletheia/analytics/profit_gradient.py",
        "aletheia/analytics/profit_gradient_estimators.py",
        "aletheia/analytics/profit_gradient_evidence.py",
        "aletheia/analytics/position_replay.py",
        "aletheia/counterfactual/filter_replay.py",
        "aletheia/economics/context_artifacts.py",
        "aletheia/execution/reconciliation.py",
        "aletheia/learning/evidence_epoch.py",
        "aletheia/learning/policy_epochs.py",
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
