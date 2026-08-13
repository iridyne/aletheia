from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import aletheia.analytics.position_path_observations as analysis


_OPENED_AT = "2026-08-13T00:00:00+00:00"
_CLOSED_AT = "2026-08-13T00:15:00+00:00"
_FIRST_BUCKET_MS = int(
    datetime(2026, 8, 13, tzinfo=timezone.utc).timestamp() * 1000
)


def _position(**overrides) -> dict:
    position = {
        "id": "stop-loss-1",
        "status": "closed",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "entry_price": 100.0,
        "current_price": 90.0,
        "quantity": 1.0,
        "realized_pnl": -12.0,
        "entry_fee": 1.0,
        "exit_fee": 1.0,
        "funding_cost": 0.0,
        "funding_source": "not_applicable",
        "execution_mode": "paper_internal",
        "entry_fill_verified": True,
        "entry_price_source": "local_fill",
        "exit_fill_verified": True,
        "exit_price_source": "local_fill",
        "close_reason": "stop_loss",
        "opened_at": _OPENED_AT,
        "closed_at": _CLOSED_AT,
    }
    position.update(overrides)
    return position


def _rows(*, highs: tuple[float, float, float]) -> list[dict]:
    lows = (99.0, 94.0, 89.0)
    closes = (100.0, 95.0, 91.0)
    return [
        {
            "bucket_open_ms": _FIRST_BUCKET_MS + index * 300_000,
            "evidence_status": "available",
            "evidence_reason": None,
            "bar_high": high,
            "bar_low": lows[index],
            "bar_close": closes[index],
            "exit_plan_version": 1,
            "entry_fee": 1.0,
            "market_type": "spot",
            "funding_cost": None,
            "funding_source": "not_applicable",
        }
        for index, high in enumerate(highs)
    ]


def _report(monkeypatch, position: dict, rows: list[dict]) -> dict:
    monkeypatch.setattr(analysis.repo, "get_position", lambda _position_id: position)
    monkeypatch.setattr(
        analysis.repo,
        "list_position_path_observations",
        lambda _position_id, *, limit: rows,
    )
    return analysis.build_position_path_report(position["id"])


def test_expected_path_starts_after_submillisecond_entry_boundary():
    first, last = analysis._expected_bucket_bounds(
        {
            "opened_at": "2026-08-13T00:00:00.000100+00:00",
            "closed_at": "2026-08-13T00:10:00+00:00",
        }
    )

    assert first == _FIRST_BUCKET_MS + 300_000
    assert last == _FIRST_BUCKET_MS + 300_000


@pytest.mark.parametrize(
    ("position", "highs", "expected_root"),
    [
        (
            _position(realized_pnl=-11.0),
            (105.0, 102.0, 101.0),
            "accounting_mismatch",
        ),
        (
            _position(current_price=101.0, realized_pnl=-1.0),
            (102.0, 101.5, 101.0),
            "cost_flip",
        ),
        (
            _position(),
            (105.0, 102.0, 101.0),
            "profit_giveback",
        ),
        (
            _position(),
            (100.0, 99.0, 98.0),
            "never_favorable_after_entry",
        ),
    ],
)
def test_complete_paths_have_mutually_exclusive_root_causes(
    monkeypatch, position, highs, expected_root
):
    report = _report(monkeypatch, position, _rows(highs=highs))

    decomposition = report["stop_loss_decomposition"]
    assert decomposition["status"] == "available"
    assert decomposition["root_cause"] == expected_root
    assert decomposition["path_coverage_status"] == "complete"
    assert decomposition["expected_bucket_count"] == 3
    assert decomposition["covered_bucket_count"] == 3
    assert decomposition["gross_pnl"] is not None
    assert decomposition["realized_net_pnl"] == position["realized_pnl"]
    assert decomposition["verified_total_cost"] == 2.0
    assert decomposition["accounting_delta"] is not None
    assert decomposition["mfe_pct"] is not None
    assert decomposition["mae_pct"] is not None
    assert "cost_complete_observation_count" not in report
    assert decomposition["giveback_to_exit_pct"] is not None


def test_cost_flip_requires_complete_cost_evidence(monkeypatch):
    position = _position(
        market_type="perpetual",
        current_price=101.0,
        realized_pnl=-1.0,
        funding_cost=None,
        funding_source="unavailable",
    )
    report = _report(monkeypatch, position, _rows(highs=(102.0, 101.5, 101.0)))

    decomposition = report["stop_loss_decomposition"]
    assert decomposition["root_cause"] == "unknown"
    assert decomposition["reason"] == "cost_flip_requires_verified_close_and_cost_evidence"
    assert decomposition["verified_total_cost"] is None
    assert decomposition["accounting_delta"] is None


