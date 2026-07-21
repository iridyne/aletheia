# Paper Funding Accounting Integrity Repair Plan

Date: 2026-07-21

Issue:
[`PAPER_FUNDING_ACCOUNTING_INTEGRITY_2026-07-21.md`](../../issues/audits/PAPER_FUNDING_ACCOUNTING_INTEGRITY_2026-07-21.md)

Classification: A-class accounting repair, no strategy policy-epoch reset

## Outcome

Make every local-paper futures close converge to one truthful and reproducible
economic result:

```text
realized PnL
  = directional price PnL
  - entry fee
  - exit fee
  - each canonical funding settlement exactly once
```

The repair must eliminate false unavailable states, eliminate duplicate funding,
atomically synchronize every accounting projection, recover eligible historical
rows without guessing, and add permanent diagnostics that catch recurrence before
the operator or learning pipeline consumes a wrong value.

## Boundaries

Allowed implementation surfaces:

- `aletheia/portfolio/local_funding.py`
- `aletheia/portfolio/paper_account.py`
- narrowly scoped local-paper close orchestration in
  `aletheia/execution/paper_broker.py` and `aletheia/portfolio/pnl.py`
- funding-history normalization in
  `aletheia/data/collectors/derivatives.py`
- `aletheia/portfolio/outcome_hooks.py`
- focused scheduler health/diagnostic wiring
- additive repository helpers required for one atomic transaction
- focused specs, E2E tests, operator diagnostics, and contract documentation

Excluded:

- external exchange order placement or account mutation;
- Bitget/OKX/Binance auth, credentials, signer logic, or live unlock;
- hard Risk limits, sizing, ranking, strategy, prompts, or agent behavior;
- `TC-X01` exchange-account funding bill collection;
- treating unknown funding as zero;
- deleting or rewriting append-only paper cash history;
- adding a new exchange, database service, or broad accounting framework.

Controlled-module note:

- `aletheia/execution/paper_broker.py` is change-controlled.
- Live behavior: unchanged.
- Secrets: unchanged.
- Persisted state semantics: local-paper futures close finalization becomes
  canonical, atomic, and correction-audited.
- Minimum proof: focused local-funding, close-materialization, account-loss, and
  outcome-lifecycle specs plus self-hosted CI.

## Design Principles

1. Preserve raw evidence; canonicalize only the accounting identity.
2. Prefer explicit event identity over fuzzy datetime comparison.
3. Separate "collector was live" from "settlement actually occurred".
4. One position settlement has one idempotent materialization owner.
5. A correction changes projections through compensating history, never by hiding
   the prior fact.
6. Every unavailable result names the exact broken proof boundary.
7. No UI reassurance can outrun backend accounting convergence.

## Target Model

### 1. Canonical Settlement Identity

Introduce one internal canonical key used only by local funding accounting:

```text
(exchange, canonical_symbol, canonical_settlement_at)
```

Rules:

- Convert every parsed time to UTC.
- Canonicalize settlement timestamps to whole-second precision.
- Accept sub-second representation skew only when it is strictly less than one
  second and both rows identify the same venue, symbol, and schedule boundary.
- Never merge events one second or more apart.
- Preserve original timestamps and snapshot IDs in evidence.
- Reject ambiguous buckets containing conflicting actual settlement rates rather
  than selecting one silently.

Funding settlements are hours apart, so whole-second identity removes exchange
serialization noise without weakening economic separation.

### 2. Evidence Roles and Precedence

Build a typed internal event view from existing rows:

| Role | Meaning | Accounting use |
|---|---|---|
| `live_observation` | rate and next target observed before settlement | proves live coverage and supplies the event only when no settled value exists |
| `settled_event` | historical row at the canonical settlement boundary | authoritative event rate after it exists |
| `schedule_transition` | row linking one canonical event to the next | proves the next expected event |
| `close_anchor` | current observation before close with target after close | proves the live collector covered the close boundary |

Precedence for one canonical settlement:

1. one unambiguous `settled_event`;
2. otherwise the latest valid `live_observation` within the existing 15-minute
   event window;
3. otherwise incomplete.

The accumulator operates on canonical settlement keys, not raw timestamps. It
must emit one evidence entry and one funding charge per key.

