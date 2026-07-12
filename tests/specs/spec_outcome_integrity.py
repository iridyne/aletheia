"""Executable target contract for closed-position outcome integrity.

The current source fixes for account scoping remain protected by PL-ADOPT claims.
The OI-* claims below describe the still-missing cross-cutting boundary between a
raw position state transition and a genuine strategy outcome.  Missing behavior
uses strict xfail tripwires: implementation makes the tripwire XPASS until the
implementing task replaces it with a real GREEN behavioral test and updates the
claim status.

Issue mapping: RG-01 through RG-22 in
docs/issues/RECONCILED_GONE_INTEGRITY_AUDIT_2026-07-12.md.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]

# This is the behavior-changing inventory, not every legitimate raw/audit reader.
# Each listed module must migrate to the central eligible-outcome read model before
# OI-C02 becomes GREEN.  Presentation and account-state readers are governed by
# OI-C04 instead of being silently treated as judgement consumers.
BEHAVIOR_CHANGING_CLOSED_POSITION_CONSUMERS = (
    "aletheia/analytics/trade_stats.py",
    "aletheia/analytics/family_scorecard.py",
    "aletheia/analytics/confidence_calibration.py",
    "aletheia/analytics/alpha_attribution.py",
    "aletheia/agent_shell/operator_loop.py",
    "aletheia/agent_shell/trading_context.py",
)


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _missing(reason: str):
    return pytest.mark.xfail(strict=True, reason=reason)


@spec_claim(
    SpecClaim(
        "OI-B00",
        "outcome_integrity",
        "§1 retained source protections",
        "L2b",
        "The outcome-integrity contract retains the existing wrong-broker, incomplete-view, "
        "paper_internal local-only, and execution-account isolation regression claims.",
        status="implemented",
    )
)
def test_existing_account_scope_regressions_remain_registered():
    source = _source("tests/specs/spec_pipeline_adopted_position.py")
    required = {
        "PL-ADOPT-10", "PL-ADOPT-11", "PL-ADOPT-12",
        "PL-ADOPT-22", "PL-ADOPT-23", "PL-ADOPT-24",
    }
    missing = sorted(claim for claim in required if claim not in source)
    assert not missing, f"existing reconciliation protections disappeared: {missing}"


@spec_claim(
    SpecClaim(
        "OI-S01", "outcome_integrity", "§2 structured closure evidence", "L2a",
        "Position persistence records close category, exit-price provenance, fill verification, "
        "outcome eligibility/exclusion, reconciliation identity/evidence, and semantics version "
        "without rewriting original economic fields.",
        status="implemented",
    )
)
def test_position_schema_has_additive_outcome_evidence_fields():
    combined = _source("aletheia/storage/models.py") + _source("aletheia/storage/migrations.py")
    required = {
        "close_category", "exit_price_source", "exit_fill_verified",
        "outcome_eligible", "outcome_exclusion_reason",
        "reconciliation_identity", "reconciliation_evidence",
        "outcome_semantics_version",
    }
    assert not (required - {name for name in required if name in combined})
    assert "position_disappearance_candidates" in combined
    assert "position_disappearance_observations" in combined


@_missing("RG-03/RG-14: neutral central outcome read model is not implemented")
@spec_claim(
    SpecClaim(
        "OI-S02", "outcome_integrity", "§3 central outcome semantics", "L2a",
        "A dependency-neutral analytics module owns ClosureSemantics/OutcomeAssessment and "
        "explicit raw_account_events, eligible_strategy_outcomes, and administrative_closures "
        "read models; Learning composes it rather than owning the global classifier.",
        status="missing",
    )
)
def test_dependency_neutral_outcome_read_model_exists():
    assert importlib.util.find_spec("aletheia.analytics.outcome_integrity") is not None
    source = _source("aletheia/analytics/outcome_integrity.py")
    for token in (
        "ClosureSemantics", "OutcomeAssessment", "raw_account_events",
        "eligible_strategy_outcomes", "administrative_closures",
    ):
        assert token in source


@_missing("RG-09: one view miss can still flow directly to orphan close")
@spec_claim(
    SpecClaim(
        "OI-B01", "outcome_integrity", "§4 durable disappearance confirmation", "L2b",
        "A single complete-view miss creates only a durable disappearance candidate; closure "
        "requires two independent complete observations or a direct broker absent result, and "
        "stale/incomplete/wrong-account views never advance confirmation.",
        status="missing",
    )
)
def test_reconciler_declares_durable_disappearance_candidates():
    source = _source("aletheia/portfolio/position_reconciler.py")
    required = (
        "DisappearanceCandidate", "record_disappearance_observation",
        "confirmation_count", "direct_broker_verification",
    )
    assert all(token in source for token in required)


@_missing("RG-10: verified exit recovery is still broker-specific")
@spec_claim(
    SpecClaim(
        "OI-B02", "outcome_integrity", "§5 broker-neutral verified exit", "L2b",
        "Bitget, OKX, and Binance implement one verified-position-exit protocol whose result is "
        "verified_fill, confirmed_absent_without_fill, or inconclusive; network and pagination "
        "failures are inconclusive.",
        status="missing",
    )
)
def test_all_broker_clients_implement_verified_position_exit():
    assert importlib.util.find_spec("aletheia.execution.verified_exit") is not None
    for path in (
        "aletheia/exchanges/bitget/client.py",
        "aletheia/exchanges/okx/client.py",
        "aletheia/exchanges/binance/client.py",
    ):
        assert "get_verified_position_exit" in _source(path), path


@_missing("RG-11/RG-16: administrative close still reuses normal economic close")
@spec_claim(
    SpecClaim(
        "OI-B03", "outcome_integrity", "§6 close/accounting split", "L2b",
        "Administrative-unverified closure may converge state and audit but does not fabricate "
        "fees, slippage, realized PnL, paper cash settlement, or outcome-only hooks; verified "
        "strategy close retains exact economic settlement and causal ids.",
        status="missing",
    )
)
def test_portfolio_has_separate_verified_and_administrative_close_paths():
    source = _source("aletheia/portfolio/pnl.py")
    assert "close_verified_position" in source
    assert "close_administrative_position" in source
    assert "outcome_hook_eligible" in source


@_missing("RG-07: reconcile detector only matches adopted reopen rows")
@spec_claim(
    SpecClaim(
        "OI-B04", "outcome_integrity", "§7 root-cause-neutral incident fingerprint", "L2b",
        "An administrative close followed by any native or adopted open with the same scoped "
        "account/instrument/direction identity triggers the reconcile incident detector.",
        status="missing",
    )
)
def test_reconcile_incident_detector_is_not_adopted_only():
    import inspect
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

    source = inspect.getsource(detect_reconcile_thrash)
    assert "source='adopted'" not in source
    assert "execution_mode" in source
    assert "market_type" in source


@_missing("RG-08: reconcile incident is not connected to global Learning refreeze")
@spec_claim(
    SpecClaim(
        "OI-C01", "outcome_integrity", "§8 incident-wide Learning refreeze", "L2c",
        "A persisted reconcile-integrity incident globally refreezes every behavior-changing "
        "Learning path to its static baseline across restart; read-only measurement remains "
        "available and recovery requires an explicit clean observation gate.",
        status="missing",
    )
)
def test_learning_control_consumes_reconcile_integrity_incident():
    source = _source("aletheia/learning/control.py")
    for token in (
        "reconcile_integrity_incident", "global_learning_refreeze",
        "clean_observation_gate",
    ):
        assert token in source


@_missing("RG-03..06/RG-18/RG-19: behavior consumers have not all migrated")
@spec_claim(
    SpecClaim(
        "OI-C02", "outcome_integrity", "§9 behavior-consumer eligibility", "L2c",
        "Every enumerated KPI, Operator, calibration, alpha, and trading-context consumer reads "
        "eligible_strategy_outcomes from the central model; insufficient evidence never falls "
        "back to unlabelled raw closed rows or a stale semantics version.",
        status="missing",
    )
)
def test_behavior_changing_consumers_use_one_eligible_read_model():
    for path in BEHAVIOR_CHANGING_CLOSED_POSITION_CONSUMERS:
        source = _source(path)
        assert "eligible_strategy_outcomes" in source, path
        assert "outcome_semantics_version" in source, path


@_missing("RG-12: Risk daily loss still consumes the ambiguous today_realized_pnl")
@spec_claim(
    SpecClaim(
        "OI-C03", "outcome_integrity", "§10 authoritative account loss", "L2c",
        "Risk daily-loss protection reads an authoritative account-loss source that excludes "
        "unverified exit economics, while strategy-performance PnL is a separate query; missing "
        "account truth is unavailable/fail-closed rather than falsely healthy.",
        status="missing",
    )
)
def test_risk_does_not_use_strategy_or_unverified_realized_pnl():
    source = _source("aletheia/risk/engine.py")
    assert "today_realized_pnl" not in source
    assert "authoritative_account_realized_loss" in source


@_missing("RG-13: analytics API does not expose honest raw/genuine dual semantics")
@spec_claim(
    SpecClaim(
        "OI-C04", "outcome_integrity", "§11 operator-facing dual reporting", "L2c",
        "Trade stats and equity reporting expose raw event, eligible outcome, excluded outcome, "
        "exclusion reason, and semantics-version fields; judgement defaults to genuine and an "
        "unavailable cohort is never rendered as zero or healthy.",
        status="missing",
    )
)
def test_analytics_routes_expose_raw_and_genuine_cohorts():
    source = _source("aletheia/server/routes/analytics.py")
    for token in (
        "raw_event_count", "eligible_outcome_count", "excluded_outcome_count",
        "outcome_exclusion_reasons", "outcome_semantics_version",
    ):
        assert token in source


@_missing("RG-20/RG-21: additive history classification/supersede tool is absent")
@spec_claim(
    SpecClaim(
        "OI-B05", "outcome_integrity", "§12 additive history remediation", "L2b",
        "History remediation is dry-run-first, additive, transactional, idempotent, archives or "
        "supersedes polluted derived artifacts without deletion, and emits paper-cash compensation "
        "candidates without applying them absent owner approval.",
        status="missing",
    )
)
def test_history_remediation_tool_declares_non_destructive_protocol():
    assert importlib.util.find_spec("aletheia.maintenance.outcome_integrity") is not None
    source = _source("aletheia/maintenance/outcome_integrity.py")
    for token in (
        "dry_run", "semantics_version", "superseded_by", "archive",
        "compensation_candidates", "owner_approval_required",
    ):
        assert token in source
    assert "DELETE FROM positions" not in source.upper()


@_missing("RG-22: clean-window identity is still an unversioned timestamp constant")
@spec_claim(
    SpecClaim(
        "OI-C05", "outcome_integrity", "§13 versioned clean window", "L2c",
        "Every performance/Learning cohort records a versioned clean-window anchor and outcome "
        "semantics version; pre-anchor, unknown-version, and incident-window rows are ineligible, "
        "and insufficient post-fix evidence remains insufficient.",
        status="missing",
    )
)
def test_clean_window_has_versioned_anchor_and_incident_exclusions():
    source = _source("aletheia/analytics/learning_window.py")
    for token in (
        "CLEAN_WINDOW_ANCHOR_ID", "OUTCOME_SEMANTICS_VERSION",
        "incident_window", "unknown_semantics_version",
    ):
        assert token in source
