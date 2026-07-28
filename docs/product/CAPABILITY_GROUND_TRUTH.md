# Aletheia Capability Ground Truth

This is the canonical capability-status document for the current reviewed
worktree. Commit identities belong in the evidence ledger for reproducibility;
merge/delivery state does not change what the current code implements.

It is generated from current code and executable evidence, not copied from an
SDD. The inventory, audit rules, detailed fact cards, coverage ledger, and
issue-ready findings live under
[`capability-integrity/`](capability-integrity/README.md).

## Interpretation

- `Supported` requires a complete static path, focused executable acceptance,
  inclusion in the declared product support surface, and no known
  contradiction.
- `Implemented` means the code and executable path exist, but the capability is
  intentionally not yet part of the declared support surface.
- E3 is optional operational observation. It increases confidence but is not a
  support gate and does not require a separate detection system.
- `Experimental` is an intentional non-core or observe-only capability.
- `Partial` and `Broken` rows name the missing link in `Gap`.
- `Core` means candidate for the first supported profile. It does not mean the
  capability is already supported.

The active product scope is `crypto-paper-mainline-v1`: crypto spot/perpetual,
`paper_internal`, System Operator as the sole strategy decision authority, Risk
Engine as the approval authority, and Learning influence disabled by default.

Table IDs are audit-only stable references, not Aletheia product terminology.
Outside these canonical tables, use the full capability name.

| Status | Count |
|---|---:|
| Supported | 68 |
| Implemented | 4 |
| Partial | 6 |
| Experimental | 21 |
| Broken | 1 |
| Deprecated | 0 |
| **Total** | **100** |

## Status matrix

### Runtime and operations

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| RT-01 | Packaged runtime installation and first launch | E1+E2 | Supported | None in tested contract | No |
| RT-02 | Stable Gateway lifecycle | E1+E2 | Supported | None in tested contract | Yes |
| RT-03 | Frontdoor and backend supervision | E1+E2 | Supported | None in tested contract | Yes |
| RT-04 | Gateway pause and resume | E1+E2 | Supported | None in tested contract | Yes |
| RT-05 | Gateway restart | E1+E2 | Supported | None in tested contract | Yes |
| RT-06 | Scheduler lifecycle and job visibility | E1+E2+E3 | Supported | None in validated profile | Yes |
| RT-07 | Connectivity auto-pause and auto-resume | E1+E2 | Supported | None in tested contract | Yes |
| RT-08 | Startup safety gates and recovery | E1+E2 | Supported | None in tested contract | Yes |
| RT-09 | Runtime continuity candidate/shadow/promotion | E1+E2 | Experimental | Outside active runtime scope; no natural promotion run is claimed | No |
| RT-10 | Health, readiness, doctor, and stability | E1+E2 | Supported | Serving Runtime identity/port and two-context isolation are executable contracts; delivery remains tracked separately | Yes |
| RT-11 | Backup, verification, export, and import | E1+E2 | Supported | None in tested contract | No |
| RT-12 | Data and filesystem retention | E1+E2 | Supported | None in tested contract | No |
| RT-13 | Update check, apply, activation, and rollback | E1+E2 | Supported | None in tested contract | No |
| RT-14 | Tunnel lifecycle and remote-owner protection | E1 | Partial | Auth fails closed, but PID ownership is not verified and start/stop/status have no focused lifecycle acceptance | No |
| RT-15 | Proxy/network routing and diagnostics | E1+E2 | Supported | None in tested routing contract | No |

