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
        spec_data_model,
        spec_agent_contracts,
        spec_api_reference,
        spec_data_layer,
        spec_operator_loop,
        spec_subsystem_observers,
        spec_audit_chain,
        spec_persona_sizing,
        # Freeze-era RED tripwires (written 2026-06-24 / extended 2026-06-27 during
        # EXPERIMENT FREEZE). Their claims are status="missing" + xfail(strict): they
        # describe the decision-chain learning fixes to apply on Round 2 解冻当天 (see
        # docs/plans/round2_week1_decision_chain_fixes.md). They were NOT in this list
        # before — i.e. the tripwires existed on disk but were only armed through
        # this matrix or explicit file paths. pyproject now includes spec_*.py in
        # python_files, so tests/specs/ is directly collectible too.
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
        # Universe pipeline integrity (UP-*): Scanner/Observer audit fixes 2026-07-01.
        # Same-source forward-outcome direction, scanner_weights consumption,
        # market_type propagation, composite feature keying.
        spec_universe_pipeline,
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
        # Serenity xStocks execution path (SX-* claims). equity_tradeable asset_class
        # enables Serenity-produced stock tokens to be traded by PI (P2).
        spec_serenity_xstock_execution,
        # Round 2 high-dimensional negative-feedback closure (IFL-* claims attach to
        # "insight_feedback_loop"). Guards the three audited defects: clean-window floor
        # (②, implemented), Module J memo_effectiveness + bias-cap enforcement (③, xfail
        # tripwires), regime snapshot (①). Behavioral/structural — not in REQUIRED_CONTRACTS.
        spec_insight_feedback_loop,
        # PI-agent decision-chain fixes (PI-* claims) — A5 cadence decoupling, E1 guard
        # advisory, operator_loop→TS convergence lock, recommend_promote approval gate.
        # From the 2026-06-28 PI-agent SDD audit (docs/issues/PI_AGENT_SDD_AUDIT).
        spec_pi_decision_chain,
        # Shadow-trade resolution health (2026-06-28, content-probe fixes). AN-*
        # claims attach to "analytics_contract" — unresolved counterfactual shadow
        # trades must not pile up unbounded. Behavioral/data (L2b), not in
        # REQUIRED_CONTRACTS.
        spec_analytics_contracts,
        # Quant-review paper attribution replay (2026-07-01, QR-04): proposal-time
        # attribution snapshot threads into paper_broker position fields.
        spec_pipeline_paper_attribution,
        spec_operator_contract_parity,
        spec_operator_single_source,
        # Outcome integrity (OI-*): closes the semantic gap between a raw local
        # position state transition and a genuine strategy outcome.  OI-B00 retains
        # the current broker/account scoping regressions; the remaining strict-xfail
        # claims are the staged RG-01..RG-22 remediation contract. 2026-07-12.
        spec_outcome_integrity,
        # #34: these spec files existed but were never imported here, so their claims
        # (skill store, universe distribution audit) were absent from the conformance
        # matrix — register them so they are actually loaded and counted. Also removed a
        # duplicate spec_pi_decision_chain import that was in this list twice.
        spec_skill_store,
        spec_universe_distribution_audit,
        # Lesson block guards (LG-*): dry-run window + TTL expiry before block takes effect.
        # Attaches to risk_engine_contract (trust boundary, already REQUIRED). 2026-06-29.
        spec_lesson_guards,
        # Decision lifecycle contract (DL-*): the cross-cutting opportunity → PiDecision
        # → TradingDecision → TradeProposal spine. Governs the SEAMS between subsystems
        # (carrier field semantics) that per-subsystem contracts don't cover. 2026-06-30.
        spec_decision_lifecycle,
        # Persistence/Recovery contract (PR-*): event/artifact stores + deterministic
        # client_order_id + recovery FSM class. Code-ahead-of-wiring domain — PR-C01 is a
        # self-correcting tripwire marking the FSM-not-wired-into-startup gap. Not REQUIRED
        # (🟡 partial implementation). 2026-06-30.
        spec_persistence,
        # Kill Conditions contract (D8-*): three system-level Kill judgements —
        # K1 consecutive PF<0.5 / K2 consecutive 3-day zero fills /
        # K3 live_unlock requires scorecard composite_score >= 70.
        # D8-S01/B01/S03 are implemented; D8-S02/S04/B02/B03/C01 are
        # missing_tripwire xfails awaiting implementation. 2026-06-30.
        spec_kill_conditions,
        # Factor feedback quality gates (FF-*): min sample size before preferred,
        # outlier-driven PF detection, hard_blocked status + ranker hard-reject.
        # 2026-06-30 Round 2 Coriva impl plan.
        spec_factor_feedback,
        # Knowledge FTS integrity (KF-*): three root causes of FTS missing records —
        # (A) bypass-manager anti-pattern, (B) disk-file deletion silent empty-body,
        # (C) operator_assessment _index() missing. 2026-07-01 root-cause analysis.
        spec_knowledge_fts,
        # Equity observer + generic stock-token registry (EQ-R* claims): guards the
        # exchange-agnostic _STOCK_TOKEN_REGISTRY + r-series rule, equity_observer's
        # priority token lookup, Serenity equity_chain lens, and schema/doc R5
        # compliance for Bitget r-series tokens. Attaches to data_utilization_contract.
        # 2026-07-01 xStocks adaptation.
        spec_equity_observer,
        # Agent-tier model routing (MR-* claims): guards deep_provider / fast_provider
        # schema fields, get_provider_for_tier() resolver, helpers provider= param, and
        # per-tier agent call sites. Attaches to model_routing_contract.
        # 2026-07-01 two-tier model routing implementation.
        spec_model_routing,
        # Cross-market-type opportunity deduplication (OD-*): one instrument per
        # canonical symbol when both spot and usdt_futures are enabled; LLM is not
        # called twice for the same directional read. Reuses data_layer_contract.
        spec_market_type_dedup,
        # Managed network egress / system proxy (NET-*): every outbound interaction
        # routes through the aletheia.net choke point; structured proxy config
        # (protocol/host/port) + enforce fail-closed policy; ccxt single-constructor
        # invariant; NO_PROXY/ALL_PROXY wiring. New "network_egress_contract"
        # (cross-cutting infra — not in REQUIRED_CONTRACTS). 2026-07-04.
        spec_network_egress,
        spec_agent_workflow,
    )


