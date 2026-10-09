# PR Review Lifecycle

This policy is the single authority for pull-request review budgets, states,
feedback validity, and merge readiness. There is no branch protection or remote
CI gate: required verification is the local `scripts/ci/ci_preflight.py` /
`scripts/ci/ci_local.py` receipts, and merges are manual (`gh pr merge <n> --rebase`)
by the owner.

## Scope and review budget

External review activity is evidence, not a command. A new review, comment, or
notification never reopens completed work automatically.

Codex gets two automatic rounds per PR: the initial review and one verification
review after fixes. A commit made solely for Codex feedback does not reset that
budget. Other automated or human feedback is not limited by this budget, but
new activity does not change the PR state by itself.

Before acting, identify the reviewed commit. Feedback for an older SHA is stale:
re-check whether the finding still exists, and never let stale feedback alone
block merge.

## States and merge readiness

Every PR is exactly one of `WORKING`, `REVIEWING`, `FIXING_REVIEW`,
`MERGE_READY`, or `MERGED`; transitions are explicit.

Enter `MERGE_READY` only after implementation, relevant tests, the local preflight
receipts, and known blockers are complete, and the Codex budget is complete, exhausted,
timed out, or intentionally skipped. Record the approved current head:

```text
MERGE_READY_SHA=<current PR HEAD SHA>
```

Normal findings on that SHA (style, refactoring, speculative concerns, low-impact
edge cases, or minor performance ideas) do not revoke readiness; use a follow-up
issue when useful.

Only concrete evidence of a deterministic correctness failure, security or
destructive behavior, data corruption/loss, broken public contract, confirmed
supported-behavior regression, incorrect financial/trading execution, or invalid
required verification may revoke readiness. Then transition
`MERGE_READY → FIXING_REVIEW`, fix and verify, update `MERGE_READY_SHA`, and
return directly to `MERGE_READY`. This emergency correction does not create an
unlimited review cycle.

## Pre-merge and post-merge

Immediately before merging, fetch the current head, confirm it equals
`MERGE_READY_SHA`, confirm the local receipts cover that head, and evaluate newly arrived
feedback using the stale-SHA and hard-blocker rules. Do not wait indefinitely or
request another review merely because activity arrived.

After `MERGED`, feedback never reopens the old PR. A valid hard blocker gets a
new corrective issue/PR; a normal improvement may get a follow-up issue.

The default path converges:

```text
implementation → Codex review → one batch repair → optional verification
→ MERGE_READY → merge
```
