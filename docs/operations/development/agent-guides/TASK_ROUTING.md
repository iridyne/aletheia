# Agent Guide: Task Routing

## Classify before fixing

- **A — repair now:** crash/hang, wrong persistence target, false accounting,
  or safety-boundary bypass.
- **B — new observation window:** decision logic, guards, sizing/quota, or
  weighting changes; create a new policy epoch and fresh evidence window.
- **C — defer:** new agents/assets/pages, self-evolution, strategy features, or
  broad abstractions while stability work remains.

Classify from the change's semantic effect, not from evidence paperwork. The
presence of a PR/Issue `Runtime evidence` field, the word `qualification`, or an
existing pinned Qualification Runtime does **not** turn an A-class/D0 repair into
B-class work. An A-class repair remains a repair unless it changes approved future
behavior. If deploying that repair would disturb an existing qualification window,
handle that deployment/window consequence under the Qualification protocol; do not
create a new experiment merely to complete the source change or fill a template.

Use the full [bug triage protocol](../../../governance/protocols/BUG_TRIAGE_PROTOCOL.md)
for non-obvious cases.

## SDD depth (orthogonal to A/B/C)

The A/B/C classification guides timing inside an owner-authorized task; it does
not grant scope or select the next project priority. SDD depth (D0 / D1 / D2) is
defined once in root [AGENTS.md](../../../../AGENTS.md#human-decision-sovereignty);
permission and escalation follow
[bounded engineering delegation](../../../../AGENTS.md#bounded-engineering-delegation).
For D1/D2 or an explicitly selected high-risk D0, an optional advisory shadow
audit is available ([SDD_SHADOW_WORKFLOW.md](SDD_SHADOW_WORKFLOW.md)); it creates
no approval authority.

## Read before acting

- Trust, live, risk, execution, secrets, permissions, audit →
  [trust policy](../../../governance/policies/trust_and_change_control.md) and
  affected `tests/specs/`.
- Entry Planner, Candidate selection, Learning, observation windows →
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
  [worktree playbook](../JJ_WORKSPACE_PLAYBOOK.md).
- Orca-owned state → [Orca workflow](../ORCA_CLI_WORKFLOW.md).
- Cross-machine work → [qualification protocol](../QUALIFICATION_DEVELOPMENT_PROTOCOL.md).
- New documentation family →
  [documentation governance](../../../governance/policies/documentation_governance.md).
