# Capability Integrity Issue Candidates

These are the frozen fact-card drafts that produced the live GitHub tracking
items. GitHub Issues, not this file, carry current ownership, priority, progress,
and closure. Each draft remains as review evidence and does not authorize
implementation by itself.

| Candidate | Live tracker |
|---|---|
| CI-01 | [#419](https://github.com/iridite/aletheia/issues/419) |
| CI-02 | [#418](https://github.com/iridite/aletheia/issues/418) |
| CI-03 | [#417](https://github.com/iridite/aletheia/issues/417) |
| CI-04 | [#409](https://github.com/iridite/aletheia/issues/409), [#410](https://github.com/iridite/aletheia/issues/410) |
| CI-05 | [#386](https://github.com/iridite/aletheia/issues/386) |
| CI-06 | Covered by persona scope in [#410](https://github.com/iridite/aletheia/issues/410); no separate architecture issue |
| CI-07 | [#426](https://github.com/iridite/aletheia/issues/426) |
| CI-08 | [#428](https://github.com/iridite/aletheia/issues/428) |
| CI-09 | [#429](https://github.com/iridite/aletheia/issues/429) |
| CI-10 | [#430](https://github.com/iridite/aletheia/issues/430) |

## CI-01 — Capital events do not adjust displayed performance

**Capability:** PF-03 Capital-adjusted performance curve
**Status:** Implemented / adjusted consumer added

**Product promise**

Settings says deposits and withdrawals are recorded so cumulative PnL reflects
true investment performance.

**Actual path**

```text
Settings Capital Events card
→ POST /api/capital/events
→ capital_events
→ account-equity snapshot window
→ ordered scoped cash-flow join
→ raw points + adjusted points + event markers
→ Overview renders distinct raw and adjusted series
```

The public account-equity-history route now consumes ordered Capital Events
inside the visible snapshot window. Its response includes the baseline,
aggregate scope, adjustment method, signed markers, raw points, and adjusted
points. Verified position PnL remains a separate unchanged series.

**User impact**

An external deposit or withdrawal is visible both as a durable audited fact and
as a separate adjustment to raw account equity.

**Target state**

Complete. Raw account equity, cash-flow-adjusted account equity, and verified
trading PnL are separately labelled and returned.

**Minimum repair**

Completed without changing portfolio accounting or historical trade facts.

**Acceptance path**

1. Seed a deposit, a trade outcome, and a withdrawal with ordered timestamps.
2. Call the public curve endpoint used by the Dashboard.
3. Assert raw equity changes with cash flows while investment PnL changes only
   with trading outcomes.
4. Assert the UI labels match the returned semantics.

**Failure/rollback**

Feature-flag the adjusted projection or revert the consumer wiring; retain
capital events and fall back to an explicitly labeled raw-equity curve.

---

## CI-02 — Live Unlock does not select live mode for new proposals

**Capability:** EX-04 New-proposal live execution mode
**Status:** Broken / Trigger-missing

**Original product promise**

The Settings warning wall and `live_unlock_contract.md §4b` presented Live
Unlock as enabling autonomous limited live execution after scorecard and
human-confirmation gates.

**Current honest boundary**

Settings and the contract now label Live Unlock as an experimental authority
prerequisite. They explicitly state that new autonomous Proposals continue to
use the selected paper/demo execution mode. The missing mode-selection join
remains a real product defect, tracked by GitHub #418, rather than being hidden
by the UI downgrade.

**Actual path**

```text
POST /api/settings/live-unlock
→ scorecard and confirmation checks
→ cfg.live.state = live_limited
→ operator.permission_mode = Auto
→ audit event

scheduled opportunity
→ paper ranker
→ System Operator decision
→ `_configured_paper_execution_mode()`
→ `cfg.exchange.paper_execution_mode`
→ TradeProposal
```

Live broker branches exist in the Execution Gateway, but the scheduled
new-proposal path does not consume `cfg.live.state` when selecting
`TradeProposal.execution_mode`. The independent Exchange mode setting writes
`cfg.exchange.mode`, which the proposal selector also does not consume. In
addition, the exchange-markets route accepts `binance_live` and `okx_live`,
while Risk, credential lookup, migrations, and the Gateway recognize
`binance_live_limited` and `okx_live_limited`. Unlocking authority therefore
does not make a new proposal enter a live-limited broker.

Focused evidence explicitly asserts that the autonomous loop never produces a
live order and that the paper proposal wrapper rejects a live execution mode.

**User impact**

The UI can report an unlocked state while automatic new proposals continue in
paper/demo mode. This is a false capability signal around the highest-risk
product action.

**Target state**

Choose one explicitly documented behavior:

- wire a human-selected live venue/mode through proposal creation with all
  existing gates; or
- relabel Live Unlock as an authority-state prerequisite and state that it does
  not select execution mode.

**Minimum repair**

Do not add a new execution architecture. Add one explicit mode-selection
boundary, or make the UI/contract honest if automatic live routing is not
intended.

**Acceptance path**

1. With live locked, create an otherwise eligible scheduled candidate and
   assert no live proposal is produced.
2. Unlock with a passing scorecard and selected live venue.
3. Assert the next new proposal carries the exact live-limited mode and reaches
   the existing gateway preflight without bypassing Risk.
4. Assert one canonical mode vocabulary is used by Settings, credential lookup,
   Risk, migrations, and Gateway.
5. Relock and assert subsequent proposals cannot select live mode.

**Failure/rollback**

Relock, disable automatic live selection, and return to the current
`paper_internal` default. Existing broker adapters remain unchanged.

---

## CI-03 — Market-aware exits promise automatic action but only wake Operator

**Capability:** EX-09 Market-aware position management
**Status:** Broken / wake producer has no position-decision consumer

**Original product promise**

Settings says the Position Manager may force-close losing positions, tighten
stops, or exit when confidence drops.

**Current honest boundary**

Settings now calls this `Market-Aware Position Review` and states that adverse
signals wake the System Operator with suggestions. It explicitly says the
evaluator does not close positions or mutate stops before a separate authorized
decision. Successful daemon delivery is now distinguished from failure:
delivery failure records `wake_failed:*` on the Position and returns
`operator_wake_unavailable` instead of reporting a false success.

**Actual path**

```text
market_aware_exits enabled
→ Position Manager reads current market signals
→ exit_rules evaluates urgent_exit / tighten_stop / confidence collapse
→ daemon accepts event: wake:urgent_exit or wake:tighten_stop
→ entry-opportunity evaluator runs
→ with no pending entry candidate, returns before attaching wake context
→ otherwise wake changes the decision fingerprint but is not rendered into the prompt
→ active decision schema accepts only trade / skip
  OR daemon unavailable: wake_failed + explicit cycle error
→ no wake-driven hold / adjust / close result
```

`aletheia/portfolio/position_manager.py` explicitly preserves Operator
authority by waking it instead of auto-closing or tightening. The focused tests
assert this exact behavior and the failed-delivery path.

**User impact**

The earlier configuration surface promised automatic protection that was not
performed. The copy mismatch is repaired, but the narrower review promise is
still not implemented end to end: the adverse-position evidence cannot produce
an Operator position decision. Process-local delivery is a secondary liveness
limitation, not the primary broken join.

**Target state**

Keep the current authority model. A successful wake must place the exact
position and adverse evidence in the existing Operator decision input and end
in one explicit hold, adjust, or close result without granting the evaluator
direct mutation authority.

**Minimum repair**

Reuse the existing daemon, Operator decision record, position adjustment, and
proactive-close paths. Add the smallest typed position-review branch that
consumes the wake and records its final result. Do not add autonomous evaluator
authority, a second Operator, or a new queue framework. Durable delivery remains
a later decision only if runtime evidence justifies it.

**Acceptance path**

1. Enable the setting and inject an urgent adverse signal for an open position.
2. Assert a running daemon accepts the urgent wake and the Operator receives
   the exact position id, suggested action, and adverse evidence.
3. Assert the Operator records one hold, adjust, or close result causally linked
   to that wake; adjustment/close must use the existing authorized paths.
4. Assert no position/order is mutated before that Operator decision.
5. Stop the daemon and assert the next attempt records `wake_failed` and an
   explicit error rather than a successful wake.

**Failure/rollback**

Revert copy/visibility changes; the current wake-only runtime behavior remains
unchanged.

---

## CI-04 — Persona unlock reuses historical outcomes outside the current rollout

**Capabilities:** LN-16 progressive unlock; LN-18 Persona rollout automation
**Status:** Experimental / safety evidence gap / GitHub #409

**Product promise**

Persona parameters unlock progressively as clean evidence accrues, with
distress refreezing authority.

**Actual path**

```text
all lifetime eligible strategy outcomes
→ clean_close_count
→ consumer-filtered unlock ladder
→ effective notional/Kelly
→ entry sizing and Proposal quantity
```

The consumer mismatch is repaired locally: persisted/custom ladders are
filtered to `notional_usdt` and `kelly_fraction`, the two active entry
consumers. The remaining gate does not scope evidence to the current Persona,
policy epoch, rollout start, or prior unlock step. An established database can
therefore unlock both active parameters immediately after the feature is
enabled.

The scheduled rollout previously compounded this gap by treating each
eight-hour invocation as an observation window and automatically enabling the
next flag. It is now a read-only safety hold and cannot mutate Persona config.

**User impact**

Historical trades generated under another policy can grant new sizing
authority without evaluating the current Persona.

**Target state**

If this experiment becomes worth supporting, each unlock must use prospective,
attributable evidence for the current Persona and policy step.

**Minimum repair**

Keep the mechanism default-off and honestly Experimental now. Do not build a
new rollout subsystem until progressive Persona authority is deliberately
chosen for the supported product surface. Reuse the existing experiment and
contribution evidence model if that decision is made.

**Acceptance path**

On a database with enough old outcomes to satisfy every step, starting a new
Persona rollout unlocks nothing. Only new, attributable outcomes for the exact
Persona/policy/step can authorize one next parameter, and distress refreezes
it across restart.

**Failure/rollback**

Leave progressive unlock disabled; base Persona sizing and legacy fallback
remain available independently.

---

## CI-05 — Exchange closes lack private funding/bill evidence

**Capability:** EX-16 Exchange-backed cost-complete close settlement
**Status:** Partial / Producer-missing outside local paper

**Product promise**

Closed outcomes and analytics are cost-complete: fees, slippage, funding, and
realized PnL have attributable economic evidence.

**Actual path**

```text
paper_internal position
→ exact entry/exit fills + scheduled funding observations
→ local funding computation
→ verified cost-complete outcome

exchange-backed position
→ exchange close/reconciliation
→ no private funding/bill production writer found
→ incomplete_funding_economics
→ excluded or explicitly incomplete outcome
```

The fail-closed outcome behavior is correct, but the public exchange-backed
capability cannot normally reach a cost-complete result.

**User impact**

Exchange demo/live trades may close operationally yet remain ineligible for
clean PnL, attribution, and Learning cohorts.

**Target state**

The selected supported exchange mode has a private, venue-attributed
funding/bill ingestion path tied to position identity and settlement windows.
Other venues remain explicitly unsupported or incomplete.

**Minimum repair**

Implement one selected venue/mode writer and its reconciliation seam. Do not
generalize all exchanges in the same issue.

**Acceptance path**

1. Materialize an exchange-backed position spanning a funding event.
2. Ingest the exact private account bill.
3. Close and reconcile the position.
4. Assert the verified outcome includes the bill identity and correct signed
   funding cost.
5. Remove or mismatch the bill and assert fail-closed incomplete economics.

**Failure/rollback**

Disable that venue's cost-complete eligibility and retain the current explicit
incomplete classification; never synthesize a zero funding cost.

---

## CI-06 — Pyramid/scale decision has no production materialization path

**Capabilities:** LN-17 Persona pyramid decision, EX-15 partial reduce/scale-in
**Status:** Experimental / production mutation intentionally blocked

**Product promise**

Persona configuration and Operator actions expose bounded adds to winning
positions and scale behavior without duplicating position identity.

**Actual path**

```text
persona_pyramid_enabled + winning position
→ sizing_policy.pyramid_plan
→ bounded {add, add_notional, reason}
→ no production caller
→ no risk-approved add proposal/order/fill materialization

reduce_position_routed + strict partial quantity
→ fail closed before every broker call
→ isolated OKX mock-tested helper is not production-authorized
```

The pyramid helper is tested as pure decision logic. The partial-reduce broker
helper is also tested in isolation, but has no attributable Proposal,
RiskDecision, confirmed-fill contract, or TP/SL bracket resync. Neither test
demonstrates a scheduler/Operator trigger or a durable position mutation through
the authorized Risk and Execution path.

**User impact**

Before the boundary repair, the setting could be enabled even though no actual
position lifecycle result followed, and the partial-reduce dispatcher could
reach the isolated OKX helper. Settings/API now block new pyramid enablement,
legacy configurations can switch off, and all strict partial reductions fail
closed before a broker call.

**Target state**

The feature is explicitly Experimental. Reference helpers remain testable, but
Settings/API and the active dispatcher refuse production mutation.

**Minimum repair**

The honest product-surface and routing repair is complete. Any later
implementation must reuse the existing proposal/Risk/gateway chain and preserve
the original position identity and attribution; do not add a parallel
scale-in/reduce executor.

**Acceptance path**

1. Create a qualifying winning position.
2. Produce an attributable Operator scale decision.
3. Assert a separate risk-approved add order and fill.
4. Assert quantity/cost basis update without a duplicate position.
5. Assert max-add and exposure caps refuse the next disallowed add.

**Failure/rollback**

Turn off `persona_pyramid_enabled`; leave positions and the base trading path
untouched.

---

## CI-07 — Doctor mixes state from different local Runtime instances

**Capability:** RT-10 Health, readiness, doctor, and stability
**Status:** Supported / default-branch delivery resolves GitHub #426

**Product promise**

Doctor reports the health and recovery actions for the Runtime that receives
the request.

**Actual path**

```text
isolated Runtime on port 8791
→ GET /_infra/doctor
→ fixed-port process probes for 8787/8788
→ report combines isolated database/config with another local instance
```

The captured runtime evidence originally reported the 8791 server PID as the
Gateway on 8787, then mixed in unrelated frontdoor/backend state from fixed
ports. The reviewed worktree now prefers the serving Runtime ID, PID, and port;
two-context and API tests keep the reports independent.

**User impact**

An owner can receive incorrect restart advice or misdiagnose a healthy isolated
Runtime based on another instance's state.

**Target state**

Every Doctor process check is scoped to the request's Runtime identity and
declared ports, or is clearly labeled as an external fixed-port observation.

**Minimum repair**

Pass the active Runtime identity/ports into existing probes and label
cross-instance observations. This repair is complete in the reviewed worktree;
do not add a new health framework.

**Acceptance path**

1. Start two isolated Runtimes with different IDs and ports.
2. Query each Doctor endpoint.
3. Assert every process finding is attributable to the queried Runtime.
4. Stop one Runtime and assert the other report remains unchanged.

**Failure/rollback**

Suppress process-specific Doctor checks when identity cannot be resolved; keep
health, readiness, and stability endpoints available.

---

## CI-10 — Tunnel PID is not sufficient process ownership

**Capability:** Tunnel lifecycle and remote-owner protection
**Status:** Implemented / ownership and lifecycle acceptance complete

**Product promise**

CLI, Settings, and Gateway lifecycle can start, report, and stop the configured
Cloudflare Tunnel while remote mutations remain owner-protected.

**Actual path**

```text
start_tunnel
→ spawn cloudflared
→ persist PID + executable + config command
→ status/stop revalidate all ownership fields
→ mismatch is visible and never signaled
```

Remote sensitive routes correctly fail closed when the tunnel token is absent
or invalid. Lifecycle acceptance now covers owned start/status/stop, legacy
pidfiles, PID reuse, and non-blocking async API dispatch.

**User impact**

PID reuse cannot make an unrelated process appear owned or receive a stop
signal. Blocking process inspection and startup waits run outside the API event
loop.

**Target state**

Complete in executable acceptance. Natural platform-specific tunnel operation
remains outside the paper Supported Core.

**Minimum repair**

Harden the existing tunnel module and route only. Do not add a tunnel
supervisor, generic process framework, or separate remote-access service.

**Acceptance path**

1. Start a fake owned cloudflared process and assert status reports it.
2. Simulate immediate process exit and assert PID state is cleared.
3. Reuse the recorded PID for a non-cloudflared identity and assert status is
   stale and stop sends no signal.
4. Exercise start/stop through the API without blocking the event loop.
5. Keep the existing missing/invalid-token 401 behavior.

**Failure/rollback**

Disable Tunnel and leave the local Gateway available. If ownership cannot be
verified, refuse to signal the recorded PID and require explicit operator
cleanup.

---

## CI-08 — Policy diff API is shadowed by the dynamic policy route

**Capability:** LN-19 Policy/version history and diff
**Status:** Supported / default-branch delivery resolves GitHub #428

**Product promise**

The persistence API exposes immutable policy history and a comparison endpoint
for two version IDs.

**Actual path**

```text
GET /api/persistence/policies/diff?from_id=...&to_id=...
→ earlier /policies/{policy_type} route matches policy_type="diff"
→ get_policy("diff")
→ intended diff_policy() endpoint is never called
```

**Current repair**

The static `/policies/diff` route now registers before
`/policies/{policy_type}`. `LP-S22` creates two immutable versions and verifies
the public diff, detail, and version-history endpoints together. Default-branch
delivery resolves GitHub #428.

**Evidence**

The pre-repair registered route order was:

```text
/policies/{policy_type}
/policies/{policy_type}/versions
/policies/diff
```

A direct TestClient request entered `get_policy()` and attempted to query the
current policy type named `diff`, confirming the static diff route is
unreachable through the product API.

**User impact**

Policy versions are durable, but an owner or agent cannot retrieve the promised
version comparison through the public endpoint.

**Target state**

The static diff route reaches `diff_policy_versions()` while existing policy
detail/history routes keep their current behavior.

**Minimum repair**

Resolve the route ambiguity inside the existing persistence router and add an
endpoint-level regression. Do not create a second policy API.

**Acceptance path**

1. Create two immutable versions of one policy.
2. Request `/api/persistence/policies/diff` with both IDs.
3. Assert added/removed/changed output.
4. Request `/api/persistence/policies/{policy_type}` and `/versions`; assert no
   regression.

**Failure/rollback**

Revert the route-order/path change; policy storage remains untouched.

---

## CI-09 — Serenity More/Less feedback has no research consumer

**Capability:** DC-13 Serenity preferences and theme feedback
**Status:** Experimental / explicitly bounded as non-behavioral history

**Product promise**

The Serenity Lens presents `+More` and `-Less` actions on active research
themes, alongside the profile-preference path that adapts later research.

**Actual path**

```text
Serenity Lens More/Less
→ POST /api/serenity/themes/{theme_id}/feedback
→ serenity_feedback_events
→ theme detail/history
→ no research selector, prompt, scorer, or scheduler consumer
```

Profile preferences are a separate working path: they are snapshotted on a
research run and consumed by the research worker.

**User impact**

The UI records the action, but future Serenity research is unchanged. A user
can reasonably interpret More/Less as preference learning rather than history
only.

**Target state**

Label the feedback as non-behavioral history. It must not add trading authority.

**Minimum repair**

Keep the existing feedback store and add explicit UI copy that More/Less records
history only. Do not create a generic feedback platform, second preference
system, or implicit research weighting.

**Acceptance path**

1. Open the Serenity Themes surface.
2. Assert the UI says More/Less records theme history only.
3. Assert it explicitly says future research selection and prompts are
   unchanged.
4. Submit feedback and confirm it remains visible in theme history.

**Failure/rollback**

Ignore the feedback and expose it as recorded-but-not-applied. Research
continues with the current neutral behavior.

## Triage order

1. CI-07 first because incorrect diagnosis can direct an owner to the wrong
   Runtime.
2. CI-03 next as an honest-surface repair with no runtime authority change.
3. CI-01 next because it corrupts owner interpretation of returns.
4. CI-04 and CI-06 together only at the product-surface boundary; implementation
   of scale-in remains a separate later decision.
5. CI-05 only after one exchange demo mode is explicitly added to Supported
   Core.
6. CI-02 remains tracked by #418, but Live stays Experimental and the defect is
   not promoted into the paper Supported Core implementation queue.
7. CI-08 is a bounded owner-visibility defect tracked by #428.
8. CI-09 is an honest-surface or bounded-consumer repair for a non-core
   research feature; it does not belong in the paper Supported Core queue.
