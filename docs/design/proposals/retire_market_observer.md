---
id: proposal-retire-market-observer
status: accepted
github_issue: 2933
related_contracts:
  - docs/design/contracts/decision_lifecycle_contract.md
  - docs/design/contracts/knowledge_contract.md
supersedes: []
---

# Retire the Market Observer pipeline

## Summary

Delete the `market_observer` agent, its scheduler job, its agent registration, and the
diagnostic surfaces that report its liveness. Its admitted workset has no producer, so the
agent runs to completion without doing anything, and the surfaces that report it describe a
retired mechanism as live runtime state.

This is a structural removal (D2): an agent leaves the runtime topology. This document
records the reasoning and the acceptance signal; since 2026-09-26 a Proposal is no longer
required as a precondition for any change depth ([decision](../../governance/decisions/DECISIONS.md#2026-09-26-—-提案文档不再是任何改动的前置条件测试是权威载体)).
The Proposal exists because the reasoning would not fit in a PR body, not because writing
one buys permission to change structure.

## Current fact

`extensions/research/market_observer.py` (2,358 lines) is registered as agent `market_observer`
(`core/aletheia/main.py:143-151`), scheduled every 10 minutes with a 500-second timeout budget
(`core/aletheia/agent_runtime/scheduler.py:202`, `:1320-1329`, `:2252-2278`), and its docstring
describes it as *"Enrich deterministically admitted Universe Opportunities"*.

`_admitted_observer_workset` (`extensions/research/market_observer.py:126`) requires, per current
universe member, an **already existing** `opportunities` row that is `open`, whose
`universe_member_id` matches, and that passes
`aletheia.analytics.filter_evidence.verify_signal_emitter_admission` — which requires
`source_agent == "universe_scanner"` plus a persisted `signal_emitter` pass fact.

Both preconditions lost their only producer when `core/aletheia/universe/signal_emitter.py` was
deleted under #2945. `repo.insert_opportunity_once_by_snapshot` and
`repo.insert_opportunity_generation` now have zero production callers and are AST-banned from
every module except `repositories.py` by
`tests/specs/spec_pipeline_middle_state_integrity.py:983-1020`. Nothing writes
`source_agent='universe_scanner'`.

The live entry mainline does not read this table. `docs/product/ACTIVE_RUNTIME_SCOPE.md` names
`aletheia.agent_shell.entry_planner.system_operator_entry_planner` as the decision authority, and
`plan_next_candidate` consumes `candidate_planning_generations` + `universe_members` to produce
an Entry Plan.

Measured on the Fedora paper runtime (deployed `98e42e03f`, read-only, 2026-09-25):
`market_observer` completed **165 runs in 24 h**, each in **~63 ms**, producing no output.

Issue #2990 documents the starvation; #2933 and #2934 own the retirement and carry
`do-not-auto-merge`.

## Goals and non-goals

Goals:

- Remove the scheduled no-op and its scheduler slot, timeout budget and agent registration.
- Delete the agent implementation rather than leave it unreachable.
- Stop `doctor`, `health` and the operator recommendations from reporting a retired mechanism as
  live runtime state.

Non-goals:

- Does not touch the canonical Candidate → Planning Cycle → Entry Plan → Proposal path.
- Does not remove `equity_observer`, `prediction_observer`, `divergence_scanner`, or any other
  producer that still writes `opportunities` rows.
- Does not delete historical `opportunities` rows, and does not change any reader of them.
- Does not authorise re-creating a deterministic Universe opportunity emitter.

## Proposed design

**Removed** (the mechanism):

| Surface | Location |
|---|---|
| Scheduler job registration, runner, timeout entry | `agent_runtime/scheduler.py:202`, `:1320-1329`, `:2252-2278` |
| Agent registration + import guard | `main.py:143-151` |
| Agent implementation | `extensions/research/market_observer.py` |
| Liveness probes | `agent_runtime/health.py:247-317`; `diagnostics/doctor.py:1722-1728` |
| Operator surfaces | `agent_shell/commands.py` (manual market scan, cadence text); `agent_shell/operator_recommendations.py:1377-1431`; `cli/control_cmd.py:224-234` |
| Agent-specific tests | `tests/observer_admission_fixture.py` and the `market_observer`-only test modules |

**Preserved** (historical evidence and attribution — these read rows and labels that already
exist and must not change):

- `COALESCE(signal_source,'market_observer')` defaults in `analytics/skip_diagnosis.py` and
  `diagnostics/doctor.py`.
- The `in_scope` producer set in `analytics/filter_evidence.py:250-252`.
- `analytics/thesis_lineage.py`, `analytics/alpha_attribution.py`, `analytics/candidate_facts.py`,
  `agent_shell/reflection.py`.
- `economics/agent_roles.py` worker-name set and `model_provider/broker.py` priority mapping.
- `portfolio/market_signal_reader.py` and every other reader of stored `opportunities` rows.

**Failure semantics**: after removal, the removed surfaces must be absent rather than degraded —
a probe that still emits `loop.*` or `health.*` about `market_observer` would keep reporting a
mechanism that no longer exists. `tests/specs/spec_pipeline_middle_state_integrity.py` already
locks the emitter side of this; the retirement must add the agent side.

## Trust and complexity review

The only trust-boundary question is whether anything authoritative depends on this agent.
Evidence says no: it cannot write an `opportunities` row (its reuse path calls
`update_open_opportunity` on rows it did not create, and its workset is empty), it holds no
credential, it cannot reach the Gateway, and the entry authority does not read its output. What
it does hold is a scheduled slot and an LLM priority class
(`model_provider/broker.py:862`), which is runtime cost without runtime effect.

The change is large in *breadth* (523 references across ~110 files) and small in *depth*: all but
the removal set are historical reads, labels and tests, and are explicitly preserved above.

A smaller change is insufficient. Disabling only the scheduler job leaves a 2,358-line module
that looks live, keeps the diagnostics lying, and invites a future session to "fix" it by
restoring the producer — the exact dual-authority outcome #2934 exists to prevent. AGENTS.md's
convergence rule is explicit: converge and delete the loser, do not leave a shim.

## Verification contract

Before the behaviour can be considered current:

1. `market_observer` is absent from the scheduler's registered jobs and from the agent registry;
   a test asserts that scheduling does not raise and that no `market_observer` job exists.
2. `doctor` and `/api/health` no longer emit any `market_observer`-keyed check or reason code;
   a test asserts the key set.
3. `import extensions.research.market_observer` fails, and no module under `core/aletheia` or
   `extensions/` imports it.
4. Historical readers are unchanged: `thesis_lineage`, `alpha_attribution`, `skip_diagnosis` and
   `filter_evidence` tests pass without edit.
5. Fedora, after deploying the merged commit: `agent_runs` gains no new `market_observer` row
   over one full 10-minute cycle, and `doctor` shows no `market_observer` surface.

## Rollout and rollback

Rollout: one PR, reviewable, with the four verification items above; merge and deploy through the
normal Fedora `update` path. No migration, no data change, no flag.

Rollback: `git revert` of the single PR restores the agent and its schedule. Because the agent
has no effect on trading state, reverting has no economic consequence — which is also why
removal is safe.

## Resolution

Accepted 2026-09-25 by the owner: the switch to Candidate → Planning Cycle → Entry
Plan was already agreed, so the superseded pipeline is removed outright rather than left
disabled. Normative statement moves to `docs/product/ACTIVE_RUNTIME_SCOPE.md`; execution in
#2933 / #2934. On acceptance, the normative statement moves to
`docs/product/ACTIVE_RUNTIME_SCOPE.md` (the retired mechanism leaves the active topology) and the
Proposal remains as decision history. Execution stays in #2933 / #2934.
