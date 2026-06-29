"""Spec conformance matrix — coverage report and structural validation.

Run with: uv run pytest tests/test_spec_conformance.py -v -s
Prints the full conformance matrix showing claim_id -> test -> status.
"""

import pytest

from tests.specs.spec_meta import SPEC_REGISTRY


def _import_all_specs():
    """Import all spec modules to populate SPEC_REGISTRY."""
    from tests.specs import (  # noqa: F401
        spec_risk_engine,
        spec_execution,
        spec_live_unlock,
        spec_promotion_gates,
        spec_data_model,
        spec_agent_contracts,
        spec_api_reference,
        spec_data_layer,
        spec_operator_loop,
        spec_subsystem_observers,
        spec_audit_chain,
        # Freeze-era RED tripwires (written 2026-06-24 / extended 2026-06-27 during
        # EXPERIMENT FREEZE). Their claims are status="missing" + xfail(strict): they
        # describe the decision-chain learning fixes to apply on Round 2 解冻当天 (see
        # docs/plans/round2_week1_decision_chain_fixes.md). They were NOT in this list
        # before — i.e. the tripwires existed on disk but were never collected/armed
        # (the SDD collection trap: spec_*.py do not match pytest python_files).
        spec_learning_pipeline,
        spec_strategy_pool,
        spec_futures_leverage,
        # End-to-end pipeline specs (PL-* claims). Registered so the conformance
        # matrix reflects pipeline coverage; not added to REQUIRED_CONTRACTS since
        # they are behavioral/E2E (L2b) rather than structural (L2a) contracts.
        spec_pipeline_risk_gate,
        spec_pipeline_execution_chain,
        spec_pipeline_live_unlock,
        spec_pipeline_market_data,
        spec_spot_parity,
        spec_pipeline_paper_trade,
        spec_pipeline_position_manager,
        spec_pipeline_operator_loop,
        spec_pipeline_profit_loop,
        spec_pipeline_stop_propagation,
        spec_pipeline_entry_enrichment,
        # Round 2 macro signals (zero-cost data integration; LSR/F&G/CoinGecko Top-N/
        # history backfill). All MS-* claims attach to
        # data_layer_contract (reused — not a new trust boundary).
        spec_macro_signals,
        # Free zero-key data sources (datasrc overhaul): Hyperliquid cross-venue perp
        # OI/funding (HL-*) + DefiLlama on-chain stablecoin/TVL/DEX (DL-*). Attach to
        # data_layer_contract (reused). Behavioral E2E claims are online-gated.
        spec_hyperliquid,
        spec_defillama,
        # Data Utilization Contract (DU-*): every acquired data domain routes to a
        # consuming module (物尽其用). Locks the Hyperliquid + on-chain consumers
        # against regression to collected-but-unused. data_utilization_contract.
        spec_data_utilization,
        # Gateway stability (GS-*): bound LLM-driven jobs (market_observer wall-clock
        # budget) + bounded structured-output retry, so provider contention can't leak
        # threads / starve the frontdoor. gateway_stability_contract.
        spec_gateway_stability,
        # Adopted-position SL/TP pipeline contract (regression lock for NULL stop bug)
        spec_pipeline_adopted_position,
        # Serenity evidence quality (D2 — generator/evaluator separation). SE-* claims
        # attach to the logical contract "serenity_evidence_quality" (not in
        # REQUIRED_CONTRACTS — behavioral, mirroring the pipeline specs).
        spec_serenity_evidence,
        # Serenity decision attribution (Phase 2 — signal_source discriminator). SA-*
        # claims attach to the logical contract "serenity_attribution" (not in
        # REQUIRED_CONTRACTS — decision-level attribution, mirroring the pipeline specs).
        spec_serenity_attribution,
        # Serenity promote → PI evaluation queue (Phase 3, Option A, D5 switch). SP-*
        # claims attach to "serenity_promote" — replaces the StrategyRecord(state='draft')
        # dead-record path with signal_pending PiDecisions. Behavioral (L2a + L2b TestClient),
        # not in REQUIRED_CONTRACTS.
        spec_serenity_promote,
        # Serenity long-horizon holding (Phase 4 — execution-school discriminator). SH-*
        # claims attach to "serenity_holding" — adds trade_school field on TradeProposal +
        # Position and branches position_manager exits/trailing-stop by school. Behavioral
        # (L2a structural + L2b pure-function), not in REQUIRED_CONTRACTS.
        spec_serenity_holding,
        # Serenity live-readiness (Phase 5 — D4 structural live-prep only). LR-* claims
        # attach to "serenity_live_readiness" — codify that trade_school threads through
        # all three live broker dispatch paths AND that the live unlock route + risk
        # engine live guard remain school-agnostic (no autonomous live unlock). Pure L2a
        # structural — no behavior change, paper still the only enabled execution mode.
        spec_serenity_live_readiness,
        # Serenity forced verdict (S4, AI Berkshire integration 2026-06-28). SV-* claims
        # attach to "serenity_verdict" — every opportunity must carry a machine-readable
        # verdict with explicit bear_case. TradingDecision.bear_case structural check included.
        spec_serenity_verdict,
        # Serenity researchability grade (S2, AI Berkshire integration 2026-06-28). SR-*
        # claims attach to "serenity_researchability" — deterministic A/B/C grade + C-grade
        # scoring ceiling (≤40) to prevent speculative opportunities from reaching
        # research_priority bucket.
        spec_serenity_researchability,
        # Serenity multi-lens parallel research (S1, AI Berkshire integration 2026-06-28). ML-*
        # claims attach to "serenity_multi_lens" — 4 research perspectives (technical/flow/
        # sentiment/macro) replace single-lens Stage 1 to reduce directional bias. Sequential
        # execution to stay within rate limits; lens registry pattern for easy extension.
        spec_serenity_multi_lens,
        # Round 2 high-dimensional negative-feedback closure (IFL-* claims attach to
        # "insight_feedback_loop"). Guards the three audited defects: clean-window floor
        # (②, implemented), Module J memo_effectiveness + bias-cap enforcement (③, xfail
        # tripwires), regime snapshot (①). Behavioral/structural — not in REQUIRED_CONTRACTS.
        spec_insight_feedback_loop,
        # PI-agent decision-chain fixes (PI-* claims) — A5 cadence decoupling, E1 guard
        # advisory, operator_loop→TS convergence lock, recommend_promote approval gate.
        # From the 2026-06-28 PI-agent SDD audit (docs/issues/PI_AGENT_SDD_AUDIT).
        spec_pi_decision_chain,
        # Strategy evaluator regression locks (2026-06-28, content-probe fixes). EV-*
        # claims attach to "evaluator_contract" — momentum_pct must honour the
        # `period` key (EV-S01) and the scanner must inject multi_tf_bars for
        # cross-timeframe conditions (EV-S02). Pure L2a structural, not a trust
        # boundary, so not in REQUIRED_CONTRACTS.
        spec_evaluator_contracts,
        # Shadow-trade resolution health (2026-06-28, content-probe fixes). AN-*
        # claims attach to "analytics_contract" — unresolved counterfactual shadow
        # trades must not pile up unbounded. Behavioral/data (L2b), not in
        # REQUIRED_CONTRACTS.
        spec_analytics_contracts,
        spec_pi_decision_chain,
        spec_operator_contract_parity,
        spec_operator_single_source,
    )


