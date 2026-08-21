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

**Authority on mismatch:** joint-checkpoint mismatch is **diagnostic**, not a hard
evidence-admission blocker. The runner logs a warning, preserves both the prior and
current fingerprint in the pass summary, and continues executing windows. This is
the authoritative behavior; `runner.py` is the reference implementation. The
per-window invalidation escalation described in §5 is a Packet 2+ concern and is
not yet enforced.

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
4. `ok=False` — at least one input was restored to a different point than the others.
   **Diagnostic only:** the runner logs a warning and records both the prior and current
   fingerprint in the pass summary (`prior_fingerprint` + `current_fingerprint`). Windows
   continue; no hard block is applied in v1. Per-window invalidation is deferred to Packet 2+.

## 4. Why the fingerprint uses only these three inputs

- **App migration head + count** captures every schema-visible fact in `app.db`: a rollback that dropped a migration bumps the count down, and if the head changes, that's an application-level restore. A row-level rollback (data changed, schema unchanged) is invisible here — that's intentional, because production data is meant to change while schema stays put. When migrations move, the joint state must move together.
- **Active manifest hashes** captures every treatment contract Main Runtime might be executing. A restore that reverts `lab.db` to an earlier state removes some hashes; a restore that reverts `app.db` to a state where those manifests weren't yet active is caught in the same comparison.
- Row-level Lab evidence (`filter_shadow_frozen_units`, `ufc_shadow_equivalence`, `lab_exposures`, evaluations) is deliberately **not** in the fingerprint. That evidence is append-only and idempotent; a partial restore that loses recent rows is recoverable through the natural next runner pass. Fingerprinting them would raise false alarms on every quiet cycle.

## 5. Blast radius on mismatch

Once `verify_joint_checkpoint().ok is False` returns:

- The runner records both `prior_fingerprint` (the last stored fingerprint) and
  `current_fingerprint` (fresh compute) in the pass summary. Both identities are
  preserved so operator review can compare them.
- The runner does **not** block window execution or reject evidence in v1 — mismatch
  is treated as a diagnostic signal. The `runner.py` behavior (continue with warning)
  is authoritative.
- Every window whose active manifest set contains a hash NOT in the last stored
  fingerprint is **provably** running against an inconsistent view. Packet 2+ MAY
  escalate this to per-window `invalidated` disposition.
- Verdict identity is immutable: if escalation is added later, invalidation appends
  an evaluation with `disposition="invalidated"`, it never rewrites history.
- No enforcement of when to call verify. The Lab runner (`aletheia/observation_lab/runner.py`) is the natural caller and will invoke it once per pass in a follow-up; this PR ships the primitives.

## 7. Verification

- `tests/test_joint_checkpoint.py` covers: fresh install (empty ledger), matching fingerprint on repeat, mismatch on manifest set change, mismatch on migration head change, save-idempotency.
