
# product/ - Product Intent

Use this directory to distinguish long-term intent, approved active scope and
proof of implementation. Each question has an existing canonical owner:

- [DIRECTION.md](definition/DIRECTION.md) defines long-term product intent.
- [ACTIVE_RUNTIME_SCOPE.md](ACTIVE_RUNTIME_SCOPE.md) is the sole machine-readable
  active-admission declaration. Read it first for the current trading route.
- [CAPABILITY_GROUND_TRUTH.md](CAPABILITY_GROUND_TRUTH.md) owns current capability
  promises, support boundaries and evidence limits; it is not a live status page.
- [SCOPE.md](SCOPE.md) supplies broader inclusion/deferral context, without
  expanding the active declaration or overriding capability limits.
- `gates/` defines metric meaning and applicable admission criteria, not new
  support. [EXPERIMENT_PROTOCOL.md](experiments/EXPERIMENT_PROTOCOL.md) defines
  observation-window governance; the other experiment files are explicit
  historical compatibility pointers.

## Subfolders

| Folder | Use |
|---|---|
| `definition/` | The single canonical product direction definition |
| `gates/` | Metrics and phase gate criteria |
| `experiments/` | Current experiment protocol plus clearly retired Round 1 compatibility pointers |
| `capability-integrity/` | Evidence inventories and fact cards; retain their original scope and verify before using as current evidence |

## Agent Rule

Follow the [authority map](../governance/policies/documentation_governance.md#authority-and-conflict-resolution)
and [bounded development delegation](../../AGENTS.md#bounded-engineering-delegation).
Do not create another product manifest, support table owner, roadmap, or session
status document. Optional, frozen, accepted and implemented are different claims;
none alone proves a healthy deployed runtime or a qualified economic outcome.

Only `definition/DIRECTION.md` defines long-term intent. If implementation differs
from approved behavior, establish the mismatch and record it in GitHub; do not
silently rewrite intent or weaken a Contract to match the implementation.
Active-scope machine fields must not change as a side effect of documentation
cleanup. Historical account identities, recovery obligations and explicitly
owner-gated Learning semantics remain governed by their existing Contracts.
