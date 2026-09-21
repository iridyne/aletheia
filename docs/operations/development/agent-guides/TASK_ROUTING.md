# Agent Guide: Task Routing

## Classify before fixing

## SDD depth (orthogonal to A/B/C)

The A/B/C classification guides timing inside an owner-authorized task; it does
not grant scope or select the next project priority. SDD depth classifies the
semantic nature of the change. They are independent.

- **D0** — restore truth / conformance repair; Contract Delta = `NONE`
- **D1** — change semantics; requires explicit `ADDED / MODIFIED / REMOVED`
  semantic delta and owner acceptance
- **D2** — change structure; requires accepted Proposal before target state
  enters Contract

SDD depth is classification only. Follow the single
[bounded engineering delegation](../../../../AGENTS.md#bounded-engineering-delegation)
for permission and escalation. A new, unapproved D1/D2 requires owner acceptance;
implementing an already accepted delta does not require another approval per
commit, technical root cause, test plan or checkpoint. The AI derives those
engineering details within the authorized outcome and existing Contracts.
Resolvable technical uncertainty calls for investigation, not an automatic
handoff; unresolved product semantics or unsafe verification do require escalation.

For D1/D2 or an explicitly selected high-risk D0, an optional advisory shadow
audit is available; read [SDD_SHADOW_WORKFLOW.md](SDD_SHADOW_WORKFLOW.md) before
using it. It does not create another approval authority.

See [documentation governance](../../../governance/policies/documentation_governance.md)
for the full rule.


- **A — repair now:** crash/hang, wrong persistence target, false accounting,
  or safety-boundary bypass.
- **B — new observation window:** decision logic, guards, sizing/quota, or
  weighting changes; create a new policy epoch and fresh evidence window.
- **C — defer:** new agents/assets/pages, self-evolution, strategy features, or
  broad abstractions while stability work remains.

Use the full [bug triage protocol](../../../governance/protocols/BUG_TRIAGE_PROTOCOL.md)
for non-obvious cases.

## Read before acting

- Trust, live, risk, execution, secrets, permissions, audit →
  [trust policy](../../../governance/policies/trust_and_change_control.md) and
  affected `tests/specs/`.
- Agent decisions, strategy, learning, paper trader, observer →
  [experiment protocol](../../../product/experiments/EXPERIMENT_PROTOCOL.md).
- New API/table/source/job/gate/tool →
  [implementation guide](../../../governance/policies/implementation_guide.md),
  then the matching API, data, utilization, or agent contract.
- System ownership or unfamiliar subsystem →
  [system map](../../../design/architecture/SYSTEM_MAP.md) and
  [agent contracts](../../../design/contracts/agent_contracts.md).
- Async route, polling/config UI, endpoint latency →
  [async performance](../ASYNC_PERFORMANCE.md).
- Backend bug or dishonest UI → triage protocol and a current/new GitHub Issue.
- Dirty checkout, merge, worktree, or frontend conflict →
  [worktree playbook](../GIT_WORKTREE_PLAYBOOK.md).
- Orca-owned state → [Orca workflow](../ORCA_CLI_WORKFLOW.md).
- Cross-machine work → [qualification protocol](../QUALIFICATION_DEVELOPMENT_PROTOCOL.md).
- New documentation family →
  [documentation governance](../../../governance/policies/documentation_governance.md).
