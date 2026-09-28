"""Focused Phase 1 contract tests for scripts/ci/ci_preflight.py."""

from __future__ import annotations

import json
from types import SimpleNamespace

import scripts.ci.ci_preflight as preflight


VALID_D1_BODY = """
## SDD Depth

D1 — CI / development workflow behavior change.

## Contract Delta

### ADDED
- Cheap metadata gate.
"""


def test_preflight_reuses_change_contract_reason_codes() -> None:
    failures = preflight.run_preflight(
        "## Contract Delta\n\nNONE\n",
        ["aletheia/ci.py"],
    )

    assert failures == [{
        "code": "missing_sdd_depth",
        "message": "Missing required section: ## SDD Depth",
    }]


def test_preflight_keeps_docs_only_exemption_from_canonical_checker() -> None:
    assert not preflight.run_preflight("", ["docs/ci/guide.md"])


def test_changed_files_vs_base_uses_merge_base_not_current_base_tip(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        if args == ["git", "merge-base", "HEAD", "origin/master"]:
            return SimpleNamespace(stdout="merge-base-123\n")
        if args == ["git", "diff", "--name-only", "merge-base-123...HEAD", "--"]:
            return SimpleNamespace(stdout="docs/ci/guide.md\naletheia/ci.py\n")
        raise AssertionError(f"unexpected git invocation: {args}")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    assert preflight.changed_files_vs_base("origin/master") == [
        "docs/ci/guide.md",
        "aletheia/ci.py",
    ]
    assert calls == [
        ["git", "merge-base", "HEAD", "origin/master"],
        ["git", "diff", "--name-only", "merge-base-123...HEAD", "--"],
    ]


def test_default_base_ignores_feature_branch_upstream(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if args == ["git", "merge-base", "HEAD", "origin/master"]:
            return SimpleNamespace(stdout="base-123\n")
        if args == ["git", "diff", "--name-only", "base-123...HEAD", "--"]:
            return SimpleNamespace(stdout="docs/ci/guide.md\n")
        raise AssertionError(f"unexpected git invocation: {args}")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    assert preflight.changed_files_vs_base() == ["docs/ci/guide.md"]
    assert all("@{upstream}" not in call for call in calls)


def test_main_reports_bounded_json_and_nonzero_for_invalid_metadata(tmp_path, capsys) -> None:
    body_path = tmp_path / "pr.md"
    body_path.write_text("## Contract Delta\n\nNONE\n", encoding="utf-8")

    assert preflight.main([
        "--pr-body-file", str(body_path), "--changed-files", "aletheia/ci.py",
    ]) == preflight.EXIT_FAIL

    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == "ci_preflight.v1"
    assert report["phase"] == "preflight_metadata"
    assert report["ok"] is False
    assert report["failures"][0]["code"] == "missing_sdd_depth"


def test_main_accepts_valid_body_without_network_or_runtime(tmp_path, capsys) -> None:
    body_path = tmp_path / "pr.md"
    body_path.write_text(VALID_D1_BODY, encoding="utf-8")

    assert preflight.main([
        "--pr-body-file", str(body_path), "--changed-files", "aletheia/ci.py", "--json",
    ]) == preflight.EXIT_PASS

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["failures"] == []
