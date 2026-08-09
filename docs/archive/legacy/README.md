# Legacy documentation collections

These files preserve dated evidence and decision provenance. They are not current
instructions, implementation truth, task tracking, or product support claims.

| Collection | Historical content | Current authority |
|---|---|---|
| `status/` | Roadmaps, progress snapshots, and completion audits | Product direction and gates under `docs/product/`; current behavior in Product Ground Truth, code, tests, and runtime evidence |
| `plans/` | Superseded implementation plans | Accepted contracts and proposals under `docs/design/`; current work in GitHub Issues and pull requests |
| `issues/` | Local defect and backlog records | GitHub Issues |
| `ai-context/` | Agent handoffs, audits, discussions, decisions, development logs, and superseded workflow context | Repository governance, current contracts, operations documentation, and the active external orchestrator |

Read an archived file only when its dated evidence or provenance is needed. If an
archived claim conflicts with a current authority or current runtime fact, the
current authority and runtime fact win.

Relative links inside archived files may be stale: collections were relocated
under `archive/legacy/` and some referenced documents were since deleted or
renamed. Treat a broken or misleading link in an archived file as historical
noise, not as a pointer to current truth; locate the current authority via the
table above instead.

The plan collection was migrated only after every file was read individually.
Durable principles were either already present in current contracts or rewritten
there before migration. Plan completion markers, dated runtime counts, fixed
thresholds, file-line references, and proposed architectures remain historical
evidence and are not product capability claims.

The AI-context collection was migrated with the same file-by-file review. Reusable
protocols now live under `docs/governance/` or `docs/operations/`; durable architecture
decisions live under `docs/governance/decisions/`; product audits live under
`docs/product/` or `docs/research/`. The archived `logs/DEVLOG.md` and
`session/AI_HANDOFF.md` remain searchable engineering chronologies, not current
runtime, capability, task, or support evidence.
