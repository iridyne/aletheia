# Current Admission And Promotion Gates

> **Role:** Product Truth for current admission decisions. These gates express
> durable prerequisites; they are not a roadmap, mutable checklist, or statement
> that any Runtime currently passes.
>
> Runtime facts come from Doctor, provenance, logs, and the selected data root.
> Current ownership and progress live in GitHub. Historical strategy-phase gates
> were retired when the RuleSet/DSL/backtest decision path was removed.
>
> Superseded simulator accounting and legacy Learning gate definitions are
> [archived](../../design/history/local_simulated_product_gates.md).

## Gate A - External demo/testnet mainline eligibility

Evidence may count toward the current MVP only when all of the following are true:

1. execution mode and decision path match
   [Active Runtime Scope](../ACTIVE_RUNTIME_SCOPE.md);
2. the running code, instance/data root, runtime session, policy epoch, and
   relevant behavior/outcome contracts are identifiable;
3. Startup -> Scheduler -> Opportunity -> Decision -> Risk -> Execution ->
   Outcome is operational without an unresolved integrity incident;
4. Live remains disabled and every order path still crosses Risk approval and the
   Execution Gateway;
5. the exact external account and instrument retain attributable private fills,
   fees, funding where applicable, and verified close economics; account state and
   reconciliation are available, with unresolved settlement reported explicitly;
6. unavailable, stale, synthetic, administrative, or unresolved facts are
   excluded or displayed honestly rather than converted to success/zero.

Passing a test suite does not pass this gate for a Runtime. Use
`uv run aletheia doctor` and current runtime evidence.

## Gate CN - Bounded CN Equity implementation and promotion

The accepted CN Equity Proposal and Product/Runtime scope authorize implementation
only. They do not pass a Runtime gate or make CN Equity supported. Promotion is
strictly one-way through `disabled -> observe -> shadow -> paper`; a failure
returns the extension to the last proved lower state without rewriting evidence.
No `live` state exists.

| Target state | Required proof before entry |
|---|---|
| `disabled` | Default. No CN V2 producer or CN Candidate/Proposal/reservation/order/accounting write is reachable. Contracts and compatibility readers may be merged behind this fence. |
| `observe` | Versioned CN identity, calendar/rules/tradability, point-in-time data manifest, and Universe publication are complete; missing/stale/unknown facts fail closed; observation has no Proposal, reservation, Risk approval, Gateway call, or execution side effect. |
| `shadow` | The complete Candidate → Plan → exact Proposal → Risk → Gateway → exit → Outcome chain is contract- and spec-conformant, but execution is denied and no executable approval or accounting mutation is emitted. An exact merged commit and frozen data/policy/account/experiment identity start a fresh window. |
| `paper` | The fresh Shadow window is clean; the owner explicitly admits the exact epoch; only the dedicated CNY Internal Paper account/Risk pool is reachable; settlement, fees, tax, reservations, corporate actions, restart, reconciliation, and canonical entry/exit are proven. |

CN qualification closes as two independent facts:
`paper_accounting_qualified` and exactly one of
`strategy_paper_promotion_passed` or `strategy_paper_promotion_failed` for
the same frozen epoch. A failed strategy result preserves valid accounting proof
but forbids strategy promotion. Backtest, Shadow, Paper, and any future broker
evidence are separate classes and cannot be pooled. Capability Ground Truth is
updated only from exact merged/runtime evidence and must continue to report Live
as unavailable.

## Gates B-D - Learning measurement, admission and progression

The [Learning contract](../../design/contracts/learning_contract.md) and
[Observation Lab contract](../../design/contracts/observation_lab_contract.md)
own current measurement and influence requirements. Observation Lab is the sole
online Experiment authority. The former local-simulator/legacy-ledger gate table
is [archived](../../design/history/local_simulated_product_gates.md); its historical
sample counts, stages and verdicts cannot authorize a current account or window.

Current measurement requires a supported registry path, exact prospective cohort,
behavior/outcome identity, attributable costs and outcomes, and the path-specific
harm and inference contract. Path registration alone does not prove runtime
qualification. Missing or conflicting authority cannot fall back to a legacy
ledger or manufacture an intervention.

The `lco-v1` contribution endpoint reads persisted Lab results. It exposes
measurement, inference and lifecycle facts without granting authority or offering
an admission mutation. Explicit owner admission and the path-specific authority
gate remain required for behavioral influence; a displayed positive result cannot
self-authorize progression. `shadow` and `paper_*` experiment-stage names describe
bounded influence and do not reinstate the retired local exchange.

No Learning result grants Risk, Gateway, credential or Live authority. Changes to
cohort identity, treatment or outcome semantics require the fresh evidence defined
by those contracts; archived simulator evidence cannot become exchange qualification.

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
- the former internal-simulator proof profile or its adapter-visibility exclusions;
- lesson/artifact counts as evidence of positive Learning contribution;
- the June 2026 Round 1 calendar, tags, or MVP script.

Historical Round 1 material remains provenance only. It cannot authorize current
work or qualify a current Runtime.