### Market data and universe

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| MD-01 | Core market snapshot collection | E1+E2+E3 | Supported | None in validated profile | Yes |
| MD-02 | Watched-position price feed | E1+E2 | Supported | None in tested contract | Yes |
| MD-03 | Orderbook, funding, and open-interest collection | E1+E2+E3 | Supported | None in validated profile | Yes |
| MD-04 | Liquidation, macro, and cross-venue context | E1+E2 | Supported | None in tested context/degradation contract | No |
| MD-05 | Historical OHLCV backfill | E1+E2+E3 | Supported | None in validated profile | Yes |
| MD-06 | Dynamic Universe generation | E1+E2+E3 | Supported | None in validated profile | Yes |
| MD-07 | Universe family calibration | E1+E2 | Experimental | External runtime experiment and verdict are tracked separately | No |
| MD-08 | Forward outcome settlement | E1+E2 | Supported | None in tested contract | Yes |
| MD-09 | Market regime detection | E1+E2 | Supported | None in tested detector-to-consumer contract | Yes |
| MD-10 | News ingestion and analysis | E1+E2 | Supported | None in tested ingestion/analysis/research contract | No |
| MD-11 | Equity opportunity observation | E1+E2 | Experimental | Outside active crypto scope | No |
| MD-12 | Prediction-market observation | E1 | Experimental | Research surface; not an active execution path | No |
| MD-13 | Exchange symbol/market eligibility | E1+E2+E3 | Supported | None in validated profile | Yes |

