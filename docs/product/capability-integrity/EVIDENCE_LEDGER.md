# Capability Integrity Evidence Ledger

## Review identity

| Field | Value |
|---|---|
| Repository | Aletheia |
| Review branch | `codex/capability-integrity-review` |
| Pinned base | `0ad6157878967725bf181a60d9fe697a9b897476` |
| Base subject | `Merge pull request #381 from iridite/fix/gateway-restart-eio` |
| Remote checked | 2026-07-28 |
| Code index | Local CodeGraph index, 1,020 files / 21,566 nodes / 69,839 edges |
| Runtime mutation | Isolated temporary data directory only |
| Product code repair | Bounded honesty/integrity repairs recorded in follow-up sections |

## Persona progressive unlock follow-up

2026-08-17 resolution: the original lifetime-history finding is repaired by a
durable rollout state machine. This ledger's original review remains historical;
the current implementation persists exact rollout/step identity and Position
manifests, requires scoped post-anchor cost-aware evidence with sample and
market-day coverage plus a stable non-degrading verdict, advances one parameter
at a time, and refreezes on invalid/degrading/distress conditions.

The original review found that the mechanism used lifetime eligible closes
rather than a prospective Persona/policy cohort. That historical limitation was
not promoted to Supported; the current follow-up supersedes it with the durable
rollout framework above while retaining the two production-consumed entry
parameters and the Experimental/default-off boundary.

Executable verification:

- Historical Persona sizing specification: **14 passed**
- Historical full executable SDD suite: **1,360 passed, 10 skipped, 15 xfailed**
- Historical SDD conformance checks: **7 passed**
- Frontend compile was not run because this worktree has no installed frontend
  toolchain (`tsc` unavailable); the Settings contract text is protected by
  `PS-S10`.

## Persona rollout automation follow-up

Historical finding: the eight-hour scheduler interval was incorrectly represented as an
observation window. The job could enable progressive unlock against lifetime
historical outcomes without a prospective Persona/policy/step verdict. The
current follow-up replaces that path with the durable evidence-gated state
machine; the interval is now only a recheck cadence.

Executable verification:

- Historical Persona sizing plus reconciliation-integrity paths: **22 passed**
- The current `PS-S13` contract protects the evidence-gated scheduler and
  owner-facing safety boundary; rerun focused CI after this follow-up.

## Discovery coverage

The inventory pass treated these as discovery surfaces, not independent proof
of functionality:

| Surface | Coverage result |
|---|---|
| Dashboard | 13 primary pages inspected and mapped to outcome-level capabilities |
| Settings | 7 Settings groups and their product copy/config writes inspected |
| API | 314 FastAPI route declarations across 21 route modules grouped by final outcome |
| CLI | 129 Typer command declarations across 26 modules grouped by final outcome |
| Scheduler | 44 registered jobs mapped to their producers, state, and consumers |
| Agent contracts | Active Operator, Risk, live, pipeline, and Learning contracts inspected |
| Active scope | `docs/product/ACTIVE_RUNTIME_SCOPE.md` used as the core boundary |
| Product/docs | README, product, operations, API, data, and current design contracts traversed |
| Core modules | Runtime, data, universe, Operator, Risk, Execution, Position, Analytics, Learning |

The current inventory uses unique capability IDs. The same IDs must appear
exactly once in the Ground Truth status matrix and exactly once in the
fact-card set; the inventory size itself is not a product invariant.

## Executable evidence

All tests below were run against the current reviewed worktree with
`uv run pytest -q`.
No full-suite run was used as a substitute for focused chain checks.

### Final repository verification

After the focused chain reviews and bounded repairs, the complete repository
suite was run with local loopback access enabled:

```text
4882 passed, 25 skipped, 15 xfailed
```

The first restricted-environment run exposed three expected loopback-bind
permission failures and one calendar-fragile analytics test. The three release
tests passed when loopback access was allowed. The analytics fixture had
ignored its `days_ago_list` and used a fixed July 12 timestamp; it now derives
close times relative to the current clock and passes independently and in the
full suite.

All modified Python files also pass Ruff, and `git diff --check` passes.
The lockfile-pinned frontend dependencies were installed for verification:
the production TypeScript/Vite build passes, and ESLint completes with zero
errors and 23 warnings. Generated dependencies and build output
remain ignored and are not part of the review diff.

### Run A — core chain and high-risk seams

