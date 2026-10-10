# Current Behavioral Contracts

Files in this directory describe behavior that is currently approved and
observable. They are not roadmaps. A Contract is the *current-behavior* half of
the SDD format: target designs are written as [specs](../specs/README.md), and an
implemented spec lands here through its `Contract Delta`.

Every Contract identifies its production entry point, durable state and
consumer, numbered requirements with checkable scenarios, failure semantics
(unavailable is never zero), authority, and executable evidence. New Contracts
start from [template.md](template.md); existing ones move to that form when they
are next materially revised.

Future targets, unwired components, and desired guarantees belong in
[`../specs/`](../specs/README.md). Known mismatches belong in GitHub Issues. A
Contract may link to either, but must not express them as current behavior.
Retired mechanisms belong in [`../history/`](../history).

Contract changes and their executable claims must remain aligned, but claim
conformance does not determine Product Support.

## Keeping a Contract and its claims aligned

"Aligned" is a two-way obligation and the direction that gets skipped is
claim → Contract. Whenever a `SpecClaim`'s `contract`, `section`, or `invariant`
text changes:

1. **If the named section does not exist, you invented it** — add the section or
   repoint the claim. Since 2026-09-26 this step is mechanical:
   `check_docs.spec_claim_anchor_failures` fails the claim, so it no longer
   depends on anyone remembering to look. (It once did: 2026-09-07, `LU-C07` was
   repointed at a `§4b selected-mode resolution` that did not exist; the fix was
   adding `§4b-2` to the Live Unlock Contract.)
