# Agent Guide: Development Workflow

## Tools and workspace

- Use normal filesystem, shell, and Git tools for search, reads, edits, patches,
  builds, tests, and repository inspection.
- Use Orca only when Orca owns the state: managed workspace/worktree lifecycle,
  Orca terminals, embedded browser tabs, automations, card state, or handoffs.
  When needed, Orca is mandatory; do not bypass it with raw `git worktree`,
  ad-hoc PTYs, or separate browser automation. Before the first Orca call, run
  `orca skills get orca-cli`; read [the Orca workflow](../ORCA_CLI_WORKFLOW.md)
  before mutating Orca state.
- External orchestration owns agents, panes, worktrees, task state, and handoffs.
  Do not build repository task databases, worker leases, handoff ledgers, or an
  orchestration CLI.
- Use a dedicated worktree/branch for non-trivial changes. In an explicitly
  requested shared checkout, re-check status before Git state changes, stage
  only named files, preserve unrelated edits, and never amend or use destructive
  Git commands without approval.

## Change and proof

When safe for the checkout, start with:

```sh
git pull --ff-only
uv sync
uv run aletheia doctor
```

- Inspect first; add focused failure-path coverage for safety, lifecycle, audit,
  secrets, and trading changes; implement the smallest fix; broaden tests only
  as warranted.
- Keep the Golden Path frozen unless explicitly changed. Update documented
  contracts and executable specs together. Register a data source and DU claim
  before collection code.
- Product truth belongs in design/decision/operations docs; ownership, priority,
  progress, and defects belong in GitHub Issues/PRs. Create a Proposal only for
  major, cross-domain, or trust-boundary changes. Follow
  [documentation governance](../../../governance/policies/documentation_governance.md).
- Commit only scoped files and publish through the normal PR workflow. GitHub
  Actions uses self-hosted runners only. Disable a broken workflow; do not
  archive the repository.
- The gateway is global: do not restart it casually or from competing worktrees.
- Owner updates lead with outcome, consequence, and current state.

## Common commands

For any Fedora runtime, Gateway, Provider, Risk, scheduler, or position question,
start with the read-only [Fedora Agent orientation](../../deployment/DEPLOYMENT.md#fedora-新-agent只读上手).
Do not infer that operational state from the local macOS checkout.

- Focused test: `uv run pytest tests/test_<area>.py -q`
- Full suite, never on macOS: `uv run pytest tests/ -q --tb=line`
- Gateway: `uv run aletheia gateway <start|stop|restart|status|logs>`
- Daily/release UI: `http://127.0.0.1:8787/`
- Worktree HMR: Vite `http://127.0.0.1:5173/`; read
  [development preview](../DEVELOPMENT_PREVIEW.md) before an isolated dev API.
