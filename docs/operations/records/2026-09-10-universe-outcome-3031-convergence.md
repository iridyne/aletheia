# #3031 B: canonical Universe Outcome ancestry

Owner accepted the A checkpoint's source-identity D1 on 2026-09-10. The accepted
[decision checkpoint](https://github.com/iridyne/aletheia/issues/3031#issuecomment-5613684212)
retains #3031 as the phase/status authority. Base: `e991e7f48ce8cb552bb65e16e0edbea6065a5e53`.
This record concerns one implementation batch and its subsequent Fedora deployment;
it does not claim G0–G5 acceptance.

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

At the implementation checkpoint, deployment had not yet been performed and
Fedora remained at the A packet's 1543c7c1 / v79 / Bitget Demo session. The
subsequent deployment is recorded below. G5 and natural runtime evidence remain
pending; #3031 remains open.


## Fedora deployment checkpoint — 2026-09-10

After the owner asked to continue #3031 at the deployment gate, the existing
command was run on Fedora:

```sh
cd /home/admin/aletheia
uv run --no-sync aletheia update --verbose
```

The transaction completed in **21.127 s**, updating `1543c7c1` to
`5ba82d189cca78d899ff5d3e5d7c1d07483d2459`, with dependency reinstall unnecessary.
Smoke, Gateway restart and Observer structural continuity passed. The updater
correctly reported `main_health=initializing`; it did not claim healthy at that
point. An independent postflight at **05:57:09 UTC** then proved healthy, no
issues/pause owners/blockers, and the scheduler running.

[Before/after and source-query evidence](evidence-3031/fedora-deployment.json)
records:

- disk, API boot/current and process-tree-verified session boot all equal the
  exact merged target; backend PID 372195, session `0a12ec905278`, started
  05:55:25.473742 UTC, schema `aletheia-sqlite-v79`;
- Bitget Demo / classic_v2, exact account fingerprint and credential generation
  unchanged, account/position cache complete, zero open local/external positions,
  Live disabled, Full permission and `require_llm_at_startup=false` unchanged;
- Worker Observation ownership remains exclusive, Main cycle not registered,
  data-root identity unchanged; the existing updater verified Observer continuity;
- SHA-256 over the explicitly listed historical fields matches before/after for
  847 Orders, 838 Fills and 419 Positions, including Position economic and
  eligibility fields. This proves those fields, not an unmeasured whole-DB hash;
- new-session Position Reconciler `1d82190cfbf3` and Market Observer
  `d06cebde7608` completed. All 754 existing Outcome hook jobs are completed,
  with no pending/failed job observed. With no external Position, this is idle
  settlement/recovery health, not successful nonzero external settlement;
- target-loaded, read-only shared Outcome cohort query on the real DB succeeds:
  419 raw closed records, 0 eligible. Session-start to 05:57:28 UTC contains
  0 new Generations/Plans/Proposals/Orders/Fills/Positions. It does not provide a
  natural post-fix close/Outcome sample.

Startup normally rotates deployment-bound evidence anchors via
`learning/evidence_epoch.py::ensure_evidence_epochs`: prior profit-loop epoch
`802bca4a142b` is retained with its original start and closes at 05:55:27 UTC;
new epoch `d24de8833825` names the target SHA. There was no manual experiment
reset, historical sample replay or payload/economics rewrite. Do not describe
normal source-provenance segmentation as an unchanged epoch ID.

**Deployment and post-start health are verified; Gate B is not qualified.**
The mutation qualification artifact remains absent, and no qualification order
was sent. G5 mirrors #2899 pending; natural runtime evidence remains pending.
This update does not authorize another defect batch, Live or controlled venue
qualification trades.
