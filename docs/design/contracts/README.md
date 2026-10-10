# Current Behavioral Contracts

Files in this directory describe behavior that is currently approved and
observable. They are not roadmaps.

Every Contract should identify:

- the production entry point;
- durable state and final consumer;
- observable end result;
- invariants and trust boundaries;
- failure and recovery semantics;
- executable evidence for current behavior.

Future targets, unwired components, and desired guarantees belong in
[`../proposals/`](../proposals). Known mismatches belong in GitHub Issues. A
Contract may link to either, but must not express them as current behavior.

Legacy or retired mechanisms belong in [`../history/`](../history).

Use [template.md](template.md) for new Contracts. Contract changes and their
executable claims must remain aligned, but claim conformance does not determine
Product Support.

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

The [Observation Experiment Ownership V2 Migration Contract](observation_experiment_ownership_v2.md) freezes the current Phase 1 legacy-coupling inventory and serial migration boundary. Its [accepted Proposal](../proposals/observation_experiment_ownership_v2.md) owns the future target; until the explicit authority-cutover gate is satisfied, legacy Experiment authority remains active.

Current framework-wide contracts include the
[LLM Inference Harness Contract](llm_inference_harness_contract.md), which owns
logical model-call identity, prompt/context/tool/output policy, and its link to
physical tokenomics attempts.

Market-data computation and storage lifecycle are owned by the
[Technical Analysis and OHLCV Tiering Contract](technical_analysis_contract.md).
Exact-timeframe and real-field provider results are owned by the
[OHLCV Provider Result Contract](ohlcv_provider_result_contract.md).
Neutral causal Swing/BOS/CHOCH facts and query-requested FVG are owned by the
[Market Structure Derived Data Contract](market_structure_contract.md).
Collector execution facts and persisted dataset freshness are kept separate by
the [Market Data Coverage and Collector Health Contract](market_data_coverage_contract.md).

Profit-loop replay, candidate/Position denominator separation, frozen opening
hypotheses, exploratory counterfactuals, and seven-layer winner gates are owned
by the [Profit-loop observability contract](profit_loop_observability_contract.md).

Learning Knowledge empty, stale, and unavailable presentation semantics are
owned by the
[Dashboard Data Presentation Contract](dashboard_data_presentation_contract.md).

The system status **headline** — which backend facts the operator UI derives it
from, in which order, and the rule that a change to one of those inputs must update
every declared consumer in the same change — is owned by the
[Status Surface Contract](status_surface_contract.md). Its producer side (the
supervised TypeScript operator process) is described in
[The Operator runtime](../architecture/OPERATOR_SHELL.md).

The [Trading Mandate Contract](trading_mandate_contract.md) owns the single
optional observe-only mandate's canonical identity, deterministic fit evidence,
Proposal freeze, Position Replay exposure, and descriptive compliance reports.

The [Settings Control Persistence Contract](settings_control_contract.md) owns
the explicit immediate-versus-staged frontend control semantics and the checked-in
Settings switch inventory.

The [Configuration Loading Contract](configuration_contract.md) owns the
strict persisted-config boundary, safe validation errors, and Doctor blocker
projection.

The [Macro Source-Time Contract](macro_source_time.md) owns TradFi/macro
source-time freshness semantics (#1465).

The [Profit-gradient evidence compiler contract](profit_gradient_evidence_contract.md)
owns the read-only `profit_gradient_evidence/v2` compiler.

The [Universe Baseline Integrity Contract](universe-baseline-integrity.md) owns the
Universe economic baseline integrity boundary (#2556).

The [Entry Plan Contract](entry-plan-contract.md) owns exact-venue Candidate
eligibility, qualitative planner tactic authority, deterministic three-branch
compilation, maker Order lifetime, and deep-tier provider admission.
