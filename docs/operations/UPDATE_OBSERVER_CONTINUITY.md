# Observer-aware source update continuity

Issue #1135 extends source-checkout deployment with read-only Observation continuity checks around the existing mutation transactions.

The interactive mutation path remains `aletheia.update.applier.apply_update()` behind `aletheia update`. The Fedora release-tag path remains the existing `aletheia update auto run` transaction. Neither path updates the Observer checkout. Both reuse the same `build_control_status()` / `_collect_overview()` projections plus the Observation ownership and Worker lock-signature contracts.

## Worker-owned deployment boundary

When `observation.execution_owner=worker`, source deployment must prove before Main mutation that:

- the configured owner is structurally valid;
- the live Main scheduler does not own the Observation cycle;
- the shared data-root identity is readable;
- Observation overview / durable identities are readable;
- the persisted Worker source identity and pass timestamp are available for unattended auto deployment.

The interactive command may proceed with missing non-failure evidence but must finish as `unproven`; a later healthy postflight cannot erase a preflight proof gap. The unattended Fedora auto path is stricter: a Worker-owned deployment does not enter Main source/data mutation while required proof is incomplete.

## Hard deployment failures

The continuity layer fails closed when it can directly prove a structural deployment problem, including:

- invalid or changed Observation execution owner;
- duplicate Main + Worker Observation-cycle ownership;
- data-root identity loss/change;
- Worker source identity loss/change when Worker owns the cycle;
- previously readable Observation state becoming unreadable;
- pre-existing Observation identities disappearing;
- active manifest generation regressing;
- app migration head/count drifting against the latest joint checkpoint before mutation;
- the updater's authoritative target SHA not matching the post-update Main checkout/runtime;
- post-restart Main health becoming `degraded`.

A joint-checkpoint mismatch caused only by the time-dependent active-manifest set is warning-only. This matches the canonical Observation runner, which records and re-anchors such lifecycle drift instead of treating normal manifest expiry/activation as a deployment fault.

## Target-loaded post-pull boundary

The interactive updater process necessarily starts under the pre-update checkout. After `git pull`, its existing Python objects and `sys.modules` entries are therefore treated as stale for target-owned semantics.

After the source switch:

- smoke policy is imported and executed in a fresh interpreter rooted at the target checkout;
- dashboard artifact validation is imported from the target checkout in a fresh interpreter;
- `update.current_commit` is persisted by target-loaded `mutate_config`, which acquires the config writer permit and reloads the latest durable config before changing that one field;
- the pre-pull `AletheiaConfig` object is never whole-saved after the source switch.

This prevents a schema-changing target release from being followed by a stale pre-pull config serialization that overwrites target migrations or unrelated concurrent settings. If target-side commit metadata cannot be persisted, the source update remains applied, but continuity is `unproven` and no verified claim is emitted.

## Post-restart health proof

The interactive source transaction polls target-loaded postflight projections for up to 120 seconds. This covers the existing five-second health cache and the normal scheduler startup warmup (including the delayed first Market Observer pass) instead of assuming health must settle within four seconds.

A health state that is still `initializing` at the end of the bounded window remains `unproven`; it is never relabeled healthy. A directly observed `degraded` state is a structural failure.

## Fedora release-tag auto deployment

The existing `aletheia update auto run` path keeps its pinned-tag, candidate-preflight, Gateway and rollback mechanics. In Worker-owned mode it now adds the same Observer structural before/after comparison.

If target Main becomes healthy but Observer continuity fails or remains unproven, auto deployment enters a durable hold and does not invent an Observer rollback.

Main backups intentionally do not rewind the independently owned `observation-lab/lab.db`. Before an existing Main rollback restores a pre-update `app.db`, the auto path acquires a non-mutating exclusion on the same `worker.lock` used by Worker passes. While that exclusion is held, it verifies the Worker's persisted `acquired_at` still equals the preflight value and keeps the exclusion for the entire Main rollback. If a Worker pass is already active, the signature changed, or exclusivity cannot be proven, the auto path refuses the cross-time rollback and enters hold rather than creating `old app.db + newer lab.db` evidence state. The exclusion does not rewrite the Worker pass signature and is used only on an already-failed target activation.

## Warning-only Observation progress

Normal Observation business progress is not a Main deployment gate. The following may change during an update without failing it:

- collecting/pending state;
- sample counts;
- verdict availability;
- evidence arrival;
- a manifest becoming active/inactive through its declared effective interval;
- joint-checkpoint fingerprint movement caused only by normal manifest lifecycle;
- joint-checkpoint movement after activation caused by the expected Main migration or independent Worker progress.

## Diagnostic surfaces

The independent commands remain authoritative diagnostics:

```bash
uv run aletheia control status --json
uv run aletheia observe overview --json
```

The interactive source update remains:

```bash
uv run aletheia update
```

The existing unattended Fedora Paper release path remains:

```bash
uv run aletheia update auto run
```
