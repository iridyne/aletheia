# Observation Lab Joint Backup / Restore Contract

> Status: Issue #1072 Cluster 5 addition to the Observation Lab contract.
> Referenced from `observation_lab_contract.md` §5.
> Enforced by `aletheia/observation_lab/checkpoint.py`.

New #1072 §"Storage and runtime boundary" (verbatim):

> Managed backup/restore must treat `app.db`, `lab.db` and active manifests
> as one consistent evidence checkpoint. If consistency cannot be proven
> after restore, affected observations fail closed as invalidated; they must
> not silently fall back to a different live cohort.

This contract is what "one consistent evidence checkpoint" means in code.

## 1. The three files that must agree

| File | Authority | What defines "state" |
|---|---|---|
| `~/.aletheia/app.db` | Production canonical facts | Head of `aletheia_schema_migrations` + total migration count |
| `~/.aletheia/observation-lab/lab.db` | Lab-owned Observations | Versioned Lab schema head/count, canonical Lab state digest, and set of currently effective `execution_manifests.manifest_hash` values |
| Any external "active manifest" cache (future) | Main Runtime seam post-Packet-2 | Same set as above; must not drift |

Only the first two exist today. When the third appears in Packet 2, it participates in the same fingerprint.

## 2. Fingerprint definition

```
joint_fingerprint = sha256(canonical_json({
    "app_migration_head": <migration_id of newest row in aletheia_schema_migrations>,
    "app_migration_count": <row count of aletheia_schema_migrations>,
    "lab_schema_head": <migration_id of newest row in lab_schema_migrations>,
    "lab_schema_count": <row count of lab_schema_migrations>,
    "lab_state_fingerprint": <canonical schema + row digest for Lab tables>,
    "checkpoint_ledger_fingerprint": <canonical digest of prior checkpoint rows>,
    "active_manifest_hashes": <sorted, deduplicated list of active manifest_hash>,
    "main_outbox_high_watermark": <largest durable Main receipt sequence>,
    "main_outbox_pending_count": <unacknowledged Main receipt count>,
    "main_outbox_fingerprint": <canonical digest of unacknowledged receipt identities>,
}))
```

Sorted + deduplicated so the fingerprint is order-independent. `canonical_json` uses the same shape as `hypothesis_fingerprint` (sorted keys, no whitespace) so different processes on different machines compute the same bytes for the same state.

## 3. Lifecycle

### On every scheduled Lab runner pass

1. `compute_joint_checkpoint()` — snapshot current state.
2. `save_joint_checkpoint(cp)` — append to `lab.db::joint_checkpoints`. No-op if the fingerprint is unchanged from the previous row (avoids ledger noise on quiet passes).

### On Lab startup (or the next pass after any suspected restore event)

1. `verify_joint_checkpoint()` — compare fresh compute against the latest stored fingerprint.
2. `ok=True, reason="first_checkpoint"` — the ledger is empty (fresh install). Continue and record.
3. `ok=True, reason="fingerprint_match"` — no drift. Continue.
4. `ok=False` — at least one input was restored to a different point than the others. The mismatch remains a durable diagnostic fact. Any behavior-bearing consumer must fail closed to its deterministic baseline; the v1 runner may continue observe-only collection and must not claim continuity verified or authorize a treatment from the mismatched state.

The scheduled runner and updater's read-only diagnostic call verification with
`allow_main_outbox_progress=True`. They may additionally receive
`ok=True, reason="main_outbox_append_only"` when every non-outbox input and the
previously checkpointed Main prefix still match and every later receipt remains
unacknowledged. This permits entry into a cycle-locked settlement boundary, not
deployment by itself.

### Update activation cut

The source/release activation boundary is intentionally narrower than an ordinary
Observation maintenance pass. After receipt producers stop and while
`worker_cycle_exclusion()` remains held through backup and activation, it performs
only the work required to prove one deployable cut:

1. verify the existing joint checkpoint;
2. read the Main outbox watermark/pending state and Lab cursor/retry state;
3. import the remaining contiguous delivery receipts when needed;
4. require `pending=0`, an empty retry list and a Lab cursor equal to the durable
   Main outbox watermark;
5. save the changed cut and require strict `fingerprint_match`.

