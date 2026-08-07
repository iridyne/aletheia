# Task 5 Durable Twin Evidence and Replay Review

> Date: 2026-08-08
>
> Verdict: PASS for diagnostic persistence and replay only
>
> Authority scorer: `universe-step-v1`
>
> Runtime/Fedora validation: not performed

## 1. Implemented scope

Task 5 closes the evidence gap left deliberately open by Task 4. Every
coarse-filter survivor now has durable step, continuous point, and continuous
pessimistic score/rank/selection facts. Both continuous arms are replayed through
the current fine filter, preferred-market deduplication, family quotas,
explore/exploit allocator, regime context, and the exact allocator random state
captured immediately before the step allocation.

The change does not switch the scorer, change a quota, emit a continuous
Opportunity, add a Learning treatment, or touch Risk/Execution authority.

## 2. Strict reverse review findings

The first Task-5 draft was not accepted as-is. Reverse evaluation found and
corrected these defects:

1. A replay based on database row order was unstable for exactly tied scores.
   The current record stores a zero-based `candidate_index`, enforces uniqueness
   and contiguity, and reconstructs the original allocator order.
2. Batch hashing initially depended on transient record iteration order. Record
   hashes are now canonically ordered by candidate identity.
3. Rank alone was insufficient for selected/dropped comparison. The current
   implementation runs the same fine-filter, dedupe, quota, regime, and random
   exploration mechanics with database decoration and filter-evidence writes
   disabled.
4. Arbitrary feature/scanner dictionaries were unsafe to persist. The writer now
   uses source-controlled allowlists and drops unknown keys instead of serializing
   them or guessing their meaning.
5. A retry could otherwise hide replay drift. Batch and candidate identities now
   fail closed when their canonical hashes conflict.

Task 4 was also re-audited during this work. The apparent duplicate market field
seen in one truncated terminal output was not present in source; the existing
exact-instrument selection regression confirmed the code was already correct.

## 3. Data and outcome design

`aletheia-sqlite-v8` adds two append-only diagnostic tables:

- `universe_twin_shadow_batches` freezes versions, source/policy/allocation
  identities, random state, blockers, and the batch hash;
- `universe_twin_shadow_records` freezes each candidate's safe input, complete
  score evidence, three-arm ranks/selections, outcome links, and record hash.

Direction-specific outcome keys share one canonical market result across arms
that express the same direction. At most long and short are registered, each at
15m/1h/4h/24h through the existing settlement and cost-economics path. Shared
outcome rows keep `allocation_selected=NULL`; arm-specific selection remains on
the twin record.

## 4. Experimental facts remain unavailable

Task 5 records, rather than fills, the next-stage gaps:

| Identity | Task-5 value | Reason |
| --- | --- | --- |
| policy epoch | `NULL` | no prospective continuous policy epoch opened |
| availability profile | `NULL` | no frozen prospective availability cohort |
| exact per-feature observation lineage | `not available` blocker | current feature rows do not carry complete provider lineage |
| assignment arm | `NULL` | no randomized or admissible assignment |
| propensity | `NULL` | no assignment means no overlap probability |
| treatment coefficient | `NULL` | no factor treatment is authorized |
| authority applied | `false` | step scorer remains sole authority |
| promotion eligible | `false` | diagnostic evidence cannot self-promote |

This prevents retrospective return differences from being mislabeled as causal
Learning evidence or feeding a self-reinforcing score loop.

## 5. Transaction and failure proof

The authority snapshot/Opportunity publication commits first. Twin serialization
and persistence run afterward through a non-fatal background boundary. Batch,
records, and fixed-horizon outcome registration share one short transaction;
there is no network or `await` inside it. Failure returns `(0, 0)` diagnostic
counts and cannot alter the already-completed scanner run or snapshot result.

Retention uses 30 days via `universe_scanner_runs_days`, deletes child records
before parent batches, and honors the existing active-experiment clean-window
cutoff. Outcome rows remain governed by their canonical outcome retention.

## 6. Verification

Current focused evidence:

| Check | Result |
| --- | --- |
| Task-4/5, migration, and data-CLI focused suite | `52 passed` |
| Wider focused Universe regression suite | `292 passed` |
| Ruff on touched Python and tests | passed |
| Python compilation | passed |
| Semgrep Python rules on changed runtime/storage modules | passed, zero findings |
| Replay after reversed database row order | passed |
| Canonical tamper detection | passed |
| Persistence outside authority transaction AST check | passed |
| Documentation governance | passed |

These are focused macOS checks, not a large local pytest run and not Fedora
runtime evidence.

## 7. Verdict and stop gate

Task 5 meets its initial purpose: complete diagnostic selected/dropped evidence
can be retained, settled, and replayed without granting it authority. The design
is stricter than the initial draft because equal-score ordering and identity
conflicts are now explicit.

Task 6 has not started. Opening a policy epoch, assigning arms, calculating
propensities, estimating causal contribution, or allowing factor Learning still
requires a separate review and approval. This Task-5 PASS is not evidence of
positive runtime effect or profitability.