### Research, opportunities, and decisions

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| DC-01 | Market Observer opportunity generation | E1+E2 | Supported | None in tested contract | Yes |
| DC-02 | Paper Ranker admission | E1+E2 | Supported | None in tested contract | Yes |
| DC-03 | System Operator trading decision | E1+E2 | Supported | None in tested contract | Yes |
| DC-04 | PI scheduled decision fallback | E1+E2 | Supported | None in tested contract | Yes |
| DC-05 | Proposal creation and causal identity | E1+E2 | Supported | None in tested contract | Yes |
| DC-06 | Paper proposal approval and rejection | E1+E2 | Supported | None in tested contract | No |
| DC-07 | Candidate ledger and decision trace | E1+E2 | Supported | None in tested contract | Yes |
| DC-08 | Agent run and opportunity observability | E1+E2 | Supported | None in tested failure-visibility contract | Yes |
| DC-09 | Manual agent triggering | E1+E2 | Supported | None in tested bounded-job contract | No |
| DC-10 | Operator command workspace | E1+E2 | Supported | None in tested contract | No |
| DC-11 | Operator session branching, tasks, and extensions | E1 | Experimental | Extension execution is intentionally bounded/non-core | No |
| DC-12 | Serenity multi-lens research | E1 | Experimental | Research-only and outside active trading proof | No |
| DC-13 | Serenity preferences and theme feedback | E1+E2 | Experimental | Profile preferences adapt research; More/Less is explicitly non-behavioral theme history (#429 records the repair) | No |

### Risk and authority

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| RK-01 | Mandatory proposal risk evaluation | E1+E2 | Supported | None in tested contract | Yes |
| RK-02 | Scoped demo/live risk policy management | E1+E2 | Supported | None in tested contract | Yes |
| RK-03 | Global and mode trading brakes | E1+E2 | Supported | None in tested contract | Yes |
| RK-04 | Loss, margin, leverage, and liquidation limits | E1+E2 | Supported | None in tested contract | Yes |
| RK-05 | Exposure and concentration limits | E1+E2 | Supported | None in tested contract | Yes |
| RK-06 | Data freshness and instrument integrity gates | E1+E2 | Supported | None in tested contract | Yes |
| RK-07 | Permission modes and restricted tool authority | E1+E2 | Supported | None in tested contract | Yes |
| RK-08 | Go-live eligibility scorecard | E1+E2 | Experimental | Live trading intentionally remains outside the supported product surface | No |

### Execution and position lifecycle

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| EX-01 | Automatic local paper trading | E1+E2 | Supported | None in tested paper contract | Yes |
| EX-02 | Exchange demo trading | E1+E2 | Experimental | Adapters exist; outside active-scope proof | No |
| EX-03 | Live Unlock and relock | E1+E2 | Experimental | UI and contract now identify this as an authority prerequisite only; Live remains outside support and EX-04 remains broken | No |
| EX-04 | New-proposal live execution mode | E1+E2 | Broken | Trigger-missing: unlock state is not consumed by proposal-mode selection; explicit `LU-C07` tripwire and GitHub #418 | No |
| EX-05 | Order constraint normalization | E1+E2 | Supported | None in tested contract | Yes |
| EX-06 | Exchange order materialization | E1+E2 | Implemented | No owner-selected external execution mode in supported profile | No |
| EX-07 | Exchange-native TP/SL brackets | E1+E2 | Experimental | Broker/private-fill paths tested; external venue behavior outside supported surface | No |
| EX-08 | Local protective exits | E1+E2 | Supported | None in tested paper contract | Yes |
| EX-09 | Market-aware position review | E1+E2 | Partial | Evaluator and best-effort Operator wake are complete; delivery failure is durable and visible, but the wake queue itself is in-memory | No |
| EX-10 | Horizon and thesis review | E1+E2 | Supported | None in tested contract | Yes |
| EX-11 | External demo-position adoption | E1+E2 | Experimental | Opt-in reconciliation path outside core | No |
| EX-12 | Exchange order/position reconciliation | E1+E2 | Implemented | No owner-selected external execution mode in supported profile | No |
| EX-13 | Stale order and disappearance handling | E1+E2 | Implemented | No owner-selected external execution mode in supported profile | No |
| EX-14 | Cost-complete close settlement | E1+E2 | Partial | Local paper complete; exchange funding/bill evidence writer absent | Yes |
| EX-15 | Partial reduce and scale-in lifecycle | E1 | Partial | Active actions reject reduce/scale-in; the dispatcher fails closed and the OKX helper remains isolated without Proposal/Risk authorization | No |

### Portfolio, performance, and attribution

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| PF-01 | Local paper cash account | E1+E2 | Supported | None in tested paper contract | Yes |
| PF-02 | Capital event recording | E1+E2 | Supported | Recording is supported; PF-03 separately tracks missing curve consumption | No |
| PF-03 | Capital-adjusted performance curve | E1 | Partial | Public copy now states that no adjusted curve exists; Capital Events remain unconsumed by performance analytics (#419) | No |
| PF-04 | Portfolio snapshots and account equity history | E1+E2 | Supported | Raw equity only; PF-03 tracks capital-adjusted curve gap | Yes |
| PF-05 | Position and trade blotter | E1+E2 | Supported | None in tested contract | Yes |
| PF-06 | Trade insights and performance analytics | E1+E2 | Supported | None in tested eligible-outcome contract | Yes |
| PF-07 | Trade attribution and thesis lineage | E1+E2 | Supported | None in tested contract | Yes |
| PF-08 | Opportunity flow and funnel attribution | E1+E2 | Supported | None in tested contract | Yes |
| PF-09 | Alpha/family/factor attribution | E1+E2 | Experimental | Measurement exists; a qualified natural sample/verdict is absent | No |

### Learning and policy evolution

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| LN-01 | Post-trade review | E1+E2 | Supported | None in tested eligible-outcome contract | No |
| LN-02 | Risk critic | E1+E2 | Supported | None in tested advisory contract | No |
| LN-03 | Dreaming consolidation | E1+E2 | Supported | None in tested consolidation-to-Operator contract | No |
| LN-04 | Trading memo synthesis | E1+E2 | Supported | Advisory only; neutral fallback tested | No |
| LN-05 | Knowledge retrieval | E1+E2 | Supported | Advisory injection and empty fallback tested | No |
| LN-06 | Lesson review lifecycle | E1+E2 | Supported | Review state gates consumption | No |
| LN-07 | Skill drafting and review | E1 | Experimental | Draft/review only; activation intentionally absent | No |
| LN-08 | Learned guard generation and enforcement | E1+E2 | Supported | Optional paper influence; disabled by default | No |
| LN-09 | Confidence calibration | E1+E2 | Supported | Measurement/reporting only | No |
| LN-10 | Shadow trades and counterfactual resolution | E1+E2 | Experimental | Observe-only by design | No |
| LN-11 | Filter controller observation | E1+E2 | Experimental | Consumer mutation intentionally absent | No |
| LN-12 | Measured Learning contribution | E1+E2 | Experimental | A qualified natural cohort/verdict is absent | No |
| LN-13 | Learning lifecycle automation | E1+E2 | Experimental | Policy-gated paper experiment; no E3 | No |
| LN-14 | Persona base sizing | E1+E2 | Supported | Notional/Kelly reach immutable Proposal quantity; other persona fields are explicitly outside this capability | No |
| LN-15 | Persona regime learning | E1+E2 | Experimental | Bounded notional appetite only; default influence closed | No |
| LN-16 | Persona progressive unlock | E1+E2 | Experimental | Default-off; active entry parameters only, but the gate uses lifetime eligible closes rather than a prospective Persona/policy cohort (#409) | No |
| LN-17 | Persona pyramid decision | E1+E2 | Partial | Pure helper has no production trigger/materializer; Settings/API block new enablement (#410) | No |
| LN-18 | Persona rollout automation | E1+E2 | Experimental | Scheduler is an explicit read-only safety hold; prospective Persona/policy step verdict is absent (#409) | No |
| LN-19 | Policy/version history and diff | E1+E2 | Supported | Static diff route precedes the dynamic detail route; endpoint acceptance covers immutable diff, detail, and history | No |

### Security, configuration, and owner visibility

| ID | Capability | Evidence | Status | Gap | Core |
|---|---|---|---|---|---|
| OS-01 | Write-only secret management | E1+E2 | Supported | None in tested contract | Yes |
| OS-02 | Model provider configuration and fallback | E1+E2 | Supported | Provider-independent routing/fallback contract | No |
| OS-03 | Exchange credential verification | E1+E2 | Implemented | Candidate and stored checks use private read-only endpoints with secret-safe failures; outside Supported Core | No |
| OS-04 | Settings persistence and coverage | E1+E2 | Supported | Individual unwired settings remain classified on their own capability rows | No |
| OS-05 | Append-only audit trail | E1+E2 | Supported | None in tested contract | Yes |
| OS-06 | Telegram alerts and daily summaries | E1+E2 | Supported | Delivery attempts and failures are explicit; external availability is not guaranteed | No |
| OS-07 | Owner-visible failure states | E1+E2+E3 | Supported | None in validated API profile | Yes |
| OS-08 | Realtime dashboard updates | E1+E2 | Supported | Websocket/poll fallback contract tested | No |

## Supported Core profile

The first supported profile is intentionally narrower than the product map:

```text
Runtime start/recovery and scheduler observability
→ current market data and active universe
→ Market Observer opportunity
→ Paper Ranker admission
→ System Operator decision
→ deterministic Risk decision
→ paper_internal execution
→ position lifecycle and protective close
→ cost-complete outcome
→ PnL, attribution, and owner-visible failure state
```

No Experimental capability is required for this profile. Live execution,
exchange demo execution, Serenity, prediction markets, Learning influence,
persona rollout, and runtime-continuity promotion remain outside it until they
are deliberately added to the support surface with complete E1/E2 acceptance.

## First-principles check

This promotion pass preserves the Alpha-first system boundary. It reuses the
frozen Golden Path, existing failure/replay tests, and the existing capability
documents. It adds no verification framework, scheduler, runtime state, product
abstraction, or trading authority. Natural Runtime observations remain useful
for operations and experiments, but are not required to recognize a complete
tested software contract.

## Current review conclusion

The current review classifies 68 rows as `Supported`. Several former Partial
rows were evidence gaps rather than product gaps. The three external-exchange
rows and credential verification stay
`Implemented`, while policy/version history and diff is `Supported` and
external TP/SL behavior remains `Experimental`. The live
routing defect remains open as GitHub #418. Health/Doctor is `Supported`; the
default-branch delivery resolves #426. EX-09 is now
an honest wake/review capability rather than a false automatic-exit promise.

The subsequent review keeps the original 20 Serenity/observe-only rows as
honest, bounded experiments. DC-13 now states its split boundary directly: profile
preferences reach the research worker, while More/Less events are durable
theme history and do not alter future selection or prompts. The default-branch
delivery resolves GitHub #429. The review added no runtime
detector or verification framework.

LN-03 is now `Supported`. The existing daily scheduler runs the Dreaming agent,
which writes durable consolidation artifacts and extracts `trade_case_study`
lessons. Existing lesson review moves qualified lessons to `applied`, and the
tested Layer3/Operator-prompt consumer reads that exact durable population.
This closes the previously documentary evidence gap without adding a new
pipeline.

LN-14 is also `Supported` after separating base entry sizing from the adjacent
unlock and pyramid experiments. The active persona's notional/Kelly values
reach System Operator sizing and immutable Proposal quantity. Settings now
labels leverage, stop, and add ranges as experimental/reference-only rather
than implying that base sizing consumes every persona field.

Persona progressive unlock is `Experimental`, not `Supported`: runtime now
ignores ladder fields without a production entry consumer, but its evidence
gate still counts lifetime eligible closes rather than a prospective cohort
for the current Persona and policy epoch. Settings states that limitation and
the mechanism remains default-off. GitHub #409 tracks the evidence-safety gap;
the separate pyramid materialization gap remains on GitHub #410.

Persona rollout automation remains `Experimental` and no longer mutates
configuration. The former implementation treated an eight-hour interval plus
broad health checks as an observation verdict and could automatically activate
the unsafe historical-count unlock path. The retained scheduler job now reports
a read-only safety hold; Settings blocks new enablement and permits legacy
enabled configurations to be switched off. No new rollout state machine was
introduced.

Three `Implemented` exchange-lifecycle rows were then reviewed as one existing
recovery chain. EX-06, EX-12, and EX-13 all retain complete static consumers
and focused executable recovery/failure paths (156 passing assertions). They
remain `Implemented`, rather than `Supported`, solely because no external
exchange execution mode belongs to `crypto-paper-mainline-v1`. No additional
product-integrity issue was found in this pass.

Exchange credential verification is the fourth `Implemented` capability.
Bitget, Binance, and OKX candidate/stored checks use private read-only account
endpoints, close clients on both success and failure, and do not expose raw
secrets. It remains outside the supported paper profile because external venue
availability is not part of that product surface.

PF-03 was subsequently downgraded from `Broken` to `Partial`. Settings and the
Capital API no longer claim that current curves consume Capital Events:
Account Equity remains raw and Cumulative PnL remains verified trading PnL.
The optional cash-flow-adjusted projection is still absent, explicitly tracked
by `AN-C06` and GitHub #419.

## Owner decision boundary

Live trading is intentionally retained as `Experimental`. This does not erase
known defects: EX-04 remains `Broken` and tracked by GitHub #418, but its repair
is not a prerequisite for the paper Supported Core. Settings, diagnostics, and
the Live Unlock contract now state that unlock records an authority prerequisite
and does not route new Proposals to live execution.

One product decision remains before external execution promotion:

1. Whether one external demo-exchange mode should join the supported profile.
   If yes, select exactly one mode before promoting EX-06/EX-12/EX-13. If no,
   keep the adapters implemented but outside the support promise.

The repository contains a substantial executable paper-trading core, but the
public product surface is broader than the proven support surface. The most
important integrity problems are not missing modules; they are incomplete
joins:

1. capital events persist but do not change performance curves;
2. live unlock changes authority state but not new-proposal execution mode;
3. market-aware exits stop at an Operator review wake rather than an automatic
   position mutation;
4. progressive Persona authority uses lifetime outcomes rather than a
   prospective Persona/policy step cohort;
5. exchange-backed close economics lack a complete private funding/bill
   evidence writer;
6. pyramid/scale behavior has decision logic without a production
   materialization path;
7. Serenity More/Less feedback is recorded but does not adapt later research.

These are specified narrowly in
[`ISSUE_CANDIDATES.md`](capability-integrity/ISSUE_CANDIDATES.md).