def test_missing_bucket_is_partial_data_even_when_economics_mismatch(monkeypatch):
    position = _position(realized_pnl=-11.0)
    rows = _rows(highs=(105.0, 102.0, 101.0))
    del rows[1]

    report = _report(monkeypatch, position, rows)

    decomposition = report["stop_loss_decomposition"]
    assert report["path_evidence_status"] == "partial_data"
    assert decomposition["status"] == "partial_data"
    assert decomposition["root_cause"] == "unknown"
    assert decomposition["reason"] == "missing_5m_buckets"
    assert decomposition["expected_bucket_count"] == 3
    assert decomposition["covered_bucket_count"] == 2
    assert decomposition["missing_bucket_count"] == 1
    assert decomposition["mfe_pct"] is None


def test_excursions_never_report_negative_mfe_or_mae():
    mfe, mae = analysis._excursions(
        direction="long",
        entry_price=100.0,
        rows=[
            {"bar_high": 99.0, "bar_low": 101.0},
            {"bar_high": 98.0, "bar_low": 102.0},
        ],
    )

    assert mfe == 0.0
    assert mae == 0.0


def test_profitable_stop_loss_is_not_in_loss_root_cause(monkeypatch):
    position = _position(
        current_price=101.0,
        realized_pnl=0.5,
        entry_fee=0.25,
        exit_fee=0.25,
    )
    report = _report(monkeypatch, position, _rows(highs=(103.0, 102.0, 101.0)))

    decomposition = report["stop_loss_decomposition"]
    assert decomposition["status"] == "not_applicable"
    assert decomposition["root_cause"] == "not_a_loss"


def test_cohort_counts_only_verified_losses_in_classification_denominator(monkeypatch):
    positions = [
        _position(id="classified", realized_pnl=-1.0),
        _position(id="unknown", realized_pnl=-1.0),
        _position(id="profitable", realized_pnl=0.5),
    ]
    decompositions = {
        "classified": {
            "status": "available",
            "root_cause": "profit_giveback",
            "reason": "observed_favorable_excursion_ended_in_stop_loss",
            "path_coverage_status": "complete",
        },
        "unknown": {
            "status": "partial_data",
            "root_cause": "unknown",
            "reason": "missing_5m_buckets",
            "path_coverage_status": "partial_data",
        },
        "profitable": {
            "status": "not_applicable",
            "root_cause": "not_a_loss",
            "reason": "stop_loss_close_did_not_realize_a_net_loss",
            "path_coverage_status": "complete",
        },
    }
    monkeypatch.setattr(
        analysis,
        "_recent_stop_loss_positions",
        lambda *, cutoff, limit: positions,
    )
    monkeypatch.setattr(
        analysis,
        "assess_reportable_outcome",
        lambda _position: SimpleNamespace(eligible=True, reason="eligible"),
    )
    monkeypatch.setattr(
        analysis,
        "build_position_path_report",
        lambda position_id: {
            "stop_loss_decomposition": decompositions[position_id]
        },
    )

    audit = analysis.build_stop_loss_path_audit()

    assert audit["loss_sample_count"] == 2
    assert audit["classified_count"] == 1
    assert audit["unknown_count"] == 1
    assert audit["not_a_loss_count"] == 1
    assert audit["root_cause_counts"] == {
        "profit_giveback": 1,
        "unknown": 1,
    }
    assert audit["reason_counts"]["missing_5m_buckets"] == 1


def test_cohort_excludes_non_reportable_outcomes_before_path_classification(
    monkeypatch,
):
    excluded = _position(id="excluded")
    monkeypatch.setattr(
        analysis,
        "_recent_stop_loss_positions",
        lambda *, cutoff, limit: [excluded],
    )
    monkeypatch.setattr(
        analysis,
        "assess_reportable_outcome",
        lambda _position: SimpleNamespace(
            eligible=False, reason="missing_attribution"
        ),
    )
    monkeypatch.setattr(
        analysis,
        "build_position_path_report",
        lambda _position_id: pytest.fail(
            "excluded outcome reached path classification"
        ),
    )

    audit = analysis.build_stop_loss_path_audit()

    assert audit["stop_loss_position_count"] == 1
    assert audit["eligible_outcome_count"] == 0
    assert audit["excluded_outcome_count"] == 1
    assert audit["outcome_exclusion_reasons"] == {"missing_attribution": 1}
    assert audit["loss_sample_count"] == 0
    assert audit["positions"][0]["included_in_loss_denominator"] is False
