# Observer-aware source update continuity

Issue #1135 extends the normal source-checkout `aletheia update` command with read-only Observation continuity checks around the existing updater transaction.

The mutation path remains `aletheia.update.applier.apply_update()`. The continuity layer reuses `build_control_status()` and `_collect_overview()` plus the existing Observation ownership and Worker lock-signature contracts. It does not invoke other CLI commands and does not update the Observer checkout.

## Hard deployment failures

The continuity layer fails closed when it can directly prove a structural deployment problem, including:

- invalid or changed Observation execution owner;
- data-root identity loss/change;
- Worker source identity loss/change when Worker owns the cycle;
- previously readable Observation state becoming unreadable;
- pre-existing Observation identities disappearing;
- active manifest generation regressing;
- preflight joint-checkpoint inconsistency;
- the source updater's authoritative target SHA not matching the post-update Main checkout/runtime when restart is requested.

A postflight continuity failure does not invent a rollback. The existing updater remains the owner of rollback semantics; the command reports that the source update was applied but continuity verification failed and directs the operator to the existing diagnostic surfaces.

## Warning-only Observation progress

Normal Observation business progress is not a Main deployment gate. The following may change during an update without failing it:

- collecting/pending state;
- sample counts;
- verdict availability;
- evidence arrival;
- a manifest becoming inactive through normal lifecycle progress;
- joint-checkpoint fingerprint movement caused by a legitimate Main migration or independent Worker progress after activation.

## Diagnostic surfaces

The independent commands remain authoritative diagnostics:

```bash
uv run aletheia control status --json
uv run aletheia observe overview --json
```

The normal update path remains one command:

```bash
uv run aletheia update
```
