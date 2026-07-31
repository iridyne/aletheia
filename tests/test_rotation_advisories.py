"""L5 back-linkage advisory tests.

Advisories from counterfactual evidence must never mutate config, DB, or
scheduler state. These tests pin both the signal semantics and the
observe-only invariant.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone


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
    from aletheia.counterfactual import advisories as advisories_mod
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
    # The report will attach a harm_signal too if a valid epoch_start is
    # available; stub _epoch_start to None so this test only exercises the
    # no_positive_band branch.
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: None)

    report = calibration.build_calibration(days=8, policy_epoch_id="epoch-under-test")
    advisories = report["rotation_advisories"]
    assert len(advisories) == 1
    adv = advisories[0]
    assert adv["signal"] == "calibration_no_positive_band"
    assert adv["observe_only"] is True
    assert adv["policy_epoch_id"] == "epoch-under-test"


# ─── Phase 3.5: harm_signal ─────────────────────────────────────────────────

def _stub_deduped(mapping):
    """Return a fake list_deduped_shadows keyed on (since_iso, until_iso).

    Each mapping value is a list of {"net_pnl_pct": float} dicts to return
    when the caller's window matches.
    """
    def _fake(**kwargs):
        since = kwargs.get("since_iso")
        until = kwargs.get("until_iso")
        key = (since, until)
        return list(mapping.get(key, []))
    return _fake


def test_harm_signal_emits_concern_on_moderate_drop(monkeypatch):
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)  # 45 days into the epoch
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)

    # 60 rows in baseline (avg 0.5%), 60 rows in current window (avg 0.1%).
    # Drop of 0.4pp → concern (>= 0.3pp), but not strong (< 0.7pp) even if
    # n reached 100.
    baseline_end = (epoch_start + timedelta(days=8)).isoformat()
    current_start = (now - timedelta(days=8)).isoformat()
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        _stub_deduped({
            (epoch_start.isoformat(), baseline_end): [
                {"net_pnl_pct": 0.5}
            ] * 60,
            (current_start, now.isoformat()): [
                {"net_pnl_pct": 0.1}
            ] * 60,
        }),
    )

    report = {
        "policy_epoch_id": "epoch-alpha",
        "bands": [{"band": "55-60", "avg_net_pct": 0.1, "n": 60}],
        "crossover_band": "55-60",
        "errors": [],
    }
    advisories = advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    )
    assert len(advisories) == 1
    adv = advisories[0]
    assert adv.signal == "harm_signal"
    assert adv.severity == "concern"
    assert adv.policy_epoch_id == "epoch-alpha"
    assert adv.observe_only is True
    assert adv.evidence["reference"] == "epoch_opening_window"
    assert adv.evidence["baseline_n"] == 60
    assert adv.evidence["current_n"] == 60
    assert adv.evidence["delta_pp"] == -0.4


def test_harm_signal_emits_strong_concern_on_large_drop_with_sample_size(monkeypatch):
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)

    baseline_end = (epoch_start + timedelta(days=8)).isoformat()
    current_start = (now - timedelta(days=8)).isoformat()
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        _stub_deduped({
            (epoch_start.isoformat(), baseline_end): [
                {"net_pnl_pct": 0.6}
            ] * 120,
            (current_start, now.isoformat()): [
                {"net_pnl_pct": -0.2}
            ] * 120,
        }),
    )

    report = {
        "policy_epoch_id": "epoch-alpha",
        "bands": [{"band": "55-60", "avg_net_pct": -0.2, "n": 60}],
        "crossover_band": "55-60",
        "errors": [],
    }
    advisories = advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    )
    assert len(advisories) == 1
    adv = advisories[0]
    assert adv.signal == "harm_signal"
    assert adv.severity == "strong_concern"
    assert adv.evidence["delta_pp"] == -0.8


def test_harm_signal_stays_concern_when_large_drop_but_insufficient_strong_n(monkeypatch):
    """Delta qualifies for strong_concern but n is below the strong threshold."""
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)

    baseline_end = (epoch_start + timedelta(days=8)).isoformat()
    current_start = (now - timedelta(days=8)).isoformat()
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        _stub_deduped({
            (epoch_start.isoformat(), baseline_end): [
                {"net_pnl_pct": 0.6}
            ] * 80,
            (current_start, now.isoformat()): [
                {"net_pnl_pct": -0.2}
            ] * 80,
        }),
    )

    report = {"policy_epoch_id": "e", "bands": [{"band": "x", "avg_net_pct": 0.1, "n": 1}], "crossover_band": "x", "errors": []}
    advisories = advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    )
    assert len(advisories) == 1 and advisories[0].signal == "harm_signal"
    assert advisories[0].severity == "concern"  # not strong, because n < 100


def test_harm_signal_silent_when_drop_below_concern_threshold(monkeypatch):
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)

    baseline_end = (epoch_start + timedelta(days=8)).isoformat()
    current_start = (now - timedelta(days=8)).isoformat()
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        _stub_deduped({
            (epoch_start.isoformat(), baseline_end): [
                {"net_pnl_pct": 0.5}
            ] * 60,
            (current_start, now.isoformat()): [
                {"net_pnl_pct": 0.3}  # drop of 0.2pp — below concern_pp=0.3
            ] * 60,
        }),
    )

    report = {"policy_epoch_id": "e", "bands": [{"band": "x", "avg_net_pct": 0.3, "n": 1}], "crossover_band": "x", "errors": []}
    assert advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    ) == []


def test_harm_signal_silent_when_baseline_sample_too_small(monkeypatch):
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)

    baseline_end = (epoch_start + timedelta(days=8)).isoformat()
    current_start = (now - timedelta(days=8)).isoformat()
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        _stub_deduped({
            (epoch_start.isoformat(), baseline_end): [
                {"net_pnl_pct": 0.5}
            ] * 10,  # under concern_min_n=50
            (current_start, now.isoformat()): [
                {"net_pnl_pct": -0.5}
            ] * 60,
        }),
    )

    report = {"policy_epoch_id": "e", "bands": [{"band": "x", "avg_net_pct": -0.5, "n": 1}], "crossover_band": "x", "errors": []}
    assert advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    ) == []


def test_harm_signal_silent_when_epoch_too_young(monkeypatch):
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)  # 5 days into the epoch
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)
    # list_deduped_shadows should never be reached because separation fails;
    # supply a stub that would explode if hit, to prove the early-return path.
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not query when epoch is too young"),
        ),
    )

    report = {"policy_epoch_id": "e", "bands": [{"band": "x", "avg_net_pct": 0.1, "n": 1}], "crossover_band": "x", "errors": []}
    assert advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    ) == []


def test_harm_signal_silent_when_epoch_start_unknown(monkeypatch):
    from aletheia.counterfactual import advisories as advisories_mod

    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: None)
    monkeypatch.setattr(
        advisories_mod,
        "list_deduped_shadows",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not query without a known epoch start"),
        ),
    )

    report = {"policy_epoch_id": "unknown-epoch", "bands": [{"band": "x", "avg_net_pct": 0.0, "n": 1}], "crossover_band": "x", "errors": []}
    assert advisories_mod.build_rotation_advisories(
        calibration_report=report, now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    ) == []


def test_harm_signal_failure_does_not_break_report(monkeypatch):
    """If list_deduped_shadows raises, the advisory is silently dropped."""
    from aletheia.counterfactual import advisories as advisories_mod

    epoch_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(advisories_mod, "_epoch_start", lambda epoch_id: epoch_start)

    def _boom(**kwargs):
        raise RuntimeError("DB is on fire")

    monkeypatch.setattr(advisories_mod, "list_deduped_shadows", _boom)

    report = {"policy_epoch_id": "e", "bands": [{"band": "x", "avg_net_pct": 0.0, "n": 1}], "crossover_band": "x", "errors": []}
    # Must not raise. Must return no advisories.
    assert advisories_mod.build_rotation_advisories(
        calibration_report=report, now=now,
    ) == []


def test_list_deduped_shadows_rejects_ambiguous_window():
    """Window intent must be explicit — days= and since_iso= are mutually exclusive."""
    import pytest

    from aletheia.counterfactual.trades import list_deduped_shadows

    with pytest.raises(ValueError):
        list_deduped_shadows()  # neither specified
    with pytest.raises(ValueError):
        list_deduped_shadows(days=8, since_iso="2026-05-01T00:00:00+00:00")
