from __future__ import annotations


def _row(idx: int, *, notional: float, return_pct: float, stop_pct: float | None, conviction: int) -> dict:
    entry = 100.0
    return {
        "id": f"position-{idx}",
        "entry_price": entry,
        "quantity": notional / entry,
        "realized_pnl": notional * return_pct / 100.0,
        "direction": "long",
        "stop_price": entry * (1.0 - stop_pct / 100.0) if stop_pct else None,
        "conviction": conviction,
        "closed_at": f"2026-01-{idx:02d}T00:00:00+00:00",
    }


def test_counterfactual_is_paired_read_only_and_exposure_bounded():
    from aletheia.analytics.sizing_counterfactual import build_sizing_counterfactual

    rows = [
        _row(1, notional=100, return_pct=10, stop_pct=1, conviction=60),
        _row(2, notional=100, return_pct=-10, stop_pct=10, conviction=70),
        _row(3, notional=100, return_pct=5, stop_pct=5, conviction=80),
    ]
    original = [dict(row) for row in rows]

    report = build_sizing_counterfactual(rows=rows, min_sample_size=3)

    assert report["policy_version"] == "sizing-counterfactual/v2"
    assert report["status"] == "available"
    assert report["sample_size"] == 3
    assert report["policies"]["actual"]["total_pnl_usdt"] == 5.0
    candidate = report["stop_paired_policies"]["risk_at_stop_equalized"]
    assert candidate["sample_size"] == 3
    assert candidate["total_notional_usdt"] == report["stop_paired_policies"]["actual"]["total_notional_usdt"]
    assert report["stop_paired_policies"]["normalization"]["target_total_notional_usdt"] == 300.0
    assert candidate["maximum_notional_usdt"] <= 100.0
    assert rows == original
    assert "no sizing authority" in report["assumptions"][-1]


def test_counterfactual_preserves_unknown_conviction_and_missing_stops():
    from aletheia.analytics.sizing_counterfactual import build_sizing_counterfactual

    rows = [_row(1, notional=25, return_pct=2, stop_pct=None, conviction=70)]
    rows[0]["conviction"] = None

    report = build_sizing_counterfactual(rows=rows, min_sample_size=2)

    assert report["status"] == "insufficient_data"
    assert report["stop_paired_policies"]["reason"] == "no_valid_persisted_stop_distance"
    assert all(bucket["sample_size"] == 0 for bucket in report["conviction_buckets"])


def test_counterfactual_prefers_immutable_entry_stop():
    from aletheia.analytics.sizing_counterfactual import _trade

    row = _row(1, notional=100, return_pct=2, stop_pct=20, conviction=70)
    row["entry_stop_price"] = 99.0

    assert _trade(row)["stop_distance"] == 0.01


def test_counterfactual_rejects_wrong_side_stop_geometry():
    from aletheia.analytics.sizing_counterfactual import _trade

    row = _row(1, notional=100, return_pct=2, stop_pct=1, conviction=70)
    row["entry_stop_price"] = 101.0

    assert _trade(row)["stop_distance"] is None


def test_counterfactual_robustness_halves_match_exposure_independently():
    from aletheia.analytics.sizing_counterfactual import build_sizing_counterfactual

    rows = [
        _row(1, notional=80, return_pct=2, stop_pct=1, conviction=60),
        _row(2, notional=120, return_pct=-1, stop_pct=4, conviction=65),
        _row(3, notional=90, return_pct=1, stop_pct=2, conviction=70),
        _row(4, notional=110, return_pct=-2, stop_pct=8, conviction=75),
    ]

    report = build_sizing_counterfactual(rows=rows, min_sample_size=4)

    assert [item["name"] for item in report["robustness_slices"]] == ["earlier_half", "later_half"]
    for item in report["robustness_slices"]:
        assert item["actual"]["total_notional_usdt"] == item["risk_at_stop_equalized"]["total_notional_usdt"]


def test_proposal_input_read_failure_stays_unknown(monkeypatch):
    from aletheia.analytics.sizing_counterfactual import _attach_immutable_proposal_inputs
    import aletheia.storage.sqlite as sqlite

    rows = [{"proposal_id": "proposal-1"}]
    monkeypatch.setattr(sqlite, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("unavailable")))

    status = _attach_immutable_proposal_inputs(rows)

    assert status == {
        "status": "unavailable",
        "reason": "proposal_input_read_failed",
        "proposal_count": 1,
    }
    assert rows == [{"proposal_id": "proposal-1"}]


def test_opportunity_cli_routes_to_read_only_counterfactual(monkeypatch):
    from aletheia.analytics import sizing_counterfactual
    from aletheia.cli.opportunity import _build_audit_view

    expected = {"status": "insufficient_data", "sample_size": 0}
    monkeypatch.setattr(
        sizing_counterfactual,
        "build_sizing_counterfactual",
        lambda *, days: {**expected, "days": days},
    )
    monkeypatch.setattr(sizing_counterfactual, "render_sizing_counterfactual", lambda report: report["status"])

    report, rendered = _build_audit_view(
        hours=25,
        limit=20,
        stage=None,
        verdict=None,
        symbol=None,
        view="sizing-counterfactual",
    )

    assert report == {**expected, "days": 2}
    assert rendered == "insufficient_data"