Result: **127 passed, 2 skipped**

```text
tests/specs/spec_active_runtime_scope.py
tests/e2e/test_pipeline_observer_to_operator.py
tests/e2e/test_pipeline_real_paper_roundtrip.py
tests/e2e/test_pipeline_position_to_close.py
tests/specs/spec_pipeline_outcome_feedback.py
tests/specs/spec_pipeline_risk_gate.py
tests/specs/spec_learning_final_consumers.py
tests/specs/spec_persona_sizing.py
tests/specs/spec_pipeline_live_unlock.py
tests/test_market_wake.py
tests/test_trade_attribution.py
tests/test_backup_integrity_fixes.py
```

### Run B — runtime, packaging, persistence, secrets, notifications

Result: **103 passed, 1 skipped**

```text
tests/specs/spec_gateway_stability.py
tests/test_scheduler_timeout_fix.py
tests/test_scheduler_timeouts.py
tests/test_packaged_update.py
tests/test_packaged_update_activation.py
tests/test_packaged_update_recovery.py
tests/test_packaged_update_routes.py
tests/test_backup_wal_safety_fix.py
tests/test_secrets.py
tests/test_secrets_cli.py
tests/test_telegram.py
```

### Run C — market data and universe

Result: **56 passed, 2 skipped**

```text
tests/specs/spec_pipeline_market_data.py
tests/test_market_data_coverage.py
tests/test_price_feed.py
tests/test_market_observer_integration.py
tests/e2e/test_pipeline_universe_to_operator.py
tests/test_universe_outcome_economics.py
tests/specs/spec_equity_observer.py
tests/test_xstocks_observer.py
```

### Run D — execution, reconciliation, and outcome integrity

Initial parallel result: **147 passed, 1 failed**. The failure read a
`SimpleNamespace` config written by another concurrently running test process.
The exact failing case was then rerun alone and passed:

```text
tests/e2e/test_outcome_integrity_full_lifecycle.py::
test_administrative_close_is_raw_only_and_risk_fails_closed_without_fake_loss
```

Combined confirmed result: **148 passed**. This is recorded as test-isolation
interference, not as a capability defect; the 147 unaffected cases passed in
the original process and the affected case passed in isolation.

The Run D set was:

```text
tests/specs/spec_pipeline_risk_execution_integrity.py
tests/specs/spec_pipeline_paper_trade.py
tests/specs/spec_pipeline_paper_close_materialization.py
tests/e2e/test_outcome_integrity_full_lifecycle.py
tests/test_reconciliation.py
tests/test_position_reconciler.py
tests/test_gateway_order_constraints.py
tests/test_paper_broker_stops.py
tests/test_outcome_closure.py
```

### Run E — Learning and attribution

Result: **90 passed, 2 skipped**

```text
tests/specs/spec_learning_pipeline.py
tests/specs/spec_learning_contribution.py
tests/specs/spec_learning_lifecycle.py
tests/specs/spec_learning_attribution.py
tests/specs/spec_learning_authority.py
tests/test_learning_loop_integration.py
tests/test_post_trade_reviewer_outcomes.py
tests/test_alpha_attribution.py
tests/test_opportunity_attribution_integrity.py
```

### Run F — live-mode reachability

Result: **3 passed**

```text
tests/test_paper_autoloop.py::test_autoloop_never_triggers_live
tests/test_paper_autoloop.py::test_paper_tools_rejects_non_paper_mode
tests/test_server.py::test_exchange_mode_change
```

These assertions confirm that the autonomous paper loop does not produce a live
order, the paper wrapper rejects a live mode, and the independent
`exchange.mode` setting can still be set to `live`.

### Run G — runtime recovery and owner infrastructure

Result: **258 passed, 2 skipped**

```text
tests/test_macos_packaged_app.py
tests/test_packaged_bootstrap.py
tests/test_packaged_bootstrap_symlinks.py
tests/test_packaged_runtime_contract.py
tests/test_connectivity.py
tests/test_auto_resume.py
tests/test_layered_pause.py
tests/test_data_retention.py
tests/test_performance_indexes_retention.py
tests/test_retention.py
tests/test_runtime_continuity.py
tests/specs/spec_network_egress.py
tests/test_network_cli.py
tests/test_doctor.py
tests/test_doctor_json.py
tests/test_health.py
tests/test_health_fast_path.py
tests/test_ws_realtime.py
tests/test_model_provider.py
tests/specs/spec_audit_chain.py
```