_import_all_specs()

REQUIRED_CONTRACTS = [
    "risk_engine_contract",
    "execution_contract",
    "live_unlock_contract",
    "promotion_gates_contract",
    "data_model",
    "agent_contracts",
    "api_reference",
    "data_layer_contract",
    "operator_layer",
    "audit_chain_contract",
    # subsystem observer claims register under "agent_contracts" by design —
    # equity_observer / prediction_observer are agents, not a separate contract.
]


class TestConformanceStructure:
    """Validate the spec registry itself is well-formed."""

    def test_every_required_contract_has_claims(self):
        for contract in REQUIRED_CONTRACTS:
            claims = SPEC_REGISTRY.get(contract, [])
            assert claims, f"CONFORMANCE GAP: no claims for '{contract}'"

    def test_every_claim_has_callable_test(self):
        for contract, entries in SPEC_REGISTRY.items():
            for claim, test_fn in entries:
                assert callable(test_fn), (
                    f"ORPHAN: {claim.claim_id} in {contract} has no callable test"
                )

    def test_claim_ids_unique_within_contract(self):
        for contract, entries in SPEC_REGISTRY.items():
            ids = [c.claim_id for c, _ in entries]
            seen = set()
            for cid in ids:
                assert cid not in seen, (
                    f"DUPLICATE claim_id '{cid}' in {contract}"
                )
                seen.add(cid)

    def test_all_levels_represented(self):
        """Each required contract should have at least one L2a claim."""
        for contract in REQUIRED_CONTRACTS:
            entries = SPEC_REGISTRY.get(contract, [])
            levels = {c.level for c, _ in entries}
            assert "L2a" in levels, (
                f"{contract} has no L2a (structural) claims"
            )


def test_print_conformance_matrix(capsys):
    """Print the full matrix (always passes — informational output)."""
    lines = ["\n{'='*60}", "  SPEC CONFORMANCE MATRIX", f"{'='*60}\n"]
    total = implemented = 0

    for contract in sorted(SPEC_REGISTRY.keys()):
        entries = SPEC_REGISTRY[contract]
        lines.append(f"\n  {contract} ({len(entries)} claims)")
        for claim, test_fn in sorted(entries, key=lambda x: x[0].claim_id):
            total += 1
            icon = {
                "implemented": "+",
                "partial": "~",
                "missing": "-",
            }.get(claim.status, "?")
            if claim.status == "implemented":
                implemented += 1
            invariant_short = claim.invariant[:55]
            lines.append(
                f"    [{icon}] {claim.level} {claim.claim_id}: "
                f"{invariant_short}..."
                f"\n        -> {test_fn.__name__}"
            )

    coverage = (implemented / total * 100) if total else 0
    lines.append(f"\n  Total: {total} | Implemented: {implemented} | "
                 f"Coverage: {coverage:.0f}%")
    lines.append(f"{'='*60}\n")

    with capsys.disabled():
        print("\n".join(lines))
