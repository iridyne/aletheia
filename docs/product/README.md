
# product/ - Product Intent

Use this directory to answer what Aletheia is for, what is in scope, and how
success is evaluated. These questions have separate canonical owners:

- [DIRECTION.md](definition/DIRECTION.md) alone defines the product and its
  long-term direction.
- [SCOPE.md](SCOPE.md) records inclusions, deferrals, and exclusions.
- `gates/` defines metric meaning and current admission. The current
  `experiments/EXPERIMENT_PROTOCOL.md` defines observation-window governance;
  the other experiment files are explicit historical compatibility pointers.

## Subfolders

| Folder | Use |
|---|---|
| `definition/` | The single canonical product direction definition |
| `gates/` | Metrics and phase gate criteria |
| `experiments/` | Current experiment protocol plus clearly retired Round 1 compatibility pointers |
| `documentation/` | Frozen history of the previous documentation migration |
| `capability-integrity/` | Evidence-first inventory, fact cards, and issue-ready product capability truth |

The canonical implementation-status summary is
[CAPABILITY_GROUND_TRUTH.md](CAPABILITY_GROUND_TRUTH.md).
Current inclusions, deferrals, and exclusions are maintained separately in
[SCOPE.md](SCOPE.md).

## Agent Rule

Only `definition/DIRECTION.md` defines product intent. Scope, gates, and
experiments may operationalize that direction but must not create a second
vision, roadmap, or north star. If implementation differs, do not silently
rewrite product intent; verify the mismatch, use GitHub Issues for tracking,
and update the appropriate Product Truth or Contract through the normal review
path.
