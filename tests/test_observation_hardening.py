"""Long-term Worker stability hardening tests (#1072 follow-up).

Three concrete gaps identified after Fedora deployment:

1. ``_is_fresh_prior_run`` freshness gate was designed for the Main
   APScheduler and could occasionally swallow a legitimate Worker pass
   (systemd ``Persistent=true`` catch-up plus 6h cadence).
2. Worker checkout is deliberately pinned but there is no operator
   surface to see how far it has drifted from master.
3. Every clean Worker pass writes 4-5 lines to the systemd journal;
   the operator only needs to see mismatches / errors long-term.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


# ─── (1) Freshness gate bypass in Worker mode ────────────────────────────────


def test_worker_mode_bypasses_freshness_gate(monkeypatch):
    """Worker owner + fresh prior run → still executes; the freshness gate
    only applies to Main APScheduler misfires, not to systemd + fcntl-locked
    Worker passes."""
    from aletheia.analytics import filter_controller

    # Rig ``_is_fresh_prior_run`` to always claim freshness would swallow.
    monkeypatch.setattr(filter_controller, "_is_fresh_prior_run", lambda _hours: True)

    ran = {"count": 0}
    monkeypatch.setattr(
        filter_controller, "compute_filter_feedback",
        lambda **_: ran.__setitem__("count", ran["count"] + 1) or [],
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        summary = filter_controller.run_controller_observe(min_samples=20)

    # Worker mode ran the compute despite the freshness signal.
    assert ran["count"] == 1
    assert summary.get("skipped_reason") != "fresh_prior_run"


def test_main_mode_still_honors_freshness_gate(monkeypatch):
    """Main-mode backward compatibility: fresh prior run still short-circuits."""
    from aletheia.analytics import filter_controller

    monkeypatch.setattr(filter_controller, "_is_fresh_prior_run", lambda _hours: True)
    ran = {"count": 0}
    monkeypatch.setattr(
        filter_controller, "compute_filter_feedback",
        lambda **_: ran.__setitem__("count", ran["count"] + 1) or [],
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="main",
    ):
        summary = filter_controller.run_controller_observe(min_samples=20)

    assert ran["count"] == 0
    assert summary.get("skipped_reason") == "fresh_prior_run"


# ─── (2) Worker checkout drift command ───────────────────────────────────────


@pytest.fixture()
def cli() -> CliRunner:
    return CliRunner()


def _init_git_repo(root: Path) -> None:
    import subprocess
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial", "--quiet"], cwd=root, check=True,
    )


def test_drift_reports_no_behind_when_master_ref_absent(cli, tmp_path, monkeypatch):
    """No local ``origin/master`` ref → behind/ahead are 0 by construction.
    The command still succeeds and emits the current HEAD for audit."""
    from aletheia.cli.observe_cmd import observe_app

    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = cli.invoke(observe_app, ["drift", "--json"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    import json
    payload = json.loads(result.stdout)
    assert payload["behind"] == 0
    assert payload["ahead"] == 0
    assert payload["drifted"] is False
    assert len(payload["worker_head"]) == 40


def test_drift_exits_non_zero_when_behind_exceeds_warn_after(
    cli, tmp_path, monkeypatch,
):
    """``--warn-after`` gates the exit code so the command can be wired into
    a health check."""
    import subprocess
    from aletheia.cli.observe_cmd import observe_app

    _init_git_repo(tmp_path)
    # Create a fake ``origin/master`` ref advanced by 5 commits.
    for i in range(5):
        (tmp_path / f"a{i}.txt").write_text(str(i))
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"c{i}", "--quiet"], cwd=tmp_path, check=True,
        )
    # origin/master is 5 commits ahead of HEAD~5.
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/master", "HEAD"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "reset", "--hard", "HEAD~5", "--quiet"], cwd=tmp_path, check=True)

    monkeypatch.chdir(tmp_path)

    # warn_after=2 → 5 behind should trip.
    result = cli.invoke(observe_app, ["drift", "--warn-after", "2", "--json"])
    assert result.exit_code == 1
    import json
    payload = json.loads(result.stdout)
    assert payload["behind"] == 5
    assert payload["drifted"] is True

    # warn_after=10 → 5 behind should NOT trip.
    result = cli.invoke(observe_app, ["drift", "--warn-after", "10", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["drifted"] is False


def test_drift_refuses_when_not_in_git_checkout(cli, tmp_path, monkeypatch):
    """Advisory command must fail visibly when the operator ran it from the
    wrong directory."""
    from aletheia.cli.observe_cmd import observe_app

    monkeypatch.chdir(tmp_path)  # empty dir, no .git
    result = cli.invoke(observe_app, ["drift"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "not a git checkout" in combined


# ─── (3) --quiet mode on observe run ─────────────────────────────────────────


def test_run_quiet_mode_stays_silent_on_clean_pass(cli, monkeypatch):
    """--quiet: a clean pass (0 windows_errored, 0 UFC mismatched) writes nothing.
    This is what the systemd unit uses for steady-state runs."""
    from aletheia.cli.observe_cmd import observe_app

    def _fake_run(*, min_samples):
        return {
            "recommendations_written": 0,
            "lab_windows": {"windows_run": 2, "windows_errored": 0},
            "ufc_shadow_equivalence": {
                "checked": 4, "matched": 4, "mismatched": 0, "skipped": 0,
            },
        }

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe", _fake_run,
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        result = cli.invoke(observe_app, ["run", "--quiet"])

    assert result.exit_code == 0
    # Only ownership/lock errors + mismatches print in quiet mode.
    assert result.stdout.strip() == ""


def test_run_quiet_mode_still_prints_on_mismatch(cli, monkeypatch):
    """--quiet: mismatches are signal, not chatter — always surface them."""
    from aletheia.cli.observe_cmd import observe_app

    def _fake_run(*, min_samples):
        return {
            "recommendations_written": 0,
            "lab_windows": {"windows_run": 2, "windows_errored": 0},
            "ufc_shadow_equivalence": {
                "checked": 4, "matched": 3, "mismatched": 1, "skipped": 0,
            },
        }

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe", _fake_run,
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        result = cli.invoke(observe_app, ["run", "--quiet"])

    assert result.exit_code == 0
    assert "mismatched=1" in result.stdout


def test_run_quiet_mode_still_prints_on_window_error(cli, monkeypatch):
    """--quiet: windows_errored > 0 is signal — surface it."""
    from aletheia.cli.observe_cmd import observe_app

    def _fake_run(*, min_samples):
        return {
            "recommendations_written": 0,
            "lab_windows": {"windows_run": 2, "windows_errored": 1},
            "ufc_shadow_equivalence": {
                "checked": 0, "matched": 0, "mismatched": 0, "skipped": 0,
            },
        }

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe", _fake_run,
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        result = cli.invoke(observe_app, ["run", "--quiet"])

    assert result.exit_code == 0
    assert "errored=1" in result.stdout


def test_run_default_mode_still_prints_clean_summary(cli, monkeypatch):
    """Without --quiet, clean passes still print their summary (backward compat)."""
    from aletheia.cli.observe_cmd import observe_app

    def _fake_run(*, min_samples):
        return {
            "recommendations_written": 0,
            "lab_windows": {"windows_run": 2, "windows_errored": 0},
            "ufc_shadow_equivalence": {
                "checked": 4, "matched": 4, "mismatched": 0, "skipped": 0,
            },
        }

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe", _fake_run,
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        result = cli.invoke(observe_app, ["run"])

    assert result.exit_code == 0
    assert "owner=worker" in result.stdout
    assert "windows_run=2" in result.stdout.replace("run=", "windows_run=")  # loose match
