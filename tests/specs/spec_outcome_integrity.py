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
    "aletheia/analytics/trade_attribution.py",
    "aletheia/analytics/insight_synthesizer.py",
    "aletheia/analytics/reasoning_feedback.py",
    "aletheia/analytics/memo_effectiveness.py",
    "aletheia/analytics/go_live_scorecard.py",
    "aletheia/trading/sizing_policy.py",
    "aletheia/trading/strategy_lifecycle.py",
    "aletheia/trust/guard_chain.py",
    "aletheia/agent_shell/trading_decision.py",
    "aletheia/analytics/sizing_trace.py",
    "aletheia/agent_shell/operator_recommendations.py",
)

ELIGIBLE_OUTCOME_ONE_HOP_DELEGATIONS = {
    "aletheia/agent_shell/operator_recommendations.py": (
        "build_post_trade_review_coverage",
        "aletheia/knowledge/review_coverage.py",
    ),
}

ATTRIBUTION_DERIVED_OUTCOME_CONSUMERS = (
    "aletheia/analytics/factor_feedback.py",
    "aletheia/analytics/guard_promotion.py",
    "aletheia/analytics/guard_effectiveness.py",
    "aletheia/analytics/trade_case_builder.py",
    "aletheia/analytics/trade_path.py",
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


@spec_claim(
    SpecClaim(
        "OI-S02", "outcome_integrity", "§3 central outcome semantics", "L2a",
        "A dependency-neutral analytics module owns ClosureSemantics/OutcomeAssessment and "
        "explicit raw_account_events, eligible_strategy_outcomes, and administrative_closures "
        "read models; Learning composes it rather than owning the global classifier.",
        status="implemented",
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
    for forbidden in (
        "aletheia.learning", "aletheia.risk", "aletheia.execution",
        "aletheia.exchanges", "aletheia.agent_shell",
    ):
        assert forbidden not in source
    learning_source = _source("aletheia/learning/outcomes.py")
    assert "assess_position_closure" in learning_source
    assert "SYNTHETIC_CLOSE_REASONS" not in learning_source


@spec_claim(
    SpecClaim(
        "OI-B01", "outcome_integrity", "§4 durable disappearance confirmation", "L2b",
        "A single complete-view miss creates only a durable disappearance candidate; closure "
        "requires two independent complete observations or a direct broker absent result, and "
        "stale/incomplete/wrong-account views never advance confirmation.",
        status="implemented",
    )
)
def test_reconciler_declares_durable_disappearance_candidates():
    confirmation = _source("aletheia/portfolio/disappearance_confirmation.py")
    reconciler = _source("aletheia/portfolio/position_reconciler.py")
    for token in (
        "PositionDisappearanceCandidate", "record_disappearance_observation",
        "MIN_INDEPENDENT_COMPLETE_OBSERVATIONS", "DIRECT_CONFIRMED_ABSENT",
        "view_complete", "view_identity", "confirmation_count",
    ):
        assert token in confirmation
    assert "process_disappearance_evidence" in reconciler
    run_body = reconciler.split("def run_reconcile", 1)[1]
    assert "reconcile_close_orphan(item)" not in run_body
    production_calls = []
    for path in (ROOT / "aletheia").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        call_count = source.count("reconcile_close_orphan(")
        if path.name == "position_reconciler.py":
            call_count -= source.count("def reconcile_close_orphan(")
        if call_count:
            production_calls.append(str(path.relative_to(ROOT)))
    assert production_calls == []
    routes = _source("aletheia/server/routes/data.py")
    assert "def run_positions_reconcile():" in routes
    assert "async def run_positions_reconcile():" not in routes


@spec_claim(
    SpecClaim(
        "OI-B02", "outcome_integrity", "§5 broker-neutral verified exit", "L2b",
        "Bitget, OKX, and Binance implement one verified-position-exit protocol whose result is "
        "verified_fill, confirmed_absent_without_fill, or inconclusive; network and pagination "
        "failures are inconclusive.",
        status="implemented",
    )
)
def test_all_broker_clients_implement_verified_position_exit():
    assert importlib.util.find_spec("aletheia.execution.verified_exit") is not None
    from aletheia.execution.verified_exit import (
        VerifiedExitRequest,
        classify_verified_exit,
    )

    request = VerifiedExitRequest(
        broker_id="bitget_demo", account_environment="demo",
        account_scope="logical-account", execution_mode="bitget_demo",
        position_id="p1", symbol="BTC/USDT", market_type="usdt_futures",
        direction="long", quantity=1.0, history_start_ms=1,
        history_end_ms=2,
    )
    assert classify_verified_exit(
        request, position_absent=True, history_complete=True, close_fills=(),
        ambiguous_activity=False, pages=1, source_endpoint="private-fills",
    ).state == "confirmed_absent_without_fill"
    assert classify_verified_exit(
        request, position_absent=True, history_complete=False, close_fills=(),
        ambiguous_activity=False, pages=1, source_endpoint="private-fills",
    ).state == "inconclusive"
    for path in (
        "aletheia/exchanges/bitget/client.py",
        "aletheia/exchanges/okx/client.py",
        "aletheia/exchanges/binance/client.py",
    ):
        assert "get_verified_position_exit" in _source(path), path


@spec_claim(
    SpecClaim(
        "OI-B03", "outcome_integrity", "§6 close/accounting split", "L2b",
        "Administrative-unverified closure may converge state and audit but does not fabricate "
        "fees, slippage, realized PnL, paper cash settlement, or outcome-only hooks; verified "
        "strategy close retains exact economic settlement and causal ids.",
        status="implemented",
    )
)
def test_portfolio_has_separate_verified_and_administrative_close_paths():
    from aletheia.portfolio.outcome_closure import close_position_administrative
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        id="oi-b03-admin", symbol="BTC/USDT", market_type="usdt_futures",
        direction="long", quantity=1.0, entry_price=100.0, current_price=101.0,
        execution_mode="paper_internal", realized_pnl=5.0,
    )
    repo.insert_position(position)
    result = close_position_administrative(
        position.id, reason="reconciled_gone",
        reconciliation_identity={
            "broker_id": "paper_internal", "account_environment": "local",
            "account_scope": "oi-b03", "execution_mode": "paper_internal",
            "position_id": position.id, "symbol": position.symbol,
            "market_type": position.market_type, "direction": position.direction,
        },
        reconciliation_evidence={"candidate": "oi-b03"},
    )
    stored = repo._select_one("positions", position.id)
    assert result["closure_type"] == "administrative"
    assert stored["realized_pnl"] is None
    assert stored["exit_fee"] is None
    assert stored["slippage_cost"] is None
    assert stored["outcome_eligible"] == 0
    assert not repo.has_paper_account_position_event("position_closed", position.id)
    reconciler = _source("aletheia/portfolio/position_reconciler.py")
    assert "settle_ready_disappearance_candidates" in reconciler
    assert "def reconcile_close_orphan" not in reconciler


@spec_claim(
    SpecClaim(
        "OI-B04", "outcome_integrity", "§7 root-cause-neutral incident fingerprint", "L2b",
        "An administrative close followed by any native or adopted open with the same scoped "
        "account/instrument/direction identity triggers the reconcile incident detector.",
        status="implemented",
    )
)
def test_reconcile_incident_detector_is_not_adopted_only():
    import inspect
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

    source = inspect.getsource(detect_reconcile_thrash)
    assert "source='adopted'" not in source
    assert "execution_mode" in source
    assert "market_type" in source


@spec_claim(
    SpecClaim(
        "OI-C01", "outcome_integrity", "§8 incident-wide Learning refreeze", "L2c",
        "A persisted reconcile-integrity incident globally refreezes every behavior-changing "
        "Learning path to its static baseline across restart; read-only measurement remains "
        "available and recovery requires an explicit clean observation gate.",
        status="implemented",
    )
)
def test_learning_control_consumes_reconcile_integrity_incident():
    control = _source("aletheia/learning/control.py")
    integrity = _source("aletheia/portfolio/reconciliation_integrity.py")
    experiments = _source("aletheia/learning/experiments.py")
    assert "global_learning_refreeze_reason" in control
    assert "get_active_reconciliation_integrity_incident" in control
    assert "clean_observation_gate" in integrity
    assert "MIN_CLEAN_OBSERVATIONS" in integrity
    assert "learning_influence_gate_reason" in experiments
    gated_consumers = {
        "aletheia/agent_shell/trading_context.py": (
            "authorize_operator_learning_context",
        ),
        "aletheia/agents/market_observer.py": (
            "authorize_observer_learning_bundle",
        ),
        # Ranker score changes and learned Guard blocks are separate authority
        # paths. Do not accept the former global on/off switch as proof for either.
        "aletheia/agents/paper_trader_ranker.py": (
            "_authorized_ranker_delta",
            "apply_numeric_treatment",
            "evaluate_guards",
        ),
        "aletheia/knowledge/guards.py": (
            "learned_guard_block_authorized",
            "apply_numeric_treatment",
        ),
        "aletheia/trading/sizing_policy.py": ("learning_influence_enabled",),
        "aletheia/trading/persona.py": ("learning_influence_enabled",),
        "aletheia/trading/persona_rollout.py": ("global_learning_refreeze_reason",),
    }
    for path, gates in gated_consumers.items():
        source = _source(path)
        for gate in gates:
            assert gate in source, f"{path}: missing {gate}"


@spec_claim(
    SpecClaim(
        "OI-C02", "outcome_integrity", "§9 behavior-consumer eligibility", "L2c",
        "Every enumerated KPI, Operator, calibration, alpha, and trading-context consumer reads "
        "eligible_strategy_outcomes from the central model; insufficient evidence never falls "
        "back to unlabelled raw closed rows or a stale semantics version.",
        status="implemented",
    )
)
def test_behavior_changing_consumers_use_one_eligible_read_model():
    for path in BEHAVIOR_CHANGING_CLOSED_POSITION_CONSUMERS:
        source = _source(path)
        delegation = ELIGIBLE_OUTCOME_ONE_HOP_DELEGATIONS.get(path)
        if delegation is None:
            assert "eligible_strategy_outcomes(" in source, path
        else:
            helper_name, helper_path = delegation
            assert f"{helper_name}(" in source, path
            assert "eligible_strategy_outcomes(" in _source(helper_path), helper_path
        assert "SYNTHETIC_CLOSE_REASONS" not in source, path
        assert "is_synthetic_close" not in source, path
    for artifact_consumer in (
        "aletheia/analytics/confidence_calibration.py",
        "aletheia/analytics/insight_synthesizer.py",
        "aletheia/analytics/reasoning_feedback.py",
        "aletheia/analytics/memo_effectiveness.py",
    ):
        assert "is_current_outcome_artifact" in _source(artifact_consumer)
    for path in ATTRIBUTION_DERIVED_OUTCOME_CONSUMERS:
        source = _source(path)
        assert "build_trade_attribution_rows" in source, path
        assert "status = 'closed'" not in source, path
        assert 'status="closed"' not in source, path
    reflection = _source("aletheia/agent_shell/reflection.py")
    assert "assess_realized_position" in reflection


@spec_claim(
    SpecClaim(
        "OI-C03", "outcome_integrity", "§10 authoritative account loss", "L2c",
        "Risk daily-loss protection reads an authoritative account-loss source that excludes "
        "unverified exit economics, while strategy-performance PnL is a separate query; missing "
        "account truth is unavailable/fail-closed rather than falsely healthy. Paper close "
        "events belong to the UTC day of the linked Position close, not the later ledger "
        "append time, and delayed events remain fail-closed unless their amount matches the "
        "linked Position economics.",
        status="implemented",
    )
)
def test_risk_does_not_use_strategy_or_unverified_realized_pnl():
    source = _source("aletheia/risk/engine.py")
    assert "today_realized_pnl" not in source
    assert "authoritative_account_realized_loss" in source
    assert "account_loss_unavailable" in source
    account_source = _source("aletheia/portfolio/account_loss.py")
    assert "eligible_strategy_outcomes" not in account_source
    assert "paper_account_events" in account_source
    assert "assess_position_closure" in account_source
    assert "JOIN positions p" in account_source
    assert "datetime(p.closed_at)" in account_source
    assert "_new_invalid_event_query" in account_source


@spec_claim(
    SpecClaim(
        "OI-C04", "outcome_integrity", "§11 operator-facing dual reporting", "L2c",
        "Trade stats and equity reporting expose raw event, eligible outcome, excluded outcome, "
        "exclusion reason, and semantics-version fields; judgement defaults to genuine and an "
        "unavailable cohort is never rendered as zero or healthy.",
        status="implemented",
    )
)
def test_analytics_routes_expose_raw_and_genuine_cohorts():
    source = _source("aletheia/analytics/outcome_integrity.py")
    for token in (
        "raw_event_count", "eligible_outcome_count", "excluded_outcome_count",
        "outcome_exclusion_reasons", "outcome_semantics_version",
    ):
        assert token in source
    routes = _source("aletheia/server/routes/analytics.py")
    assert "raw_recorded_points" in routes
    assert "genuine_points" in routes
    assert '"points": genuine_points' in routes
    assert '"total_realized": genuine_total' in routes
    frontend = _source("frontend/src/charts/PnLChart.tsx")
    assert "Raw recorded account events" in frontend
    assert "Verified strategy curve" in frontend


@spec_claim(
    SpecClaim(
        "OI-B05", "outcome_integrity", "§12 additive history remediation", "L2b",
        "History remediation is dry-run-first, additive, transactional, idempotent, archives or "
        "supersedes polluted derived artifacts without deletion, and emits paper-cash compensation "
        "candidates without applying them absent owner approval.",
        status="implemented",
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


@spec_claim(
    SpecClaim(
        "OI-C05", "outcome_integrity", "§13 versioned clean window", "L2c",
        "Every performance/Learning cohort records a versioned clean-window anchor and outcome "
        "semantics version; pre-anchor, unknown-version, and incident-window rows are ineligible, "
        "and insufficient post-fix evidence remains insufficient.",
        status="implemented",
    )
)
def test_clean_window_has_versioned_anchor_and_incident_exclusions():
    source = _source("aletheia/analytics/learning_window.py")
    for token in (
        "CLEAN_WINDOW_ANCHOR_ID", "OUTCOME_SEMANTICS_VERSION",
        "incident_window", "unknown_semantics_version",
    ):
        assert token in source


@spec_claim(
    SpecClaim(
        "OI-C06", "outcome_integrity", "§14 cross-layer lifecycle", "L2c",
        "Deterministic E2E coverage proves verified and administrative closes preserve one "
        "meaning across cash, Risk, Learning and reporting; durable disappearance, restart, "
        "account isolation, live alert-only, reopen refreeze, three-broker evidence and history "
        "idempotency remain covered without real orders or live authority.",
        status="implemented",
    )
)
def test_cross_layer_outcome_lifecycle_has_final_e2e_inventory():
    lifecycle = _source("tests/e2e/test_outcome_integrity_full_lifecycle.py")
    for token in (
        "close_position_verified", "close_position_administrative",
        "authoritative_account_realized_loss", "evaluate_proposal",
        "assess_realized_position", "build_outcome_cohort_summary",
        "get_equity_curve", "global_learning_refreeze_reason",
    ):
        assert token in lifecycle

    coverage = {
        "tests/test_disappearance_confirmation.py": (
            "test_one_complete_absence_creates_candidate_but_never_closes",
            "test_stale_or_wrong_account_view_never_advances_candidate",
            "test_candidate_and_observations_survive_database_reconnection",
            "test_live_disappearance_records_evidence_but_remains_alert_only",
        ),
        "tests/specs/spec_pipeline_adopted_position.py": (
            "test_run_reconcile_non_bitget_no_orphan_close",
            "test_paper_internal_is_outside_exchange_reconciliation_scope",
        ),
        "tests/test_verified_exit.py": (
            "test_bitget_verified_exit_uses_direct_position_and_close_fill",
            "test_okx_verified_exit_normalizes_contract_quantity",
            "test_binance_verified_exit_and_network_error_fail_closed",
        ),
        "tests/test_outcome_history_remediation.py": (
            "test_history_remediation_dry_run_apply_and_repeat_are_non_destructive",
            "test_apply_rolls_back_classification_when_archive_step_fails",
        ),
    }
    for path, tests in coverage.items():
        source = _source(path)
        for test_name in tests:
            assert f"def {test_name}" in source, f"{path} missing {test_name}"
    audit = _source("docs/status/audits/OUTCOME_INTEGRITY_FINAL_AUDIT_2026-07-12.md")
    assert "not run" in audit.lower()
    assert "not approved and not applied" in audit.lower()
    assert "live remains disabled" in audit.lower()