2. The claim's `invariant` may promise only what the Contract states and only
   what the test actually exercises. A claim promising more than the Contract is
   the defect, even when the test is green. (`LU-C07` promised a selector acting
   "before Proposal persistence" while the Contract records that join as missing
   and tracked in #418.)
3. If the behaviour was retired, invert the claim to assert the retirement holds
   rather than deleting it, and mark the mechanism retired in the owning
   document. A red spec that caught a documented retirement is worth converting
   into a guard against reinstatement. (`AC-S13`, `DM-S06`, both 2026-09-07.)
4. A Contract must not present a retired mechanism as current. `DM-S06` inverted
   cleanly only after `docs/design/data/data_model.md` marked the five Runtime
   Continuity tables retired.
5. Claim ids follow the owning spec file's existing contiguous numbering. Do not
   invent a far-future number to avoid collisions — `RE-S99` was wrong where the
   file ran `RE-S01..S08`; it is `RE-S09`.

## Writing an executable claim that will not rot

- **Assert behaviour, not source text.** Call the function; check the return
  value, the exception, or the persisted row.
- If a source-text scan is genuinely the only option, its docstring must state
  **what it guarantees and what it does not**. A prefix/import denylist is
  direct-import hygiene, never a reachability guarantee — measure the transitive
  closure before claiming otherwise.
- Use AST, not substring matching, for anything about imports:
  `from a.b import c` and `from a.b.c import d` name different modules.
- "String must be absent" stands alone. "String must be present" must be paired
  with an assertion about the behaviour behind it, or it will pass vacuously
  after the text survives a semantic change (`DM-S06`: the DDL text was still in
  the file while the runtime constant excluded it).
- An exact-inventory assertion (`allowed == discovered`) trips on debt
  **shrinking** too. Print both `unexpected` and `stale` in the message so the
  next reader can tell which direction fired; only `unexpected` is a real signal.
- Prove a new guard is non-vacuous before shipping it: revert the fix it guards,
  see it fail, restore. Record that in the commit.

Full case notes for each rule above:
[`DEBUG_AUDIT_PLAYBOOK.md`](../../governance/protocols/DEBUG_AUDIT_PLAYBOOK.md)
SOP-14 through SOP-19.

## Index

Grouped by the trading mainline. "Frozen" means the capability is outside the
current product scope or has no production consumer; its Contract stays only while
code still exists and is not a support claim
([Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md)).

| Stage | Contract | Owns |
|---|---|---|
| Spine | [Decision Lifecycle](decision_lifecycle_contract.md) | Seams Candidate → Planning → Entry Plan → Proposal → Risk → Gateway |
| Universe / Candidate | [Candidate Selection Utility](candidate_selection_utility_contract.md) | Sole Candidate selection authority and its persisted evidence |
| | [Universe Baseline Integrity](universe-baseline-integrity.md) | Research benchmark baseline (#2556), not selection authority |
| | [Filter Evidence](filter_evidence_contract.md) | Per-gate pass/reject facts and shared candidate outcomes |
| | [Opportunity Outcome](opportunity_outcome_contract.md) | Fixed-horizon counterfactual outcomes of candidates |
| Market facts | [Market Structure](market_structure_contract.md) | Causal swing/BOS/CHoCH facts used by the Entry Planner |
| | [Technical Analysis and OHLCV Tiering](technical_analysis_contract.md) | Indicator computation and OHLCV storage lifecycle |
| | [OHLCV Provider Result](ohlcv_provider_result_contract.md) | Exact-timeframe, real-field provider results |
| | [Market Data Coverage](market_data_coverage_contract.md) | Collector execution facts vs dataset freshness |
| | [Derivatives Economic Semantics](derivatives_economic_semantics.md) | Funding / OI sign and unit semantics |
| | [Macro Source-Time](macro_source_time.md) | TradFi/macro source-time freshness |
| Planning / Entry | [Entry Plan](entry-plan-contract.md) | Planner hypothesis, deterministic Compiler, immutable Plan |
| | [Entry Sizing and Financing](entry_sizing_and_financing_contract.md) | Risk intent → size, leverage and margin |
| | [Trade Decision Rationale](trade_decision_rationale_contract.md) | Where decision reasons are stored |
| | [Serenity](serenity_contract.md) | Experimental research source publishing Candidates (default off) |
| Risk | [Risk Engine](risk_engine_contract.md) | The sole approval authority before any submission |
| | [Live Unlock](live_unlock_contract.md) | Human-only Live unlock, scorecard gate, agent prohibitions |
| | [Kill Conditions](kill_conditions_contract.md) | Demo kill thresholds |
| Execution / Position / Close | [Execution](execution_contract.md) | Gateway, UTA submission, reconciliation, audit |
| | [Exit and Close](exit_and_close_contract.md) | Exit triggers, the single close router, close claims, verified exit |
| | [Outcome Integrity](outcome_integrity_contract.md) | When a close is a cost-complete Outcome; funding evidence; incident breaker |
| | [Persistence and Recovery](persistence_recovery_contract.md) | Durable state recovery, backup/restore, bundle import |
| | [Audit Chain](audit_chain_contract.md) | Append-only audit and hash chain |
| Review / Learning | [Learning](learning_contract.md), [Knowledge](knowledge_contract.md), [Counterfactual](counterfactual_contract.md) | Being replaced by [spec 0001-learning-loop](../specs/0001-learning-loop/spec.md) |
| | [Observation Lab](observation_lab_contract.md), [Lab V1 design](observation_lab_v1_design.md), [Lab backup](observation_lab_backup_contract.md), [Experiment ownership V2](observation_experiment_ownership_v2.md) | Treatment assignment and receipts (reused by spec 0001 LL-R16) |
| | [Profit-loop Observability](profit_loop_observability_contract.md) | Read-only replay and denominator separation |
| | [Profit-gradient Evidence](profit_gradient_evidence_contract.md) | Read-only gradient compiler (frozen research) |
| Runtime | [Scheduler](scheduler_contract.md) | Job registry, executors, pause semantics |
| | [Agent Contracts](agent_contracts.md) | Agent tasks and the operator-shell boundary |
| | [LLM Inference Harness](llm_inference_harness_contract.md) | Logical model-call identity and prompt/context policy |
| | [Configuration Loading](configuration_contract.md) | Strict config boundary and Doctor blocker projection |
| Owner surfaces | [Status Surface](status_surface_contract.md) | System status headline inputs and consumers |
| | [Settings Control](settings_control_contract.md) | Immediate vs staged Settings semantics |
| | [Dashboard Data Presentation](dashboard_data_presentation_contract.md) | Empty / stale / unavailable presentation |
| Frozen | [Trading Mandate](trading_mandate_contract.md) | Observe-only mandate fit; no production caller |

The per-document review of 2026-10-10 that grades each Contract against this form is
[frozen evidence](../../research/audits/doc-golden-standard-2026-10-10/README.md).
