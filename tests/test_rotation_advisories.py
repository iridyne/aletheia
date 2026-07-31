"""L5 back-linkage advisory tests.

Advisories from counterfactual evidence must never mutate config, DB, or
scheduler state. These tests pin both the signal semantics and the
observe-only invariant.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone


def test_no_positive_band_emits_info_advisory():
    from aletheia.counterfactual.advisories import build_rotation_advisories

    report = {
        "window_days": 8,
        "policy_epoch_id": "epoch-abc",
        "total_candidates": 42,
        "conviction_gate": 55,
        "crossover_band": None,
        "bands": [
            {"band": "<40", "avg_net_pct": -0.5, "n": 10},
            {"band": "55-60", "avg_net_pct": -0.1, "n": 20},
        ],
        "errors": [],
    }
    advisories = build_rotation_advisories(
        calibration_report=report,
        now=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert len(advisories) == 1
    adv = advisories[0]
    assert adv.advisory_type == "policy_epoch_rotation"
    assert adv.version == "v1"
    assert adv.signal == "calibration_no_positive_band"
    assert adv.severity == "info"
    assert adv.policy_epoch_id == "epoch-abc"
    assert adv.observe_only is True
    assert "owner may wish to review" in adv.recommendation.lower()
    # Evidence embeds a bounded, self-contained view (no external references).
    assert adv.evidence["report"] == "counterfactual.calibration.build_calibration"
    assert adv.evidence["window_days"] == 8
    assert len(adv.evidence["bands_summary"]) == 2


def test_positive_band_present_emits_no_advisory():
    from aletheia.counterfactual.advisories import build_rotation_advisories

    report = {
        "window_days": 8,
        "policy_epoch_id": "epoch-abc",
        "total_candidates": 200,
        "conviction_gate": 55,
        "crossover_band": "55-60",
        "bands": [
            {"band": "<40", "avg_net_pct": -0.5, "n": 10},
            {"band": "55-60", "avg_net_pct": 0.2, "n": 20},
        ],
        "errors": [],
    }
    assert (
        build_rotation_advisories(
            calibration_report=report,
            now=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        == []
    )


def test_report_with_errors_does_not_produce_advisories():
    from aletheia.counterfactual.advisories import build_rotation_advisories

    report = {"errors": ["Database is not initialized."], "bands": []}
    assert (
        build_rotation_advisories(
            calibration_report=report,
            now=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        == []
    )


def test_empty_bands_do_not_produce_advisory():
    """No bands = no evidence at all; the advisory would be noise."""
    from aletheia.counterfactual.advisories import build_rotation_advisories

    report = {
        "window_days": 8,
        "crossover_band": None,
        "bands": [],
        "errors": [],
    }
    assert (
        build_rotation_advisories(calibration_report=report) == []
    )


def test_advisory_observe_only_invariant():
    """The advisories module must not import or call any mutation path."""
    from aletheia.counterfactual import advisories

    src = inspect.getsource(advisories)

    # Strip the module docstring before scanning so prose descriptions
    # mentioning forbidden names are not flagged as calls.
    module_doc = advisories.__doc__ or ""
    code = src.replace(module_doc, "")

    # No config mutation call
    assert "save_config(" not in code

    # No policy_epoch mutation call
    assert "ensure_skip_remediation_policy_epoch(" not in code
    assert "persist_policy_epoch(" not in code
    assert "build_policy_epoch(" not in code

    # No mutation-flavoured import
    forbidden_imports = (
        "from aletheia.config.loader import save_config",
        "from aletheia.agent_shell.skip_policy import ensure_skip_remediation_policy_epoch",
        "from aletheia.agent_shell.skip_policy import persist_policy_epoch",
    )
    for line in forbidden_imports:
        assert line not in code, f"advisories module must not import {line}"

    # No DB write hints
    for forbidden in ("repo.insert_", "repo.update_", "repo.delete_", "execute_write"):
        assert forbidden not in code, f"advisories module must not call {forbidden}"

    # Every RotationAdvisory instance in this module is constructed with
    # observe_only=True. Any bare RotationAdvisory( without observe_only=True
    # kwarg would be a regression.
    constructions = src.count("RotationAdvisory(")
    observe_true = src.count("observe_only=True")
    # The dataclass definition itself uses "observe_only: bool" but no True
    # literal in the class body; the True literals appear only at construction
    # sites (and possibly in test/render helpers). Every construction must set
    # observe_only=True.
    assert observe_true >= constructions - 1, (
        "each RotationAdvisory construction must explicitly set observe_only=True"
    )


def test_calibration_report_attaches_advisories_field_even_when_empty(monkeypatch):
    """The report always carries rotation_advisories (possibly empty list)."""
    from aletheia.counterfactual import calibration

    # Stub list_deduped_shadows so we control the report contents deterministically.
    monkeypatch.setattr(
        calibration,
        "list_deduped_shadows",
        lambda **kwargs: [
            {
                "trade_score": 60.0,
                "net_pnl_pct": 0.5,
                "was_selected": 1,
                "symbol": "BTC/USDT",
                "direction": "long",
            },
        ],
    )
    report = calibration.build_calibration(days=8)
    assert "rotation_advisories" in report
    assert isinstance(report["rotation_advisories"], list)


def test_calibration_report_advisory_when_no_positive_band(monkeypatch):
    from aletheia.counterfactual import calibration

    monkeypatch.setattr(
        calibration,
        "list_deduped_shadows",
        lambda **kwargs: [
            {
                "trade_score": 30.0,
                "net_pnl_pct": -0.4,
                "was_selected": 0,
                "symbol": "BTC/USDT",
                "direction": "long",
            },
            {
                "trade_score": 60.0,
                "net_pnl_pct": -0.2,
                "was_selected": 1,
                "symbol": "BTC/USDT",
                "direction": "long",
            },
        ],
    )
    report = calibration.build_calibration(days=8, policy_epoch_id="epoch-under-test")
    advisories = report["rotation_advisories"]
    assert len(advisories) == 1
    adv = advisories[0]
    assert adv["signal"] == "calibration_no_positive_band"
    assert adv["observe_only"] is True
    assert adv["policy_epoch_id"] == "epoch-under-test"
