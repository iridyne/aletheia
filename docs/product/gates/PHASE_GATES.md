# Current Admission And Promotion Gates

> **Role:** Product Truth for current admission decisions. These gates express
> durable prerequisites; they are not a roadmap, mutable checklist, or statement
> that any Runtime currently passes.
>
> Runtime facts come from Doctor, provenance, logs, and the selected data root.
> Current ownership and progress live in GitHub. Historical strategy-phase gates
> were retired when the RuleSet/DSL/backtest decision path was removed.
>
> Last aligned with code and contracts: 2026-08-06.

## Gate A - Active paper mainline eligibility

Evidence may count toward the current MVP only when all of the following are true:

1. execution mode and decision path match
   [Active Runtime Scope](../ACTIVE_RUNTIME_SCOPE.md);
2. the running code, instance/data root, runtime session, policy epoch, and
   relevant behavior/outcome contracts are identifiable;
3. Startup -> Scheduler -> Opportunity -> Decision -> Risk -> Execution ->
   Outcome is operational without an unresolved integrity incident;
4. Live remains disabled and every order path still crosses Risk approval and the
   Execution Gateway;
5. paper accounting is cost-complete and the two-table paper-ledger invariant is
   healthy;
6. unavailable, stale, synthetic, administrative, or unresolved facts are
   excluded or displayed honestly rather than converted to success/zero.

Passing a test suite does not pass this gate for a Runtime. Use
`uv run aletheia doctor` and current runtime evidence.

## Gate B - Learning shadow measurement readiness

Only a registry path with all three capabilities may create current formal
measurement evidence:

- `consumer_wired=true`;
- `settings_supported=true`;
- `measurement_supported=true`.

The current set contains exactly one path: `universe_family_calibration`. Every
other declared treatment hook returns its static fallback and must not create a
new hypothesis, experiment, exposure, policy epoch, or authority claim.

A current shadow measurement additionally requires:

| Requirement | Current software rule |
|---|---|
| Prospective cohort | Stable assignment at the persisted assignment-unit cluster |
| Complete outcomes | Canonical full-arm outcome or valid selected-member mirror/fallback; conflicts fail closed |
| Minimum sample | 50 complete assignment units in treatment and 50 in control |
| Minimum time | Seven consecutive UTC dates, each with at least one complete eligible exposure unit from either arm; both 50-unit arm gates must be met inside that run |
| Exact identity | Current hypothesis, stage, epoch, horizon, outcome contract, metric version, and behavior-contract hash |
| Harm semantics | Underpowered is `indeterminate`; determinate breach blocks authority |
| Actual intervention | Shadow normally has none and therefore yields `insufficient_data/no_actual_intervention` after ordinary sample/time gates |

A UTC date with no complete eligible unit in either arm resets the current
qualification run. Daily arm imbalance remains visible and is handled by the
independent per-arm sample gate. Planned or partial outages have no implicit waiver
when they leave a full day uncovered; pre-gap samples remain history and cannot
satisfy post-gap sample or outcome gates. The persisted coverage reason must be
`qualified` before owner admission.

## Gate C - Explicit shadow-to-paper trial admission

Shadow evidence never self-authorizes. The scheduled lifecycle controller may
recommend `admit_trial`, but only the protected authenticated owner route may
cross from `shadow` to `paper_10`.

Admission requires all of the following at the moment of mutation:

1. exact current shadow verdict is `insufficient_data` with reason
   `no_actual_intervention`;
2. verdict, experiment, hypothesis, stage, policy epoch, horizon, metric, outcome
   contract, and behavior-contract identities all match;
3. harm state is `clear`;
4. the experiment is active and the path remains measurement-supported;
5. autonomous Learning, contribution evaluation, lifecycle automation, paper
   influence, and the exact path allowlist are enabled;
6. Runtime is not paused and no global reconciliation refreeze is active;
7. owner supplies the expected epoch and reason through the protected route;
8. one transactional lifecycle event closes shadow and creates a fresh
   `paper_10` successor with a new assignment salt.

Missing, stale, malformed, or changed evidence fails closed. Read-only owner
surfaces do not expose an admission mutation.

## Gate D - Bounded paper progression

The supported progression is:

```text
shadow --explicit owner admission--> paper_10 -> paper_25 -> paper_50 -> paper_active
```

After owner admission, automated progression is allowed only one stage at a time
when the current stage has a positive, identity-matched, harm-safe measurement and
all promotion gates remain enabled. Allocation is bounded to 10%, 25%, 50%, then
the configured `paper_active_allocation_pct` (default 80%, maximum 90%) so a control
holdout remains.

The primary result remains randomized intent-to-treat: all qualified assigned
treatment units, including baseline fallbacks, contribute their realized outcome to
the treatment mean, confidence interval and harm calculation. Actual intervention is
a separate promotion gate. `authority_applied` records permission consistently across
the cluster; an actual intervention additionally requires at least one member with a
finite before/after behavior delta. The full per-arm minimum must be actual
interventions. Refused and all-no-op clusters remain visible and stay in ITT, so they
dilute the policy effect, but they do not count toward the intervention minimum.
Lifecycle rechecks these counts so a legacy or malformed positive verdict cannot grant
a successor stage.

A validly activated bounded Paper stage does not lose its existing allocation merely
because its current `insufficient_data` verdict has an `indeterminate` harm state from
sample/continuity underpower. It may continue collecting the predeclared window.
Negative/invalid evidence, determinate harm, identity/config/refreeze failure, or a
non-underpower indeterminate reason still withdraws authority immediately; promotion
continues to require positive ITT, clear-harm, and sufficient actual-intervention evidence.

| Current evidence | Required lifecycle result |
|---|---|
| `positive` and all gates current | Promote one stage |
| `neutral` or ordinary insufficient data | Hold |
| `negative` or determinate harm breach | Refreeze/fail to static baseline |
| `invalid` | Invalidate/fail to static baseline |
| stale epoch/horizon/contract identity | Cannot grant authority; safety withdrawal remains reachable |
| missing or malformed evidence | Hold/fail closed |

Any change to decision input, treatment, allocation semantics, statistical unit,
outcome economics, harm gate, or continuity rule is B-class: close the old cohort,
rotate identity/epoch as required, and collect a new window from zero.

## Gate E - Live readiness

Live is outside the active MVP proof and remains disabled by default. No score,
test, document, or merged PR enables it.

Before any owner-authorized live transition, all of the following must hold:

- the current [Go-Live Scorecard](METRICS.md#5-go-live-scorecard) satisfies the
  exact threshold and blocker rules enforced by the live contract;
- eligible net realized PnL is positive and outcome integrity is current;
- reconciliation, credentials, connectivity, Risk, kill switch, position caps,
  and confirmation requirements pass;
- the owner explicitly authorizes the exact live mode and bounded capital/risk
  profile;
- the implementation and verification record for any trust-boundary change is in
  Git/PR/CI.

See `docs/design/contracts/live_unlock_contract.md` for the executable contract.

## Retired gates

The following are not current admission criteria:

- counts of legacy strategies or strategy lifecycle transitions;
- RuleSet/DSL/backtest promotion;
- a top-level `knowledge_items.item_type='risk_lesson'` query;
- exchange-demo visibility as a requirement for the active `paper_internal` proof;
- lesson/artifact counts as evidence of positive Learning contribution;
- the June 2026 Round 1 calendar, tags, or MVP script.

Historical Round 1 material remains provenance only. It cannot authorize current
work or qualify a current Runtime.
