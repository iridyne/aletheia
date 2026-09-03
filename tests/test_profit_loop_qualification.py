"""Tests for profit_loop_qualification — #988 whole-policy profitability verdict."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import aletheia.analytics.outcome_integrity as oi_module
import aletheia.analytics.profit_loop_qualification as qual_module
from aletheia.analytics.profit_loop_qualification import (
    QUALIFICATION_CONTRACT_VERSION,
    REQUIRED_REGISTRATION_KEYS,
    compile_qualification_verdict,
)
from aletheia.cli.inspect_cmd import inspect_app
from typer.testing import CliRunner


runner = CliRunner()

_CUTOFF = "2026-02-01T00:00:00+00:00"

# Full preregistered dict — cutoff_at is now a required registration field.
_REGISTRATION = {
    "deploy_sha": "abc123" * 10,
    "runtime_session_id": "session-001",
    "execution_mode": "paper_internal",
    "policy_epoch_id": "epoch-001",
    "improvement_id": "breakeven_after_1r/v1",
    "registration_at": "2026-01-01T00:00:00+00:00",
    "stopping_rule": "30_days_or_50_genuine_closes",
    "cost_model_id": "default/v1",
    "cutoff_at": _CUTOFF,
    "max_drawdown_cap": 0.20,
}

_BASE_DATE = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_outcomes(pnls: list[float], opened_at: str = "2026-01-02T00:00:00+00:00") -> list[dict]:
    return [
        {
            "realized_pnl": pnl,
            "opened_at": opened_at,
            "closed_at": (_BASE_DATE + timedelta(days=i)).isoformat(),
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-001",
            "status": "closed",
        }
        for i, pnl in enumerate(pnls)
    ]


def _patch_outcomes(monkeypatch, pnls: list[float]) -> None:
    _patch_rows(monkeypatch, _make_outcomes(pnls))


def _complete_cohort(rows: list[dict]) -> oi_module.CompleteOutcomeCohort:
    return oi_module.CompleteOutcomeCohort(
        rows=tuple(rows),
        assessments=tuple(
            oi_module.OutcomeAssessment(eligible=True, reason="eligible")
            for _row in rows
        ),
        source_raw_event_count=len(rows),
        loaded_raw_event_count=len(rows),
        cohort_complete=True,
        cohort_state="available",
        ordered_source_digest="cohort-digest",
        identity={
            "execution_mode": "paper_internal",
            "runtime_session_id": None,
            "closed_after": _REGISTRATION["registration_at"],
            "closed_before": _REGISTRATION["cutoff_at"],
        },
    )


def _patch_rows(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(
        qual_module,
        "read_complete_outcome_cohort",
        lambda **_: _complete_cohort(rows),
    )


# ---------------------------------------------------------------------------
# Happy-path verdict tests
# ---------------------------------------------------------------------------

def test_proven_verdict_all_gates_pass(monkeypatch):
    # 15 uniform wins + 5 uniform losses → net > 0, PF > 1, top1 ≈ 5% < 25%, top5 ≈ 25% < 50%
    pnls = [1.0] * 15 + [-0.5] * 5
    _patch_outcomes(monkeypatch, pnls)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["contract_version"] == QUALIFICATION_CONTRACT_VERSION
    assert result["verdict"] == "minimum_profitable_loop_proven"
    assert result["metrics"]["net_realized_pnl"] > 0
    assert result["metrics"]["profit_factor"] > 1.0
    assert result["content_hash"]


def test_zero_loss_cohort_proven(monkeypatch):
    """All-win cohort (zero losses) must pass the profit_factor gate (#1148)."""
    pnls = [1.0] * 10 + [0.5] * 5  # spread across 15 days, no losses
    _patch_outcomes(monkeypatch, pnls)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "minimum_profitable_loop_proven"
    assert result["metrics"]["profit_factor"] is None  # serialised as null (no_losses)
    assert result["metrics"]["profit_factor_state"] == "no_losses"
    assert result["gates"]["profit_factor_above_1"]["pass"] is True


def test_zero_loss_artifact_is_json_safe(monkeypatch):
    """Zero-loss cohort artifact must be serialisable as standards-compliant JSON."""
    import json as _json
    _patch_outcomes(monkeypatch, [1.0] * 10)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    serialised = _json.dumps(result, allow_nan=False)  # raises on Infinity / NaN
    decoded = _json.loads(serialised)
    assert decoded["metrics"]["profit_factor_state"] == "no_losses"


def test_all_losses_cohort_rejected(monkeypatch):
    """All-loss cohort: net_pnl_positive and profit_factor_above_1 both fail."""
    _patch_outcomes(monkeypatch, [-1.0] * 5)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    failed = result["gates"]["failed_gates"]
    assert "net_pnl_positive" in failed
    assert "profit_factor_above_1" in failed


def test_flat_trades_only_rejected(monkeypatch):
    """All-flat cohort (no wins, no losses): profit_factor_state='no_trades', gate fails."""
    _patch_outcomes(monkeypatch, [0.0] * 5)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert result["metrics"]["profit_factor_state"] == "no_trades"
    assert result["gates"]["profit_factor_above_1"]["pass"] is False


def test_rejected_verdict_negative_pnl(monkeypatch):
    _patch_outcomes(monkeypatch, [-5.0, -3.0, 1.0, -2.0])
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert "net_pnl_positive" in result["gates"]["failed_gates"]


def test_rejected_verdict_concentration_breach(monkeypatch):
    # One trade is 90% of all absolute PnL — breaches top-1 cap
    _patch_outcomes(monkeypatch, [90.0, 1.0, 1.0, 1.0, 1.0, -5.0])
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert "concentration_top1" in result["gates"]["failed_gates"]


@pytest.mark.parametrize("cap_key", ["concentration_top1_cap", "concentration_top5_cap"])
def test_explicit_zero_concentration_cap_is_preserved(monkeypatch, cap_key):
    _patch_outcomes(monkeypatch, [1.0] * 15 + [-0.5] * 5)

    result = compile_qualification_verdict(
        registration={**_REGISTRATION, cap_key: 0.0}
    )

    gate_key = cap_key.removesuffix("_cap")
    assert result["gates"][gate_key]["pass"] is False
    assert result["gates"][gate_key]["threshold"] == "<= 0.0"


@pytest.mark.parametrize("cap_key", ["concentration_top1_cap", "concentration_top5_cap"])
@pytest.mark.parametrize("invalid", [None, "bad", math.nan, math.inf, -0.01, 1.01])
def test_present_invalid_concentration_cap_fails_before_outcome_read(
    monkeypatch, cap_key, invalid
):
    def unexpected_read(**_kwargs):
        pytest.fail("invalid registration must fail before reading outcomes")

    monkeypatch.setattr(qual_module, "read_complete_outcome_cohort", unexpected_read)

    with pytest.raises(ValueError, match=cap_key):
        compile_qualification_verdict(
            registration={**_REGISTRATION, cap_key: invalid}
        )


def test_omitted_concentration_caps_keep_documented_defaults(monkeypatch):
    _patch_outcomes(monkeypatch, [1.0] * 15 + [-0.5] * 5)

    result = compile_qualification_verdict(registration=_REGISTRATION)

    assert result["gates"]["concentration_top1"]["threshold"] == "<= 0.25"
    assert result["gates"]["concentration_top5"]["threshold"] == "<= 0.5"


def test_insufficient_data_no_outcomes(monkeypatch):
    _patch_outcomes(monkeypatch, [])
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "insufficient_data"


def test_drawdown_gate_enforced_when_cap_registered(monkeypatch):
    # 100 loss then recovery — large drawdown > 5% cap
    _patch_outcomes(monkeypatch, [1.0, -50.0, 60.0, 1.0, 1.0, 1.0, 1.0])
    reg = {**_REGISTRATION, "max_drawdown_cap": 0.05}
    result = compile_qualification_verdict(registration=reg)
    assert result["gates"]["max_drawdown_bounded"]["pass"] is False
    assert result["verdict"] == "minimum_profitable_loop_rejected"


def test_drawdown_gate_recorded_only_without_cap(monkeypatch):
    _patch_outcomes(monkeypatch, [10.0, 5.0, -2.0, 8.0, 3.0])
    reg = {k: v for k, v in _REGISTRATION.items() if k != "max_drawdown_cap"}
    result = compile_qualification_verdict(registration=reg)
    # Gate is recorded but not enforced
    assert result["gates"]["max_drawdown_bounded"]["pass"] is None


def test_artifact_is_hashed_and_reproducible(monkeypatch):
    _patch_outcomes(monkeypatch, [5.0, -1.0, 3.0, 2.0])
    r1 = compile_qualification_verdict(registration=_REGISTRATION)
    r2 = compile_qualification_verdict(registration=_REGISTRATION)
    assert r1["content_hash"]
    assert len(r1["content_hash"]) == 64  # SHA-256 hex
    assert r1["content_hash"] == r2["content_hash"], "identical inputs must produce identical content_hash"


def test_artifact_includes_registration_hash(monkeypatch):
    _patch_outcomes(monkeypatch, [5.0, -1.0, 3.0, 2.0])
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert "registration_hash" in result
    assert len(result["registration_hash"]) == 64


def test_changed_registration_produces_different_registration_hash(monkeypatch):
    """Different registration blobs yield different registration_hash values."""
    _patch_outcomes(monkeypatch, [5.0, -1.0, 3.0, 2.0])
    r1 = compile_qualification_verdict(registration=_REGISTRATION)
    reg2 = {**_REGISTRATION, "max_drawdown_cap": 0.10}
    r2 = compile_qualification_verdict(registration=reg2)
    assert r1["registration_hash"] != r2["registration_hash"]


# ---------------------------------------------------------------------------
# Required-field enforcement tests (preregistration contract)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_key", list(REQUIRED_REGISTRATION_KEYS))
def test_missing_required_field_raises(missing_key):
    reg = {k: v for k, v in _REGISTRATION.items() if k != missing_key}
    with pytest.raises(ValueError, match="missing required keys"):
        compile_qualification_verdict(registration=reg)


def test_cutoff_at_in_registration_is_required():
    """cutoff_at must be in the registration file, not supplied at call time."""
    reg = {k: v for k, v in _REGISTRATION.items() if k != "cutoff_at"}
    with pytest.raises(ValueError, match="missing required keys"):
        compile_qualification_verdict(registration=reg)


# ---------------------------------------------------------------------------
# Temporal consistency enforcement tests
# ---------------------------------------------------------------------------

def test_registration_at_equal_to_cutoff_raises():
    reg = {**_REGISTRATION, "registration_at": _CUTOFF, "cutoff_at": _CUTOFF}
    with pytest.raises(ValueError, match="must be strictly before"):
        compile_qualification_verdict(registration=reg)


def test_registration_at_after_cutoff_raises():
    reg = {
        **_REGISTRATION,
        "registration_at": "2026-03-01T00:00:00+00:00",
        "cutoff_at": "2026-02-01T00:00:00+00:00",
    }
    with pytest.raises(ValueError, match="must be strictly before"):
        compile_qualification_verdict(registration=reg)


# ---------------------------------------------------------------------------
# CLI optional --cutoff validation tests
# ---------------------------------------------------------------------------

def test_cli_command_emits_versioned_json(monkeypatch, tmp_path):
    outcomes = _make_outcomes([1.0] * 15 + [-0.5] * 5)
    _patch_rows(monkeypatch, outcomes)

    reg_file = tmp_path / "registration.json"
    reg_file.write_text(json.dumps(_REGISTRATION), encoding="utf-8")

    # --cutoff omitted: cutoff_at comes entirely from the registration file
    result = runner.invoke(
        inspect_app,
        ["profit-loop-qualification", "--registration", str(reg_file), "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["contract_version"] == QUALIFICATION_CONTRACT_VERSION
    assert data["verdict"] in {
        "minimum_profitable_loop_proven",
        "minimum_profitable_loop_rejected",
        "insufficient_data",
    }


def test_cli_matching_optional_cutoff_accepted(monkeypatch, tmp_path):
    """--cutoff that matches registration['cutoff_at'] is accepted."""
    outcomes = _make_outcomes([1.0] * 5 + [-0.5] * 2)
    _patch_rows(monkeypatch, outcomes)
    reg_file = tmp_path / "reg.json"
    reg_file.write_text(json.dumps(_REGISTRATION), encoding="utf-8")

    result = runner.invoke(
        inspect_app,
        ["profit-loop-qualification", "--registration", str(reg_file),
         "--cutoff", _CUTOFF, "--json"],
    )
    assert result.exit_code == 0, result.output


def test_cli_cutoff_mismatch_rejected(tmp_path):
    """--cutoff that differs from registration['cutoff_at'] is rejected."""
    reg_file = tmp_path / "reg.json"
    reg_file.write_text(json.dumps(_REGISTRATION), encoding="utf-8")

    result = runner.invoke(
        inspect_app,
        ["profit-loop-qualification", "--registration", str(reg_file),
         "--cutoff", "2026-03-01T00:00:00+00:00", "--json"],
    )
    assert result.exit_code != 0
    # Rich may wrap the message across lines; check for stable substrings on each side.
    assert "does not" in result.output
    assert "match" in result.output


# ---------------------------------------------------------------------------
# Identity filter test (from #1145 fix)
# ---------------------------------------------------------------------------

def test_identity_filter_prevents_epoch_pooling(monkeypatch):
    """Two profitable epochs in the same window: only the registered one contributes."""
    epoch_a = [
        {
            "realized_pnl": 10.0,
            "opened_at": "2026-01-02T00:00:00+00:00",
            "closed_at": f"2026-01-{2 + i:02d}T12:00:00+00:00",
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-other",
        }
        for i in range(6)
    ]
    epoch_b = [
        {
            "realized_pnl": -3.0,
            "opened_at": "2026-01-02T00:00:00+00:00",
            "closed_at": f"2026-01-{10 + i:02d}T12:00:00+00:00",
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-001",
        }
        for i in range(4)
    ]

    _patch_rows(monkeypatch, epoch_a + epoch_b)

    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "minimum_profitable_loop_rejected"
    assert result["metrics"]["total_eligible_closes"] == 4
    assert result["metrics"]["net_realized_pnl"] < 0
    assert result["funnel"]["identity_mismatched_count"] == 6


# ---------------------------------------------------------------------------
# #1261 — UTC-normalized timestamp comparisons
# ---------------------------------------------------------------------------

def test_utc_equivalent_timestamps_all_included(monkeypatch):
    """opened_at as Z, +00:00, and -05:00 (adjusted to UTC) all ≥ registration_at are included."""
    # registration_at is 2026-01-01T00:00:00+00:00
    # opened_at variants that are UTC-equal to 2026-01-02T12:00:00Z
    variants = [
        "2026-01-02T12:00:00Z",
        "2026-01-02T12:00:00+00:00",
        "2026-01-02T07:00:00-05:00",  # == 12:00 UTC
    ]
    outcomes = [
        {
            "realized_pnl": 1.0,
            "opened_at": opened,
            "closed_at": f"2026-01-0{3 + i}T12:00:00+00:00",
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-001",
            "status": "closed",
        }
        for i, opened in enumerate(variants)
    ]
    _patch_rows(monkeypatch, outcomes)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["metrics"]["total_eligible_closes"] == 3


def test_positive_offset_before_registration_excluded(monkeypatch):
    """opened_at with positive offset whose UTC instant is before registration_at → excluded."""
    # registration_at is 2026-01-01T00:00:00Z
    # 2025-12-31T20:00:00-05:00 = 2026-01-01T01:00:00Z which is AFTER — use something before
    # 2025-12-31T22:00:00+03:00 = 2025-12-31T19:00:00Z which is BEFORE registration_at
    outcomes = [
        {
            "realized_pnl": 1.0,
            "opened_at": "2025-12-31T22:00:00+03:00",  # UTC: 2025-12-31T19:00:00Z — before reg
            "closed_at": "2026-01-03T12:00:00+00:00",
            "execution_mode": "paper_internal",
            "runtime_session_id": "session-001",
            "status": "closed",
        }
    ]
    _patch_rows(monkeypatch, outcomes)
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["metrics"]["total_eligible_closes"] == 0


# ---------------------------------------------------------------------------
# #1249 — Loss-first drawdown from starting_equity
# ---------------------------------------------------------------------------

def test_loss_first_drawdown_from_starting_equity(monkeypatch):
    """[-1, -1, +5] cohort: drawdown correctly reflects loss from starting_equity."""
    _patch_outcomes(monkeypatch, [-1.0, -1.0, 5.0])
    # starting_equity=10: peak=10, trough=8 after two -1s, dd=2/10=0.2
    result = compile_qualification_verdict(
        registration=_REGISTRATION, starting_equity=10.0
    )
    assert result["metrics"]["max_drawdown_from_peak"] == pytest.approx(0.2)


def test_loss_first_drawdown_from_zero_starting_equity(monkeypatch):
    """[-1, -1, +5] cohort with no starting_equity: peak=3, trough computed correctly."""
    _patch_outcomes(monkeypatch, [-1.0, -1.0, 5.0])
    # starting_equity defaults to 0: peak goes to 3 (after +5), but drawdown from 0
    # -1: equity=-1, peak=0, peak<=0 and equity<peak → max_dd=1.0
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["metrics"]["max_drawdown_from_peak"] == 1.0


# ---------------------------------------------------------------------------
# #1251 — Cohort truncation (10k cap)
# ---------------------------------------------------------------------------

def test_unavailable_cohort_returns_hashed_insufficient_data(monkeypatch):
    unavailable = oi_module.CompleteOutcomeCohort(
        rows=(),
        assessments=(),
        source_raw_event_count=None,
        loaded_raw_event_count=0,
        cohort_complete=False,
        cohort_state="unavailable",
        ordered_source_digest=None,
        identity={
            "execution_mode": "paper_internal",
            "runtime_session_id": None,
            "closed_after": _REGISTRATION["registration_at"],
            "closed_before": _REGISTRATION["cutoff_at"],
        },
    )
    monkeypatch.setattr(
        qual_module, "read_complete_outcome_cohort", lambda **_: unavailable
    )
    result = compile_qualification_verdict(registration=_REGISTRATION)
    assert result["verdict"] == "insufficient_data"
    assert result["funnel"]["source_raw_event_count"] is None
    assert result["funnel"]["cohort_complete"] is False
    assert result["content_hash"]
    stable = {
        key: value
        for key, value in result.items()
        if key not in {"content_hash", "generated_at"}
    }
    from aletheia.storage.hashing import sha256_json

    assert result["content_hash"] == sha256_json(stable)


def test_loss_before_newest_10000_is_included_in_sqlite_qualification():
    """The authority verdict must evaluate the complete registered cohort."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO opportunities (id, symbol, market) VALUES (?, ?, ?)",
        ("cohort-opportunity", "BTC/USDT", "crypto"),
    )
    db.execute(
        """
        INSERT INTO trade_proposals (
            id, opportunity_id, symbol, market_type, direction, execution_mode
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "cohort-proposal",
            "cohort-opportunity",
            "BTC/USDT",
            "usdt_futures",
            "long",
            "paper_internal",
        ),
    )

    opened_at = "2026-08-01T01:00:00+00:00"
    start = datetime(2026, 8, 2, tzinfo=timezone.utc)
    pnls = [-10_000.0, *([1.0] * 6_000), *([-0.5] * 4_000)]
    orders = []
    positions = []
    for index, pnl in enumerate(pnls):
        position_id = f"cohort-position-{index:05d}"
        order_id = f"cohort-order-{index:05d}"
        closed_at = (start + timedelta(seconds=index * 10)).isoformat()
        orders.append(
            (
                order_id,
                "cohort-proposal",
                "BTC/USDT",
                "usdt_futures",
                "long",
                "market",
                1_000.0,
                100.0,
                "paper_internal",
                position_id,
                "filled",
                opened_at,
            )
        )
        positions.append(
            (
                position_id,
                "BTC/USDT",
                "usdt_futures",
                "long",
                1_000.0,
                100.0,
                100.0 + pnl / 1_000.0,
                pnl,
                "paper_internal",
                "cohort-proposal",
                order_id,
                "closed",
                "verified_exit",
                "strategy",
                "local_fill",
                1,
                "local_fill",
                1,
                1,
                "eligible",
                "oi-v1",
                0.0,
                "local_simulation",
                opened_at,
                closed_at,
                0.0,
                0.0,
                "cohort-opportunity",
                "session-001",
            )
        )

    db.executemany(
        """
        INSERT INTO orders (
            id, proposal_id, symbol, market_type, direction, order_type,
            quantity, price, execution_mode, position_id, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        orders,
    )
    db.executemany(
        """
        INSERT INTO positions (
            id, symbol, market_type, direction, quantity, entry_price,
            current_price, realized_pnl, execution_mode, proposal_id, order_id,
            status, close_reason, close_category, entry_price_source,
            entry_fill_verified, exit_price_source, exit_fill_verified,
            outcome_eligible, outcome_exclusion_reason, outcome_semantics_version,
            funding_cost, funding_source, opened_at, closed_at, entry_fee,
            exit_fee, opportunity_id, runtime_session_id
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        positions,
    )
    db.commit()

    from collections import Counter
    from aletheia.analytics.outcome_integrity import (
        assess_reportable_outcome,
        raw_account_events,
    )

    loaded = raw_account_events(
        execution_mode="paper_internal",
        runtime_session_id="session-001",
        closed_after="2026-08-01T00:00:00+00:00",
        closed_before="2026-08-05T00:00:00+00:00",
    )
    reasons = Counter(assess_reportable_outcome(row).reason for row in loaded)
    assert reasons == {"eligible": 10_000}

    result = compile_qualification_verdict(
        registration={
            **_REGISTRATION,
            "registration_at": "2026-08-01T00:00:00+00:00",
            "cutoff_at": "2026-08-05T00:00:00+00:00",
        },
        starting_equity=100_000.0,
    )

    assert result["verdict"] == "minimum_profitable_loop_rejected", result
    assert result["metrics"]["net_realized_pnl"] == -6_000.0
    assert result["metrics"]["profit_factor"] == 0.5
    assert result["funnel"]["source_raw_event_count"] == 10_001
    assert result["funnel"]["loaded_raw_event_count"] == 10_001
    assert result["funnel"]["cohort_complete"] is True


# ---------------------------------------------------------------------------
# #1261 — Invalid registration_at fails closed
# ---------------------------------------------------------------------------

def test_invalid_registration_at_raises():
    """Non-ISO or naive registration_at must raise ValueError (fail closed)."""
    reg = {**_REGISTRATION, "registration_at": "not-a-date"}
    with pytest.raises(ValueError):
        compile_qualification_verdict(registration=reg)


def test_naive_registration_at_raises():
    """Naive timestamp (no timezone) in registration_at must raise ValueError."""
    reg = {**_REGISTRATION, "registration_at": "2026-01-01T00:00:00"}
    with pytest.raises(ValueError):
        compile_qualification_verdict(registration=reg)