Owner-approved #3096 adds one bounded applied-delivery admission to step 1.
It requires all experiment/window/result/manifest evidence to be absent, only
unclassified receipt audits, and a valid retry-free Main cursor. Replaying the
retained receipt chain must reproduce the current cursor; reconstructing the prior
cursor and removing only the suffix in memory must reproduce both the trusted full
Lab fingerprint and checkpoint ledger. Every applied suffix receipt must match
Main's immutable fields and have confirmed application/acknowledgement. App/Lab
schema and manifest identity must remain unchanged. Pending receipts must stay
identityless captures eligible for unclassified import. Missing, altered or other
evidence refuses recovery without saving a checkpoint (OBC-UPD-04).

Preflight may report this read-only proof as settlement-required, never as strict
checkpoint health. The proof is repeated after producers stop under the cycle lock;
only the existing importer and checkpoint writer complete the cut. All historical
rows are preserved. This admission adds no reset/re-anchor/fallback mechanism and
does not loosen the ordinary runner or behavior-bearing verification rules.

When the strict checkpoint already matches and the delivery frontier is already
caught up, the activation settlement is a read-only no-op: it reuses that trusted
fingerprint and does not run empty importer, audit-mirror, pruning, compaction or
checkpoint-save passes.

Lab audit mirroring, acknowledged-outbox pruning, finalized-generation compaction
and Observation-window advancement remain durable scheduled/manual maintenance.
They are not prerequisites for source activation and therefore do not extend the
update-critical section. Deferring them loses no evidence: their source rows remain
durable and are included in the authoritative recovery boundary.

The authoritative backup still occurs before the source switch and under the same
Observation cycle exclusion. Backup, restore, authority cutover/rollback, ordinary
CLI verification and behavior-bearing consumers retain strict verification by
default.

## 4. Why the fingerprint uses these inputs

- **App migration head + count** captures every schema-visible fact in `app.db`: a rollback that dropped a migration bumps the count down, and if the head changes, that's an application-level restore. A row-level rollback (data changed, schema unchanged) is invisible here — that's intentional, because production data is meant to change while schema stays put. When migrations move, the joint state must move together.
- **Lab schema/state** captures the physical Lab contract and append-only evidence identities, including rows that a Lab-only restore could lose or replace. The runner records the post-window state, so expected append-only writes become the next verified checkpoint rather than perpetual false drift.
- **Active manifest hashes** captures every treatment contract Main Runtime might be executing. Each loaded manifest is re-hashed from its canonical content before it can enter this set; payload-only mutation or a forged stored hash is an integrity failure.
- **Checkpoint ledger** captures prior checkpoint identity without hashing the just-created current row, avoiding a self-referential fingerprint while still detecting removal or replacement of historical checkpoint rows.
- **Main receipt cut** binds the logical high watermark and pending transport frontier. Acknowledged rows covered by the Lab cursor/evidence are a bounded retention cache, so pruning them does not change the fingerprint. Pending receipt loss or identity drift, cursor rollback, and an acknowledged suffix without matching Lab progress remain mismatches.

## 5. Blast radius on mismatch

Once `verify_joint_checkpoint().ok is False` returns:

- Every window whose active manifest set contains a hash NOT in the last stored fingerprint is **provably** running against an inconsistent view — mark those windows `invalidated`.
- Every window whose active manifests are **all** in the stored fingerprint but the app.db head diverged is **suspect**: the fingerprint alone can't tell if the row-level facts those manifests depend on are still consistent. Owner review §7 rule applies: fail closed rather than pool across a discontinuity.
- Verdict identity is immutable: invalidation appends an evaluation with `disposition="invalidated"`, it never rewrites history.

## 6. What this contract does NOT do

- No generic automatic recovery, reset, or fallback. Only the explicitly proven applied-delivery suffix above may enter update settlement; all other mismatches require separate owner resolution and must not create a replacement baseline.
- No incremental/Merkle checkpoint ledger. Checkpoint computation remains the current canonical full-state proof; performance work may remove redundant computations but may not replace strict evidence with an unverified cache.
- No permission to skip, delete or force-acknowledge a blocking receipt. The contiguous importer remains fail-closed.

## 7. Verification

- `tests/test_joint_checkpoint.py` covers: fresh install (empty ledger), matching fingerprint on repeat, runner-only pending Main progress, rejection of acknowledged suffixes and changed prefixes, mismatch on manifest set change, mismatch on app or Lab-only state change, schema identity, manifest immutability, and save-idempotency.
- `tests/test_update_settlement_hot_path.py` covers: lazy preflight verification, the caught-up no-op path, delivery-only update settlement, retry refusal, cycle-lock lifetime and the separated stop/settle/backup progress boundary.