### 3. Two Honest Completeness Modes

`live_coverage` remains the normal close-time mode:

- fresh opening anchor;
- every elapsed canonical settlement observed;
- every transition observed;
- close anchor no older than 15 minutes.

`historical_ledger` is the delayed-recovery mode:

- exact entry Fill lineage passes;
- exact exit Fill and fees are durable;
- one venue/instrument history response covers the requested start/end range;
- actual settlements form a monotonic canonical sequence;
- the sequence includes every event in the holding window;
- the first actual event after close, or an exchange-provided complete range
  boundary, proves there was no omitted settlement before close;
- pagination/completeness is explicit; a bounded `limit=48` response must not be
  called complete when the holding window exceeds its covered range.

The two modes may share canonical event construction but may not combine partial
proofs to manufacture completeness.

### 4. One Atomic Finalization Path

Replace the multi-call delayed-settlement sequence with one idempotent repository
transaction, conceptually:

```text
materialize_paper_close_economics(
    position_id,
    realized_pnl,
    funding_cost,
    complete_funding_evidence,
    outcome_eligibility,
    accounting_version,
)
```

The transaction must:

1. re-read and lock the current Position under the process-wide SQLite writer
   transaction;
2. verify entry/exit/fee snapshots have not changed;
3. compute and compare a deterministic funding-evidence fingerprint;
4. update the Position projection and final reconciliation evidence;
5. materialize `position_close_events.realized_pnl` from `NULL`, or apply an
   audited correction when an existing numeric value is wrong;
6. append exactly one `paper_account_events.position_closed` event for first
   settlement;
7. append a compensating `manual_credit` or `manual_debit` cash event, identified
   by position/source/fingerprint, for a correction to an already settled position;
8. update outcome eligibility and exclusion reason;
9. enqueue each required outcome-hook job exactly once when eligibility changes
   to true;
10. insert the authoritative SQLite audit row with complete before/after values;
11. commit all database changes together.

JSONL audit mirroring and immediate hook processing happen after commit. A
post-commit delivery failure must leave durable jobs for retry and must not make
the caller repeat cash settlement.

### 5. Idempotency

Persist the canonical evidence fingerprint and accounting semantics version in
the existing reconciliation evidence unless implementation proves a dedicated
column is necessary.

Required behavior:

- same fingerprint and same result: no-op;
- same fingerprint and different result: invariant violation, stop and alert;
- new stronger evidence with prior `NULL` result: finalize once;
- new stronger evidence correcting a numeric result: append one compensation and
  one correction audit;
- repeated correction request: no-op;
- existing outcome-hook jobs: never duplicate.

Do not add a new table unless tests demonstrate that existing reconciliation
evidence cannot provide safe durable idempotency. If a schema field is required,
update the data model and executable spec in the same change.

## Work Packages

### WP0: Freeze and Baseline

Goal: preserve evidence before changing accounting semantics.

Actions:

- Record the issue and this plan in Git.
- Capture a read-only dry-run inventory by category: pending, terminal, numeric
  mismatch, duplicate canonical event, cross-ledger mismatch, missing hook jobs.
- Record the current accounting commit and semantics version in the report.
- Do not modify runtime rows.

Exit gate:

- the dry-run is deterministic across two consecutive runs;
- every reported row includes causal Position, Order, Fill, and close-event IDs;
- no credentials, account configuration, or raw secret-bearing exchange payloads
  appear in the report.

### WP1: Test-First Canonical Event Contract

Goal: make settlement identity and precedence executable before code changes.

Add focused cases to `tests/specs/spec_pipeline_local_funding.py`:

| ID | Case |
|---|---|
| PFA-LF-01 | `00.000` live target and `00.001/002/004` historical event become one canonical settlement. |
| PFA-LF-02 | A canonical settlement contributes exactly once. |
| PFA-LF-03 | Settled historical rate supersedes the pre-settlement observation for the same event. |
| PFA-LF-04 | Conflicting settled rates in one canonical bucket fail closed. |
| PFA-LF-05 | Events at least one second apart are never merged. |
| PFA-LF-06 | Missing canonical event remains unavailable. |
| PFA-LF-07 | Complete historical sequence can bracket a mid-window close. |
| PFA-LF-08 | Truncated/paginated history cannot claim completeness. |
| PFA-LF-09 | Entry Fill identity and active-notional rules remain unchanged. |
| PFA-LF-10 | Directional funding signs remain unchanged. |

