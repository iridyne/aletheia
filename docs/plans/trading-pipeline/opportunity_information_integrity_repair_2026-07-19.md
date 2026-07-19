# Opportunity Information Integrity Repair

Date: 2026-07-19

## Outcome

Close the audited P0 and P1 gaps between collected market/research data and the
durable Opportunity decision chain. The repair must make every input truthful,
instrument-scoped, freshness-bounded, source-attributed, and behavior-tested.

Policy epoch: `opportunity_information_integrity/v1`.

This is one B-class repair epoch because the changes are deployed together and
can all change what the System Operator sees. Results from this epoch must not be
pooled with earlier Opportunity conversion, decision, or outcome windows.

## Boundaries

Allowed:

- `aletheia/agents/`, `aletheia/data/`, `aletheia/universe/`, `aletheia/regime/`
- `aletheia/agent_shell/`, `aletheia/analytics/`, non-authority storage helpers
- scheduler registration for bounded read/research jobs
- focused tests and executable contracts

Excluded:

- Risk Engine behavior or limits
- Execution Gateway, brokers, exchange clients, auth, or signer logic
- Live unlock, credentials, permissions, or restricted tools
- new exchange, database service, or live capability

## Acceptance Matrix

| ID | Requirement | Proof |
| --- | --- | --- |
| P0-01 | Observer pre-filter consumes structured 1h RSI/ATR facts, never Markdown text | production-format behavioral test |
| P0-02 | Hyperliquid and Coinalyze OI delta uses a same-source 45-75 minute baseline | collector behavior tests |
| P0-03 | Spot context and features never inherit perp funding/OI/liquidation/Position X | spot/perp isolation tests |
| P0-04 | Regime uses true 7-day market-cap change and completed-candle true range | deterministic regime tests |
| P0-05 | Prediction direction requires an exact asset and price-threshold polarity; ambiguous or non-finite inputs fail closed | polarity/identity/numeric matrix + pipeline test |
| P0-06 | News symbols use exact entities; publication time is bounded; analysis is bounded and failures never become analyzed | entity/time/analyzer/scheduler tests |
| P1-01 | Opportunity metadata records the current T0 trigger and only T1 source identities actually rendered to the model | manifest behavior tests |
| P1-02 | PiDecision evidence carries T0/T1 provenance without reconstructing historical sources | exact-source manifest tests |
| P1-03 | Serenity pending carriers hydrate their exact thesis/evidence/score | decoy/top-five regression test |
| P1-04 | Partial Serenity promotion remains retryable; evidence additions reassess active opportunities; only one exact final consumes a carrier | lifecycle/integrity tests |
| P1-05 | Terminal Serenity skip advances source state atomically | decision lifecycle test |
| P1-06 | Trade cases and post-trade review consume complete persisted rationale and source manifests, and only mark records actually rendered | no-truncation/selection tests |
| DOC-01 | Data-utilization and agent contracts describe runtime behavior, not planned behavior | spec conformance |
| OBS-01 | Every acceptance row has a direct behavior assertion | coverage checklist test |

## Verification

Focused tests run before broader suites. The final evidence must include:

1. Every acceptance ID mapped to at least one passing behavioral test.
2. Coverage for changed modules with no uncovered decision branch in the new
   behavior.
3. Relevant specs and end-to-end Opportunity tests.
4. Full `tests/` suite.
5. `aletheia doctor` from the worktree, without restarting the global gateway.

Passing structural string checks alone does not satisfy this plan. Resource-heavy
full-suite and line-coverage runs are not required for this repair on the target
device; the acceptance evidence is the focused behavior/spec matrix plus lint and
diff checks.

## Completion Evidence

- Focused Opportunity information-integrity and adjacent regression matrix:
  `574 passed`.
- Repository Python lint: `uv run ruff check aletheia tests` passed.
- Patch integrity: `git diff --check` passed.
- The full `tests/` suite was intentionally stopped at the owner's request because
  it is too CPU-intensive for the target device; no full-suite result is claimed.