Aggregate confirmed focused assertions: **785 passed**, with **9 skipped**.
The isolated rerun replaces the one contaminated failure and is counted once.

### Run H — Golden Path promotion gate

Result: **42 passed**

```text
tests/e2e/test_active_mainline_golden_path.py
tests/e2e/test_active_mainline_reachability.py
tests/e2e/test_pipeline_failure_paths.py
tests/test_runtime_replay_pack.py
tests/specs/spec_active_runtime_scope.py
tests/test_runtime_session_provenance.py
tests/test_acceptance.py
```

The frozen Golden Path was reused unchanged. It proves the deterministic
paper-mainline chain and is not treated as natural-runtime evidence.

### Run I — Experimental capability integrity

Result: **399 passed**

Two focused batches reused existing tests for runtime continuity,
equity/prediction observation, Operator extensions, Serenity, exchange
adoption, shadow/counterfactual analytics, attribution, Learning
contribution/lifecycle/filter control, skills, and persona learning/rollout.

The review found one producer-without-consumer gap: Serenity More/Less theme
feedback is durable and visible but does not affect later research (DC-13,
GitHub #429). Passing persistence tests were not treated as proof of behavioral
adaptation.

### Run J — Implemented external-exchange integrity

Result: **156 passed**

The focused set crossed private-fill materialization, multi-broker account
scoping, historical chain recovery, adopted-position identity, TP/SL private
materialization, order and position reconciliation, stale-order cleanup,
reconciliation incident handling, and final outcome integrity.

The tests prove the executable local contract and fail-closed recovery
semantics. They are not presented as natural evidence from a real external
venue, and therefore do not promote EX-06, EX-12, or EX-13 into the current
paper Supported Core.

### Run K — SDD capability alignment

Result: **22 passed**

The Conformance Matrix automatically discovered all **100** executable spec
modules and registered **1,135** claims: 1,110 implemented, 12 partial,
12 missing, and one superseded. The focused run combined the conformance
structure with the Position Manager specs after correcting the human-readable
market-aware exit contract.

The output label is now `Claim implementation ratio: 98%`. It is deliberately
not called coverage, because claim status cannot establish the support status
of a complete product capability. No Runtime, trading authority, or business
behavior changed in this alignment pass.

### Run L — PF-03 honest downgrade

Result: **1,356 passed, 10 skipped, 14 expected xfailed** across the complete
`tests/specs/` suite.

The SDD registry now contains **1,137** claims: 1,111 implemented, 12 partial,
13 missing, and one superseded. `AN-S06` protects the honest owner-facing
semantics; `AN-C06` is the expected missing tripwire for a future distinct
cash-flow-adjusted projection.

The focused Capital/Analytics/conformance run also passed
**17 tests**, with one environment skip and the one expected `AN-C06` xfail.
The frontend source change is a string-only description update. A local
frontend build was unavailable because this worktree has no installed
TypeScript toolchain (`tsc` not found); no dependency installation was added
for this documentation-level repair.

### Run M — EX-04 honest experimental boundary

Result: **33 passed, 2 expected xfailed** across the Live Unlock contracts,
pipeline checks, focused diagnostics, and conformance structure.
The complete executable SDD suite also passed with **1,356 passed, 10 skipped,
15 expected xfailed**; the only additional expected xfail is `LU-C07`.

The SDD registry now contains **1,138** claims: 1,111 implemented, 12 partial,
14 missing, and one superseded. `LU-S03` protects the owner-visible fact that
Live Unlock is an experimental authority prerequisite, while `LU-C07` is the
expected missing tripwire for the absent join from unlocked state to
new-Proposal `*_live_limited` mode selection.

The production selector was not changed and no live path was enabled. Settings,
the Live Unlock contract, and configuration diagnostics now say that autonomous
new Proposals remain on the selected paper/demo execution mode. GitHub #418 was
verified OPEN and remains the durable repair tracker.

### Run N — EX-09 honesty and LN-19 reachability

Result: **1,358 passed, 10 skipped, 15 expected xfailed** across the complete
executable SDD suite. The focused Position Manager, market-wake, policy,
conformance, and owner-copy run passed **69 tests** with two environment skips.

The SDD registry now contains **1,140** claims: 1,113 implemented, 12 partial,
14 missing, and one superseded. `PL-PM-14` keeps Settings aligned with the
wake-only market-aware authority model. `LP-S22` creates two immutable policy
versions and crosses the public diff, detail, and version-history endpoints.

EX-09 moves from `Broken` to `Partial`: its bounded review/wake behavior is
real, while automatic close or stop mutation remains intentionally absent.
LN-19 moves from `Broken` to `Supported`: the existing static route now
precedes the dynamic policy-type route, with no second API or storage model.
GitHub #417 and #428 were verified OPEN during review. #417 remains the
capability-gap tracker; default-branch delivery resolves #428.

### Run O — DC-13 honest boundary and LN-03 final consumer

Result: **14 passed** across the Serenity promotion spec, Dreaming scheduler
output, quantitative lesson evidence, and the applied-lesson-to-Operator-prompt
consumer.

The complete executable SDD and conformance run then passed with **1,366
passed, 10 skipped, and 15 expected xfailed**.

`SP-S07` protects the owner-facing statement that Serenity More/Less records
theme history only and does not change future research selection or prompts.
GitHub #429 was verified OPEN during review; default-branch delivery resolves
it. DC-13 returns from `Partial` to the intentional `Experimental`
surface; no feedback engine or second preference system was added.

LN-03 moves from `Partial` to `Supported`. The existing Dreaming producer emits
lessons with `source_type="trade_case_study"`; the corrected focused acceptance
uses that exact durable identity, advances it through the existing applied
lesson lifecycle, and verifies Layer3 plus the System Operator prompt consume
it. No new scheduler, store, agent, or runtime detector was introduced.

The SDD registry now contains **1,141** claims: 1,114 implemented, 12 partial,
14 missing, and one superseded.

### Run P — LN-14 Persona base-sizing boundary

Result: **14 passed** across the complete Persona sizing spec.

The complete executable SDD and conformance run then passed with **1,367
passed, 10 skipped, and 15 expected xfailed**.

`PS-S14` switches on the existing roll Persona, resolves its notional/Kelly
through the System Operator sizing seam, and verifies that the resulting
notional becomes the immutable Proposal quantity at a fixed price. Settings
now names notional/Kelly as the active base-entry consumers and labels
leverage, stop, and add ranges as separate experimental/reference inputs.

LN-14 moves from `Partial` to `Supported`. No Persona leverage rule, stop
override, scale-in materializer, learning mechanism, or Risk authority was
added. GitHub #409 tracks progressive-unlock evidence; GitHub #410 tracks the
separate pyramid materialization gap.

### Persona pyramid boundary follow-up

CodeGraph confirms that `pyramid_plan()` has test callers only. There is no
Scheduler/Operator trigger and no Risk-approved add Proposal, Order, Fill, or
Position-update consumer. Settings and the trading-config API now block new
enablement, while legacy enabled configurations retain an explicit off-ramp.
`PS-S11`/`PS-S12` continue to protect the bounded helper without representing
it as execution.

The SDD registry now contains **1,142** claims: 1,115 implemented, 12 partial,
14 missing, and one superseded.

## Runtime evidence

An isolated `paper_internal` Runtime was started with a temporary data
directory on port 8791. It ran commit
`0ad6157878967725bf181a60d9fe697a9b897476`, created Runtime Session
`809177ff2ff6`, and shut down cleanly after about six minutes.

Natural results attributable to that session:

- scheduler running with 44 registered jobs;
- 39 core market snapshots;
- 9 orderbook, 15 funding, and 84 open-interest snapshots;
- 4,849 OHLCV rows, including a natural active-universe backfill that reported
  4,219 fetched bars and three ready symbols;
- one dynamic Universe snapshot from 1,000 scanned assets and 30 selected
  members;
- nine causal candidates, all correctly marked execution-ineligible because
  live prices were absent;
- missing LLM configuration surfaced as failed agent runs and
  `health_status=degraded`, so no trade was fabricated.

Doctor also reported facts for the fixed 8787/8788 local instance while the
validated Runtime was on 8791. That historical observation exposed the
cross-instance attribution defect. The reviewed worktree now has executable
serving-Runtime and two-context isolation contracts, so RT-10 is `Supported`;
the old observation remains evidence for why the repair was necessary.

No historical evidence from another SHA was counted. The unavailable CASI
connection and older Windows monitor state remain operational context only.

### Run Q — remaining Partial capability closure

The final five-item pass produced one combined focused acceptance run:

```text
174 passed, 1 skipped
```

- RT-14: owned start/status/stop, legacy pidfile refusal, unrelated PID reuse,
  and non-blocking API dispatch.
- PF-03: durable Capital Events cross into raw plus adjusted Account Equity and
  visible flow markers without changing verified trading PnL.
- LN-17 / EX-15: reference helpers remain bounded while Settings/API and the
  dispatcher prove that no production order or partial mutation can occur.
- EX-16: Bitget/OKX/Binance private funding identity, signed economics,
  ambiguity refusal, retention refusal, atomic settlement, delayed recovery,
  and Outcome/Attribution/Learning hooks.

The Dashboard production build and documentation governance check also passed.
No natural external-venue or cross-platform tunnel operation was used to claim
`Supported`; those complete code paths remain `Implemented`.

## Reviewed broken joins

| Join | Producer exists | Final consumer exists | Verdict |
|---|---:|---:|---|
| Capital events → adjusted performance curve | Yes | Yes; separate adjusted account-equity points and markers | PF-03 Implemented |
| Live-unlocked state → new proposal execution mode | Yes | No | EX-04 Broken |
| Market-aware adverse signal → Operator position decision | Yes | Yes; exact wake evidence reaches bounded hold/adjust/close review and a durable result | EX-09 Supported |
| Persona leverage/loss/add parameters → production sizing/execution | Yes | Partial/no | Reference-only fields cannot progressively unlock; LN-17 pyramid is Experimental; LN-14 base sizing is Supported |
| Exchange private funding bill → cost-complete outcome | Yes; Bitget/OKX/Binance private ledgers | Exact attribution, atomic settlement, and delayed recovery | EX-16 Implemented |
| Pyramid plan → Risk/Gateway/position materialization | Reference helper only | Intentionally blocked | LN-17/EX-15 Experimental |
| Partial-reduce request → authorized broker/position materialization | Reference helper only | Intentionally blocked before broker | EX-15 Experimental |
| Tunnel process record → owned cloudflared process → safe stop/recovery | PID + executable + config command | Yes; mismatch is visible and never killed | RT-14 Implemented |
| Candidate exchange credentials → authenticated private read-only result | Yes; Bitget account info, Binance/OKX balance | Yes; secret-safe result and no persistence | OS-03 Implemented |

## Evidence limitations

- External model, exchange, Telegram, tunnel, and proxy behavior was not invoked.
- No live unlock, live order, exchange demo order, runtime promotion, update, or
  restore was performed.
- E2 tests use bounded fixtures/mocks where external systems are involved.
- `Experimental` is not a weaker spelling of `Implemented`; it is an explicit
  product-support boundary.
- E3 observations are optional operational confidence and do not determine the
  product support status.

### Run P — Market-aware position-review consumer audit

Result: **48 passed** across the focused market-wake, daemon, wake-event,
position-to-close, and Position Manager contract suites at
`963e441983e23eda2f67304cd153c9125f713692`.

The tests prove adverse detection, best-effort wake delivery, explicit delivery
failure, and independent Operator adjustment/close helpers. They do not join
those facts. Current source shows three blocking facts:

1. an empty pending entry queue returns before wake context is attached;
2. attached wake context affects the decision fingerprint but
   `_build_assessment_prompt()` does not render it for the model;
3. `_DECISION_SCHEMA` and `_parse_decision()` accept only `trade`/`skip`, while
   the later `adjust_position`/`close` dispatch branches are unreachable from
   the active model contract.

EX-09 is therefore `Broken`, not `Partial`. This correction adds no runtime
state or trading authority.

### Run Q — Market-aware position-review consumer repair

The bounded repair reuses the existing daemon and
`review_portfolio_state()` command. Position wakes no longer enter the
entry-opportunity evaluator. The review reads the complete open-position set,
scopes it to exact wake ids, renders the wake evidence, and permits only
`hold`, `adjust_position`, or `close_one`. Results carry a deterministic wake
fingerprint in `pi_decisions`; model unavailability is also durable and visible.
Adjustment and close continue through the existing authorized helpers.

Focused acceptance passes **138 tests**, including an adverse-signal producer
through coalesced WakeBatch to a durable Operator hold, exact adjustment,
safe-close behavior, unavailable-result persistence, daemon routing, and the
no-direct-mutation invariant. No Agent, table, scheduler, queue, or direct
evaluator authority was added. EX-09 is `Supported`.