Exit gate: tests fail for the current exact-timestamp implementation for the
intended reason.

### WP2: Canonicalize Accounting, Not Raw Storage

Goal: implement the smallest correct event builder.

Actions:

- Add a private canonical settlement-key helper in `local_funding.py`.
- Build canonical event buckets while retaining raw evidence.
- Apply the explicit role/precedence rules.
- Make live and historical completeness separate entry points that share only
  validated event construction and arithmetic.
- Keep raw funding snapshots unchanged unless a narrowly scoped normalization at
  ingestion is proven safer and fully backward-compatible.
- Remove raw timestamp equality as an economic identity check.

Exit gate:

- PFA-LF-01 through PFA-LF-10 pass;
- all existing local-funding specs remain green;
- no spot or exchange-backed behavior changes.

### WP3: Atomic Delayed Materialization

Goal: close every post-recovery consistency gap.

Add tests before implementation:

| ID | Case |
|---|---|
| PFA-MAT-01 | Delayed recovery updates Position, close event, cash ledger, evidence, eligibility, and hook jobs together. |
| PFA-MAT-02 | Failure at each write stage rolls the entire transaction back. |
| PFA-MAT-03 | Repeated recovery creates no duplicate cash or hook job. |
| PFA-MAT-04 | Numeric correction preserves the close-event identity, updates its economic projection under audit, and appends one compensating cash event. |
| PFA-MAT-05 | Compensation amount equals corrected PnL minus prior settled PnL. |
| PFA-MAT-06 | Incomplete evidence changes none of the numeric accounting projections. |
| PFA-MAT-07 | Eligibility transition enqueues hooks once and post-commit retry processes them. |
| PFA-MAT-08 | Paper daily-loss snapshot sees no intermediate two-table mismatch. |

Implementation actions:

- Introduce one transaction owner for delayed economics finalization.
- Use the same owner for normal close-time finalization where feasible; do not
  create parallel accounting implementations.
- Reuse the constrained `manual_credit` / `manual_debit` actions for compensation,
  with position/source/fingerprint evidence; do not expand the persisted action enum.
- Update reconciliation evidence with final canonical events, source mode,
  fingerprint, and accounting version.
- Keep JSONL mirroring outside the database transaction while preserving the
  authoritative SQLite audit row inside it.

Exit gate: every failure-injection and idempotency test passes.

### WP4: Recovery State and Scheduler Reliability

Goal: prevent recoverable rows from becoming permanently lost because retries did
not execute.

Actions:

- Make retry outcome explicit: `recovered`, `deferred_source_unavailable`,
  `incomplete_history`, or `structurally_unrecoverable`.
- Do not use age alone to label a row structurally unrecoverable.
- Preserve bounded retries and backoff, but emit an operator-visible alert when
  the retry budget is exhausted.
- Re-open legacy terminal rows only through the versioned dry-run repair when the
  fixed algorithm proves complete evidence.
- Resolve the `Cannot operate on a closed database` settler failure and add a
  focused lifecycle regression.
- Align scheduler interval, comments, retry-budget math, and health output.
- Track last successful settler run separately from scheduler invocation.

Exit gate:

- restart/lifecycle tests prove a valid thread-local database connection;
- a failed attempt does not consume a successful-retry count;
- overdue pending rows are visible in doctor/health output;
- no automatic terminal state is produced from elapsed time alone.

### WP5: Dry-Run Historical Reconciliation

Goal: identify every row affected by the old accounting semantics before writes.

Implement a maintenance command with dry-run as the default.

Report categories:

- `recoverable_null_pnl`;
- `terminal_now_recoverable`;
- `duplicate_settlement_numeric_mismatch`;
- `close_event_projection_mismatch`;
- `paper_cash_ledger_mismatch`;
- `missing_outcome_hooks`;
- `still_incomplete` with exact reason.

Each report row must include:

