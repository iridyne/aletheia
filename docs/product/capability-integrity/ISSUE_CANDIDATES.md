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
**Status:** Partial / owner-facing promise repaired; adjusted consumer still missing

**Product promise**

Settings says deposits and withdrawals are recorded so cumulative PnL reflects
true investment performance.

**Actual path**

```text
Settings Capital Events card
→ POST /api/capital/events
→ capital_events
→ GET /api/capital/net-deposits
→ Settings summary only

analytics equity curve
→ realized eligible position PnL
→ rendered curve
```

`aletheia.storage.repositories.get_net_deposits` has no analytics consumer.
`aletheia.analytics.trade_insights._equity_curve` and the account-equity-history
route build their outputs without capital events. The comment in
`aletheia/server/routes/capital.py` claiming that analytics subtracts net
deposits is therefore false.

**User impact**

An external deposit or withdrawal is persisted and shown in Settings but can be
misrepresented as investment performance in the product's curves.

**Target state**

Every curve that promises investment performance either consumes a
well-defined capital-adjustment series or is relabeled as raw account equity.
Raw account equity and investment return must not be conflated.

**Minimum repair**

Define one capital-event adjustment projection and consume it in the promised
curve(s). Do not redesign portfolio accounting or rewrite historical trades.

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
**Status:** Partial / automatic action intentionally absent

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
→ System Operator reevaluation
  OR daemon unavailable: wake_failed + explicit cycle error
→ no automatic close or stop mutation
```

`aletheia/portfolio/position_manager.py` explicitly preserves Operator
authority by waking it instead of auto-closing or tightening. The focused tests
assert this exact behavior and the failed-delivery path.

**User impact**

The earlier configuration surface promised automatic protection that was not
performed. That mismatch is repaired. The remaining limitation is liveness:
the accepted wake queue is process-local and is not durable across daemon or
process loss.

**Target state**

Keep the current authority model and describe this capability as
market-aware Operator wake/review, unless a separate approved design changes
the execution authority.

**Minimum repair**

The honest naming and failed-delivery result are implemented. Do not add
autonomous exit authority or a new queue framework under this issue. A durable
carrier is only justified if observed runtime loss shows the existing
heartbeat/re-evaluation path is insufficient.

**Acceptance path**

1. Enable the setting and inject an urgent adverse signal for an open position.
2. Assert a running daemon accepts the urgent wake.
3. Assert no position/order is mutated before an Operator decision.
4. Stop the daemon and assert the next attempt records `wake_failed` and an
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

**Capability:** EX-14 Cost-complete close settlement
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
**Status:** Partial / Trigger-missing and Consumer-missing

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

Either hide the feature as experimental, or route one explicit Operator scale
decision through proposal, Risk, execution, fill attribution, and position
quantity/cost-basis update.

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
**Status:** Partial / process ownership and acceptance missing

**Product promise**

CLI, Settings, and Gateway lifecycle can start, report, and stop the configured
Cloudflare Tunnel while remote mutations remain owner-protected.

**Actual path**

```text
start_tunnel
→ spawn cloudflared
→ write integer tunnel.pid
→ status/stop checks only whether that PID is alive
→ no executable identity check that the process is still owned cloudflared
```

Remote sensitive routes correctly fail closed when the tunnel token is absent
or invalid. The process lifecycle is weaker: after PID reuse, status can report
an unrelated process as the tunnel and stop can signal that unrelated process.
There are no focused start/stop/status tests. The async start API also calls the
synchronous three-second startup check directly.

**User impact**

Tunnel state may be false after restart or PID reuse, and a stop request could
terminate an unrelated local process. A blocked API worker can also delay other
requests during startup verification.

**Target state**

Lifecycle state proves both PID liveness and cloudflared ownership before
reporting or signaling the process. API startup does not block the event loop,
and focused tests cover start, immediate failure, status, safe stop, stale
ownership, and auth failure.

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
