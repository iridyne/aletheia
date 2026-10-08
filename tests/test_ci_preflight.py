"""Focused Phase 1 contract tests for scripts/ci/ci_preflight.py."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

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


@pytest.mark.skipif(shutil.which("jj") is None, reason="changed_files_vs_base reads the diff through jj")
def test_changed_files_vs_base_diffs_from_the_fork_point_not_the_base_tip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(preflight.vcs, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("JJ_USER", "Test")
    monkeypatch.setenv("JJ_EMAIL", "test@example.invalid")

    def jj(*args: str) -> None:
        subprocess.run(["jj", *args], cwd=tmp_path, check=True, capture_output=True)

    jj("git", "init")
    (tmp_path / "shared.txt").write_text("base\n")
    jj("commit", "-m", "base")
    jj("bookmark", "create", "base", "-r", "@-")
    jj("new", "base", "-m", "base advances")
    (tmp_path / "shared.txt").write_text("advanced\n")
    jj("bookmark", "create", "tip", "-r", "@")
    jj("new", "base", "-m", "feature")
    (tmp_path / "feature.txt").write_text("feature\n")

    assert preflight.changed_files_vs_base("tip") == ["feature.txt"]


@pytest.mark.skipif(shutil.which("jj") is None, reason="changed_files_vs_base reads the diff through jj")
def test_unresolvable_base_is_reported_not_swallowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(preflight.vcs, "REPO_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="unable to resolve a base"):
        preflight.changed_files_vs_base("no-such-revset-xyz")


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