- position and causal IDs;
- old/new accounting semantics version;
- raw and canonical settlement IDs;
- old and proposed funding/PnL;
- old and proposed eligibility;
- required cash compensation;
- required hook jobs;
- a deterministic repair fingerprint.

The command must:

- perform no writes without an explicit apply flag and owner approval;
- refuse apply when the report fingerprint is stale;
- use the normal atomic finalization service rather than direct SQL;
- stop on ambiguous/conflicting evidence;
- remain idempotent after partial process interruption.

Exit gate: two dry runs match byte-for-byte for unchanged data.

### WP6: Apply Approved Corrections

Goal: converge runtime accounting without hiding history.

Preconditions:

- focused tests and self-hosted CI are green;
- dry-run report reviewed by the owner;
- runtime backup completed through the normal operational procedure;
- shared gateway state is stable;
- exact apply scope is approved by position ID/fingerprint.

Apply order:

1. pending rows with complete evidence;
2. terminal rows newly proven complete;
3. numeric mismatches requiring cash compensation;
4. close-event/evidence projection mismatches;
5. missing outcome-hook jobs.

For each row, verify immediately:

- one canonical PnL;
- synchronized Position and close-event projection;
- exactly one initial cash settlement plus at most one versioned compensation;
- complete reconciliation evidence and audit before/after;
- correct eligibility;
- required hook jobs present exactly once.

Exit gate: post-apply dry run reports no actionable mismatch.

### WP7: Permanent Prevention and Observability

Goal: detect recurrence before a wrong value reaches the owner or learning loop.

Add doctor/health checks:

| ID | Diagnostic |
|---|---|
| PFA-OBS-01 | count canonical settlement buckets with conflicting rates |
| PFA-OBS-02 | count Position vs close-event PnL mismatches |
| PFA-OBS-03 | count settled Position vs paper cash ledger mismatches |
| PFA-OBS-04 | count eligible delayed settlements missing outcome-hook jobs |
| PFA-OBS-05 | pending recovery age and last successful settler timestamp |
| PFA-OBS-06 | terminal rows that the current accounting version can now recover |
| PFA-OBS-07 | accounting semantics versions represented in the selected analytics window |

Behavior:

- any numeric cross-ledger mismatch is unhealthy, not informational;
- pending recovery is a warning after two successful settler cadences;
- conflicting canonical events fail closed and alert;
- diagnostics are read-only and bounded;
- no health probe performs recovery writes.

Exit gate: seeded failures produce the expected doctor/health status and healthy
fixtures remain green.

### WP8: Contract and Data-Model Parity

Goal: make the repaired semantics durable and prevent a later implementation from
reintroducing the old model through an undocumented shortcut.

Update together with implementation:

- `docs/design/contracts/execution_contract.md`: canonical settlement identity,
  evidence precedence, live vs historical completeness, and delayed finalization;
- `docs/design/data/data_model.md`: finalization fields, reconciliation evidence
  fingerprint/version, close-event convergence, and
  `position_economics_adjustment` cash action;
- `docs/design/data/data_utilization.md`: historical funding use in delayed local
  accounting recovery, without claiming exchange-account broker funding;
- `docs/design/contracts/risk_engine_contract.md`: the Position/cash/close-event
  consistency requirement consumed by authoritative paper loss;
- `docs/design/contracts/scheduler_contract.md` and
  `docs/design/contracts/agent_contracts.md`: actual settler cadence, essential-job
  behavior, durable success/failure status, and outcome-hook replay;
- the matching executable spec claims and conformance registration.

Rules:

- documentation must describe merged runtime behavior, not the desired future;
- no contract may call public funding history `broker_ledger`;
- no contract may imply that age alone proves structural unrecoverability;
- compensation remains an owner-approved append-only action, consistent with the
  data-model migration rules;
- line-number references and scheduler registry tables must be refreshed from the
  final implementation.

Exit gate:

- every new acceptance ID maps to a behavioral test;
- contract, data model, and implementation use the same state names and accounting
  version;
- documentation-link and conformance checks pass.

## Acceptance Matrix