_import_all_specs()

REQUIRED_CONTRACTS = [
    "risk_engine_contract",
    "execution_contract",
    "live_unlock_contract",
    "data_model",
    "agent_contracts",
    "api_reference",
    "data_layer_contract",
    "operator_layer",
    "audit_chain_contract",
    "decision_lifecycle_contract",
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

    def test_non_implemented_claim_budget(self):
        """#75 fix: `test_print_conformance_matrix` is informational-only
        and passes green even when the matrix contains missing/partial/
        superseded claims. An owner reading "green tests" then believes the
        contracts are all wired up. Anchor the current baseline as a budget
        — new missing/partial claims can only be added by consciously
        updating this test's expectation, matching what happened when
        contracts were paused or new claims added without wiring.

        If this test fails, either:
          - flip the newly-broken claim's status to 'implemented' (the fix), OR
          - if the gap is intentional, update the counts below and note why.
        """
        counts: dict[str, int] = {}
        for entries in SPEC_REGISTRY.values():
            for claim, _ in entries:
                counts[claim.status] = counts.get(claim.status, 0) + 1

        # Baseline anchored 2026-07-08 from the review's own count:
        #   missing=5, partial=1, superseded=1.
        # Bumping any of these upward is a signal that a new gap opened;
        # bumping downward means a fix landed and someone should ratchet
        # this budget down accordingly.
        # TASK-2026-032 intentionally added twelve OI-* RED tripwires for the
        # reconciled-gone remediation.  They are split into later implementation
        # tasks; TASK-2026-033 makes OI-S01 GREEN by adding the persistence layer.
        # Strict xfail keeps the remaining eleven honest without representing them
        # as implemented.
        # TASK-2026-034 makes OI-S02 GREEN with the dependency-neutral central
        # classifier/read model, durable incident/Learning refreeze, and disappearance
        # confirmation, broker-neutral verified exit, and verified/admin accounting
        # split, behavior consumers, and authoritative Risk loss. Three OI
        # reporting/history claims remain missing.
        MAX_MISSING = 7
        # #153: 5 self-heal specs moved to partial (OL-C08, OL-S13, SH-S01, SH-C02, SH-B03).
        # TASK-2026-022 adds six explicitly bounded Learning-evolution claims whose
        # Phase-0 cores are implemented but whose consumer-wide persistence/UI/
        # authority adapters remain later-phase work: LE-B16/B21 and
        # LE-C06/C10/C11/C13. Keeping them partial is deliberate semantic honesty;
        # the target contract names the exact remaining boundary for each claim.
        MAX_PARTIAL = 12
        MAX_SUPERSEDED = 1

        missing = counts.get("missing", 0)
        partial = counts.get("partial", 0)
        superseded = counts.get("superseded", 0)

        assert missing <= MAX_MISSING, (
            f"CONFORMANCE REGRESSION: {missing} missing claims exceed the "
            f"documented budget of {MAX_MISSING}. New missing claim(s) landed — "
            "either implement them or consciously bump the budget with a reason."
        )
        assert partial <= MAX_PARTIAL, (
            f"CONFORMANCE REGRESSION: {partial} partial claims exceed the "
            f"documented budget of {MAX_PARTIAL}."
        )
        assert superseded <= MAX_SUPERSEDED, (
            f"CONFORMANCE REGRESSION: {superseded} superseded claims exceed the "
            f"documented budget of {MAX_SUPERSEDED}."
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
                # #74 fix: "superseded" claims (older contract clauses replaced
                # by a newer one) get their own icon so the matrix doesn't render
                # them as "?" (unknown). Not a failure — a legitimate lifecycle
                # state next to implemented/partial/missing.
                "superseded": "s",
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
