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
| `~/.aletheia/observation-lab/lab.db` | Lab-owned Observations | Set of currently effective `execution_manifests.manifest_hash` values |
| Any external "active manifest" cache (future) | Main Runtime seam post-Packet-2 | Same set as above; must not drift |

Only the first two exist today. When the third appears in Packet 2, it participates in the same fingerprint.

## 2. Fingerprint definition

```
joint_fingerprint = sha256(canonical_json({
    "app_migration_head": <migration_id of newest row in aletheia_schema_migrations>,
    "app_migration_count": <row count of aletheia_schema_migrations>,
    "active_manifest_hashes": <sorted, deduplicated list of active manifest_hash>,
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
4. `ok=False` — at least one input was restored to a different point than the others. **Affected Observations must fail closed as `invalidated`** with `invalidation_reason="joint_checkpoint_mismatch:<detail>"`. Do not fall back to any current live cohort.

## 4. Why the fingerprint uses only these three inputs

- **App migration head + count** captures every schema-visible fact in `app.db`: a rollback that dropped a migration bumps the count down, and if the head changes, that's an application-level restore. A row-level rollback (data changed, schema unchanged) is invisible here — that's intentional, because production data is meant to change while schema stays put. When migrations move, the joint state must move together.
- **Active manifest hashes** captures every treatment contract Main Runtime might be executing. A restore that reverts `lab.db` to an earlier state removes some hashes; a restore that reverts `app.db` to a state where those manifests weren't yet active is caught in the same comparison.
- Row-level Lab evidence (`filter_shadow_frozen_units`, `ufc_shadow_equivalence`, `lab_exposures`, evaluations) is deliberately **not** in the fingerprint. That evidence is append-only and idempotent; a partial restore that loses recent rows is recoverable through the natural next runner pass. Fingerprinting them would raise false alarms on every quiet cycle.

## 5. Blast radius on mismatch

Once `verify_joint_checkpoint().ok is False` returns:

- Every window whose active manifest set contains a hash NOT in the last stored fingerprint is **provably** running against an inconsistent view — mark those windows `invalidated`.
- Every window whose active manifests are **all** in the stored fingerprint but the app.db head diverged is **suspect**: the fingerprint alone can't tell if the row-level facts those manifests depend on are still consistent. Owner review §7 rule applies: fail closed rather than pool across a discontinuity.
- Verdict identity is immutable: invalidation appends an evaluation with `disposition="invalidated"`, it never rewrites history.

## 6. What this contract does NOT do

- No coordinated backup script. Backup tooling is out of Observation Lab scope; the contract only says "if the resulting state is inconsistent, the Lab must not silently proceed". A future ops task can add a `aletheia observe backup` command that snapshots all three inputs together with the fingerprint recorded.
- No automatic recovery. On a mismatch the operator resolves it manually — either by rolling forward the lagging file or by declaring the mismatch acceptable (a deliberate ledger reset) and starting a new checkpoint chain. The Lab cannot know which is correct.
- No enforcement of when to call verify. The Lab runner (`aletheia/observation_lab/runner.py`) is the natural caller and will invoke it once per pass in a follow-up; this PR ships the primitives.

## 7. Verification

- `tests/test_joint_checkpoint.py` covers: fresh install (empty ledger), matching fingerprint on repeat, mismatch on manifest set change, mismatch on migration head change, save-idempotency.
