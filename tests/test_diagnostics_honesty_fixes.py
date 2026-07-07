"""Focused regressions for B9-diag batch (#177, #178, #179, #180).

Four small diagnostics / CLI / knowledge-router honesty gaps:

- #177: knowledge router had 3 unreachable role entries (strategy_designer,
        equity_observer, paper_trader) — none of them called
        build_advisory_context, yet _ITEM_TYPES_BY_ROLE listed them, misleading
        readers. Trimmed to just market_observer, the sole live caller.
- #178: doctor's config_consistency probe collapsed severity="info" findings
        to "blocker" via a two-branch conditional — a harmless info hint drove
        doctor exit 1. Now a three-way severity map matches source severity.
- #179: doctor's audit_chain probe excluded errors with unparseable timestamps
        from `post_fix`, so an all-undateable set produced an "info, all
        historical" verdict — hiding a real post-fix corruption that happened
        to also lose its timestamp. Now undateable breaks land as WARN.
- #180: `aletheia inspect config` printed cfg.operator.permission_mode under
        the "agent" block — misled readers about which permission mode gates
        trading. Now prints cfg.agent.permission_mode under "agent" and adds
        an "operator" block with the operator mode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


# ---------------------------------------------------------------------------
# #177 — router keeps only the wired role
# ---------------------------------------------------------------------------


def test_knowledge_router_lists_only_wired_roles():
    """#177: unreachable role entries misled readers about what's actually wired.
    Only market_observer is a live caller of build_advisory_context."""
    from aletheia.knowledge.router import _ITEM_TYPES_BY_ROLE

    assert set(_ITEM_TYPES_BY_ROLE.keys()) == {"market_observer"}, (
        f"router should only carry wired roles; got {sorted(_ITEM_TYPES_BY_ROLE.keys())}"
    )


# ---------------------------------------------------------------------------
# #178 — doctor honours all three source severities
# ---------------------------------------------------------------------------


def test_doctor_maps_info_severity_to_info_not_blocker(tmp_path, monkeypatch):
    """#178: an info-severity config_consistency finding must show up in the
    doctor report as info, not blocker (previously all non-'warning' collapsed
    to blocker → doctor exit 1)."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.diagnostics import doctor as _doctor_mod
    from aletheia.diagnostics.config_consistency import ConfigInconsistency

    findings = [
        ConfigInconsistency(
            field_path="fake.info_finding",
            severity="info",
            message="informational hint",
            fix_hint="optional adjustment",
        ),
        ConfigInconsistency(
            field_path="fake.warning_finding",
            severity="warning",
            message="worth attention",
            fix_hint="consider adjusting",
        ),
        ConfigInconsistency(
            field_path="fake.blocker_finding",
            severity="blocker",
            message="must fix",
            fix_hint="required change",
        ),
    ]
    monkeypatch.setattr(
        "aletheia.diagnostics.config_consistency.check_config_consistency",
        lambda cfg: findings,
    )

    report = _doctor_mod.Report()
    _doctor_mod._probe_config_consistency(report)

    kinds = {c.name: c.severity for c in report.checks}
    assert kinds["config.consistency.fake.info_finding"] == "info"
    assert kinds["config.consistency.fake.warning_finding"] == "warn"
    assert kinds["config.consistency.fake.blocker_finding"] == "blocker"


# ---------------------------------------------------------------------------
# #179 — undateable audit-chain breaks land as WARN, not INFO
# ---------------------------------------------------------------------------


def test_doctor_audit_chain_flags_undateable_breaks_as_warn(tmp_path, monkeypatch):
    """#179: a chain break whose event has no parseable timestamp used to fall
    silently outside `post_fix`, so an all-undateable set was declared
    historical (info). Now undateable breaks are WARN — cannot claim historical."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.diagnostics import doctor as _doctor_mod

    # Stub verify_audit_chain (returns a dict, per files/jsonl.py) to describe
    # one chain break with no parseable timestamp.
    def fake_verify():
        return {
            "valid": False,
            "records": 100,
            "reanchors": 0,
            "errors": ["hash mismatch at record 42"],
            "error_timestamps": [""],  # no parseable timestamp
        }

    monkeypatch.setattr(
        "aletheia.files.jsonl.verify_audit_chain", fake_verify
    )

    report = _doctor_mod.Report()
    _doctor_mod._probe_audit_chain(report)

    audit_rows = [c for c in report.checks if c.name == "trust.audit_chain"]
    assert audit_rows, "audit_chain probe should have added a check"
    check = audit_rows[0]
    assert check.severity == "warn", (
        f"undateable chain breaks must be WARN, not INFO — got {check.severity} "
        f"with detail {check.detail!r}"
    )
    assert "unparseable timestamp" in check.detail.lower()


# ---------------------------------------------------------------------------
# #180 — inspect config prints agent permission_mode under agent
# ---------------------------------------------------------------------------


def test_inspect_config_reports_agent_permission_mode_under_agent_namespace(
    tmp_path, monkeypatch,
):
    """#180: `aletheia inspect config` under the 'agent' block should print
    cfg.agent.permission_mode, not cfg.operator.permission_mode. Two distinct
    permission surfaces — printing the wrong one misled readers about what's
    gating trading."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "PaperAuto"
    save_config(cfg)

    from typer.testing import CliRunner
    from aletheia.cli.inspect_cmd import inspect_app

    result = CliRunner().invoke(inspect_app, ["config", "--json"])
    assert result.exit_code == 0

    import json
    payload = json.loads(result.output)
    assert payload["agent"]["permission_mode"] == "Paper", (
        f"'agent' block must reflect cfg.agent.permission_mode ('Paper'); got "
        f"{payload['agent']['permission_mode']!r}"
    )
    # And the operator's own mode is now visible in its own block, not hidden.
    assert payload["operator"]["permission_mode"] == "PaperAuto"