| ID | Requirement | Proof |
|---|---|---|
| PFA-A01 | Millisecond skew cannot split one settlement | PFA-LF-01/02 |
| PFA-A02 | Actual settled history has deterministic precedence | PFA-LF-03/04 |
| PFA-A03 | Distinct settlements remain distinct | PFA-LF-05 |
| PFA-A04 | Partial evidence never publishes PnL | PL-LF-02, PFA-LF-08, materializer complete-evidence guard |
| PFA-A05 | History can honestly recover a closed interval | PFA-LF-07 |
| PFA-A06 | Entry lineage, notional, sign, and fees do not regress | PFA-LF-09/10 plus existing specs |
| PFA-A07 | Delayed economics materializes atomically | PFA-MAT-01/02 plus authoritative account-loss specs |
| PFA-A08 | Settlement and correction are idempotent | PFA-MAT-01/04 |
| PFA-A09 | Eligibility transition delivers downstream hooks | PFA-MAT-01 |
| PFA-A10 | Recovery survives restart/database lifecycle | PFA-MAT-09 |
| PFA-A11 | Historical repair is dry-run-first and fingerprint guarded | PFA-REC-01/02 |
| PFA-A12 | Recurrence is detected before consumption | PFA-REC-03 plus Doctor focused tests |
| PFA-A13 | Contracts and executable specs describe the same merged behavior | WP8 parity review and conformance tests |

## Verification Commands

Run focused proof locally on macOS; do not run the large full pytest mission on
this device.

```sh
uv run pytest tests/specs/spec_pipeline_local_funding.py -q
uv run pytest tests/specs/spec_pipeline_paper_close_materialization.py -q
uv run pytest tests/test_authoritative_account_loss.py tests/test_paper_account.py -q
uv run pytest tests/e2e/test_outcome_integrity_full_lifecycle.py -q
uv run ruff check aletheia tests
git diff --check
uv run aletheia doctor
```

If frontend source changes, also run:

```sh
cd frontend
npm run build
```

Broader tests must run through the repository's self-hosted CI or an approved
non-macOS machine. The PR and CI record are required evidence for this
safety-sensitive accounting repair.

## Deployment and Observation

Deployment sequence:

1. Merge code and executable contracts with dry-run mode only.
2. Deploy without applying historical corrections.
3. Observe at least one full funding cycle and verify new closes contain one
   canonical event per settlement.
4. Review and approve the dry-run historical report.
5. Apply the fingerprint-locked repair set.
6. Re-run doctor, cross-ledger diagnostics, and the dry-run report.
7. Observe 48 hours or at least two full longest-interval funding cycles,
   whichever is longer.

Success signals:

- zero duplicate canonical settlements;
- zero cross-ledger numeric mismatch;
- zero eligible closes missing hooks;
- no unexplained pending row past two successful settler cadences;
- no paper account blocked by a recoverable timestamp representation mismatch;
- no change in live/demo-exchange order behavior.

## Rollback

Code rollback and accounting correction rollback are separate.

- Code may roll back to the previous release if new closes fail the acceptance
  diagnostics.
- Never delete compensation events or rewrite append-only cash history.
- If a correction itself is proven wrong, apply a new versioned compensating
  event and audited projection correction.
- Historical apply remains disabled until the fixed version is redeployed and a
  new dry-run fingerprint is approved.
- Outcome hooks created by a correction are not deleted; if their input is later
  invalidated, mark the derived artifact ineligible/deprecated through its normal
  lifecycle.

## Positive Project Contribution

This repair is complete only if it reduces total system complexity rather than
adding another special case.

Expected durable gains:

- one reusable settlement identity for live and historical paper funding;
- one atomic close-economics finalization path instead of separate repair writes;
- consistent Position, close-event, cash, audit, and learning facts;
- fewer false account freezes without weakening unknown-data safety;
- explicit accounting-version boundaries for honest analytics;
- deterministic dry-run tooling for future accounting migrations;
- health checks that turn silent PnL drift into an immediate actionable failure.
- aligned execution, data-model, risk, scheduler, and agent contracts that prevent
  semantic drift from recreating the bug under a different helper.

## Non-Goals

- No strategy optimization or new observation epoch.
- No approximate funding based on current rate.
- No direct repair SQL.
- No new trading capability.
- No exchange-backed funding-bill implementation.
- No weakening of causal IDs, fill authority, fee validation, or UI honesty.
