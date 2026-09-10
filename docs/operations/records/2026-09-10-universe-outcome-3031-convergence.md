# #3031 B: canonical Universe Outcome ancestry

Owner accepted the A checkpoint's source-identity D1 on 2026-09-10. The accepted
[decision checkpoint](https://github.com/iridyne/aletheia/issues/3031#issuecomment-5613684212)
retains #3031 as the phase/status authority. Base: `e991e7f48ce8cb552bb65e16e0edbea6065a5e53`.
This record concerns one implementation batch, not G0–G5 acceptance or deployment.

## Change and evidence

Previously a real fixture Universe Plan → Proposal → Order → private Fill →
Position had complete canonical ancestry but NULL `opportunity_id`, so the
shared Outcome gate rejected it. The new end-to-end regression failed before
implementation at `attribution_chain_complete(position)` (`1 failed`, 1 collected).

The producer and snapshot cohort now share one SQL ancestry rule in
`analytics/outcome_integrity.py`, replacing the two separately maintained
queries. It verifies the Plan/Generation/Candidate and Order/Proposal backlinks;
Serenity and retained Opportunity history use their source-specific identity.
An unrelated Opportunity cannot rescue a broken Plan-bound source chain.
`analytics/trade_attribution.py` projects the accepted Plan/Generation/Candidate
IDs instead of independently rejecting a missing Opportunity. No source facts
or synthetic Opportunities are invented.

Contracts updated: Learning §11.1, Entry handoff, trust policy §1.1; PL-OF-01/04
wording is source-specific and PL-OF-20 proves the canonical Universe handoff.
The change modifies no execution/Risk code, schema, migration, credentials,
Alpha, economics selection, tactic or Live setting.

Final focused command:

```sh
uv run --no-sync pytest -q \
  tests/e2e/test_pipeline_universe_outcome.py \
  tests/specs/spec_pipeline_outcome_feedback.py \
  tests/specs/spec_pipeline_multileg_outcome_attribution.py \
  tests/test_outcome_integrity_read_model.py \
  tests/test_outcome_closure.py \
  tests/uta/test_execution_coordinator.py \
  tests/specs/spec_broker_funding_settlement.py \
  tests/test_entry_learning_context.py \
  tests/test_trade_attribution.py \
  tests/test_factor_feedback.py \
  tests/test_post_trade_reviewer_outcomes.py
```

Result: **184 passed / 184 collected**, including the canonical close → shared
Outcome → Trade Attribution / Learning reader and hook idempotency, broken
Order/Proposal/Generation/Candidate/source links, missing fee/funding or private
fill verification, administrative close, persisted negative eligibility,
clean-window exclusion, canonical Serenity identity and existing account/replay
and funding settlement checks. Candidate expiry/Generation supersession after
execution leaves the historical causal chain valid. Docs governance, PR metadata
preflight and whitespace checks also pass.

The new fixture uses the existing Risk decision/UTA doubles and bypasses only
fixture runtime fingerprint matching. It uses RESTING_MAKER with a real receipt,
real persistence/materialization and native close path. Broker funding evidence
is seeded in the isolated fixture; its transport is covered by existing funding
specs, not claimed as a live result. The changed ancestry and consumer boundaries
are not mocked. No additional test platform or production abstraction was added.

## Review and remaining boundary

Local diff review checked all shared gate callers, the direct close write and
single-snapshot read consistency, multi-entry-leg matching, the source-specific
projection, negative persisted markers, and the unchanged hook eligibility
recheck. Optional external review is intentionally skipped; no remote GitHub CI
was dispatched or awaited.

Existing negative Position eligibility stays negative on reads. No bulk
reclassification, historical payload/hash/economics rewrite, completed-job
reopening or measurement-window replay is performed. Existing explicitly
retryable evidence recovery keeps its existing authority. The closure/economic
`oi-v1` format and clean-window anchor are unchanged. A rollback reverts this
code/Contract delta against the same v79 schema; it does not roll back history.

The A baseline's 13 failing TRIGGERED Entry fixtures and #3008 strict xfail are
outside this focused denominator and are not reported as repaired. #2866's
separate prompt/inference identity mismatch remains; accepting a genuine
Position into the shared Learning reader does not prove the entire bounded
Entry context is available or effective.

No deployment, restart, exchange mutation, qualification artifact creation,
Observation window reset or live Fedora verification of this new implementation
was performed. The last Fedora evidence remains the A packet's 1543c7c1 / v79 /
Bitget Demo session. This batch can establish code/local regression convergence;
G5 and natural runtime evidence remain pending and #3031 remains open.
