from __future__ import annotations

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


def test_safe_docs_are_zero_ci_but_authoritative_docs_are_quick() -> None:
    safe = _plan(["README.md", "docs/references/example.md"])
    assert safe["mode"] == "docs_only"
    assert safe["github_ci"] == "none"
    assert safe["required"]["trusted_ci"] is False

    authoritative = _plan(["docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md"])
    assert authoritative["mode"] == "quick"
    assert authoritative["required"]["quick"] is True
    assert authoritative["github_checks"] == ["Verify Change Contract", "Trusted CI verdict"]


def test_non_safe_docs_file_invalidates_zero_ci() -> None:
    report = _plan(["README.md", "aletheia/cli/doctor_cmd.py"])
    assert report["mode"] == "affected"
    assert report["required"]["python_unit"] is True


def test_critical_and_ci_authority_escalate() -> None:
    deep = _plan(["aletheia/risk/engine.py"])
    assert deep["mode"] == "deep"
    assert deep["required"]["runtime"] is True
    assert deep["review"]["codex_required"] is True

    full = _plan([".github/workflows/ci.yml"])
    assert full["mode"] == "full"
    assert full["required"]["full_ci"] is True
    assert full["review"]["codex_required"] is True


def test_frontend_operator_native_do_not_pull_unrelated_stacks() -> None:
    frontend = _plan(["frontend/src/App.tsx"])
    assert frontend["mode"] == "affected"
    assert frontend["required"]["frontend"] is True
    assert frontend["required"]["python_unit"] is False
    assert frontend["required"]["native"] is False

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


def test_policy_changes_themselves_are_full_and_fingerprint_is_stable() -> None:
    left = _plan(["scripts/ci_plan.py"])
    right = _plan(["scripts/ci_plan.py"])
    assert left["mode"] == "full"
    assert left["plan_fingerprint"] == right["plan_fingerprint"]


def test_labels_only_escalate() -> None:
    report = _plan(["README.md"], trusted_labels=["ci:deep"])
    assert report["mode"] == "deep"
    assert report["required"]["runtime"] is True
