# Session Handoff — 2026-07-20

> Session type: Learning-module structural repair + observability closure.
> All session PRs merged to master via admin bypass (CI temporarily unavailable).
> Master head: `613ab66d` at handoff time.

---

## 1. What shipped this session (6 PRs, all merged)

| PR | Commit | Layer | Scope |
|---|---|---|---|
| [#143](https://github.com/iridite/aletheia/pull/143) | `cca0acbd` | Learning | P1 — `market_observer._analyze_symbol` writes `learning_candidate_key` into `opportunity.metadata`. Ranker paths (`ranker_case_score`/`memo_score`/`factor_filter`) can now record exposures. P2 — `SUPPORTED_PATH_IDS` expanded from `{universe_family_calibration, operator_learning_bundle}` to 6 paths including all three ranker paths and `observer_advisory_bundle`. |
| [#145](https://github.com/iridite/aletheia/pull/145) | `fef39597` | Analytics/API | `compute_persistent_recommendations` — cross-run stability analyzer on `controller_recommendations`. New fields on `GET /analytics/filter-controller`. New CLI section. `ControllerConfig.persistent_min_consecutive_runs=3, persistent_max_runs_lookback=10`. Client.ts type extended. |
| [#146](https://github.com/iridite/aletheia/pull/146) | `434b507e` | Frontend | Learning → Reports tab card rendering persistent controller signals. Meta line, empty-state naming, streak badge, tone-colored signal, timestamp, reasoning line. Prominent no-live-consumer disclaimer. |
| [#147](https://github.com/iridite/aletheia/pull/147) | `e272d894` | Analytics | `shadow_trades` resolvers Paths 1 & 2 now populate `net_pnl_pct`, `cost_model_version`, `gross_return_pct`, `estimated_fee_pct`, `estimated_spread_slippage_pct`, `estimated_funding_pct`, `min_tradable_edge_pct`, `settlement_price_source`. Uses the same `estimate_outcome_economics` helper as `filter_candidate_outcomes` settlement. 5 new unit tests. |
| [#148](https://github.com/iridite/aletheia/pull/148) | `34d8d8e0` | Learning registry | `filter_parameter_tune` skeleton entry declared with `consumer_wired=False, is_learning_treatment=False, settings_supported=False` and design doc at `docs/archive/legacy/plans/learning/filter_parameter_tune_design.md`. |
| [#149](https://github.com/iridite/aletheia/pull/149) | `613ab66d` | Docs | Full rewrite of `docs/archive/legacy/plans/learning/evolution_module_overview.md` (2026-07-02 stub → current-state architecture with 10 injection paths, 4-layer structure). Audit doc `LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md` gained §8.1 recording repair progress (19/20 gaps closed) and the "problem-5 architecturally infeasible" decision record. |

## 2. Past 4 days (2026-07-16 → 2026-07-20) — 50 commits, by area

```text
Frontend UI / UX               ████████████ 12
  ├─ overview (5)              — cold-start perf, metric-tile clip, status-row chevron, sm/md/lg/xl scale, cap defer display
  ├─ ui (3)                    — DataTable noWrap default, persistent card, design lessons
  ├─ format (2)                — fmtCompact, subscript-zero
  └─ frontend/dashboard (2)    — 15s fetch cap, cold-start reduction

Learning module                ██████████ 10
  ├─ P1+P2 wiring              (this session)
  ├─ Persistent signals        (this session)
  ├─ shadow enrichment         (this session)
  ├─ filter_parameter_tune     (this session skeleton)
  ├─ SDD-vs-reality repair     (#141 landed pre-session)
  └─ contribution measurement  (#748e2e2c, #cff6b3e5)

Operator flow                  ████ 4
  └─ defer-loop, adverse skip, non-terminal defer, opportunity carrier preservation

Docs / SDD                     ████ 4
  └─ ADR layer added, opportunity lifecycle contracts, product philosophy, session handoff

Risk / paper reconciliation    ███ 3
  └─ orphaned paper ledger, conservative realized_pnl, risk daily-loss

Data collectors                ██ 2
  └─ OI delta exchange, funding-rate gap

Gateway / infra                ██ 2
  └─ dashboard_static excluded from watcher, no hot-reload

Blotter / Approval             █ 1
Serenity                       █ 1
Diagnostics                    █ 1
Settings                       █ 2
Opportunity lifecycle          █ 2
Analytics (filter_controller)  █ 1 (this session)
Shadow_trades                  █ 1 (this session)
```

**Where the energy went**: frontend UI polish + learning verdict-pipeline plumbing + operator defer/skip logic.
**Where the energy did NOT go** (see §3 for the strategic implication):
`execution/`, `risk_limits`, `owner_mutation_route`, `live_trading_gate`, `model_provider`, `credentials/secrets`, `bitget/auth`, `agent_runtime/permissions`, `universe/allocator` (deep work), `strategy_lifecycle`.

---

## 3. Strategic focus — where NOT to keep polishing

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    WORK-CONCENTRATION vs UNBLOCK-VALUE                    │
│                                                                           │
│   heavy work here ──────────► frontend UI polish (safe, easy)             │
│        │                                                                  │
│        │              ┌────────────────────┐                             │
│        │              │  learning verdict  │                             │
│        │              │    plumbing        │  ← this session's focus     │
│        │              └────────────────────┘                             │
│        │                                                                  │
│        │              ┌────────────────────┐                             │
│        │              │  operator flow     │                             │
│        │              │  (defer/skip)      │                             │
│        │              └────────────────────┘                             │
│        ▼                                                                  │
│                                                                           │
│                       ▲                                                   │
│                       │                                                   │
│   HIGH UNBLOCK        │              ┌──────────────────────────────┐    │
│   VALUE — TOUCH!      │              │ admit_shadow_trial real impl │    │
│                       │              │ (authenticated owner route)  │    │
│                       │              │ ← ALL learning promotion     │    │
│                       │              │   blocked until this exists  │    │
│                       │              └──────────────────────────────┘    │
│                       │                                                   │
│                       │              ┌──────────────────────────────┐    │
│                       │              │ Live-mode readiness path     │    │
│                       │              │ (mode transition, credential │    │
│                       │              │  boundary, risk envelope)    │    │
│                       │              └──────────────────────────────┘    │
│                       │                                                   │
│                       │              ┌──────────────────────────────┐    │
│                       │              │ Universe allocator deep tune │    │
│                       │              │ (only path with 18k exposures│    │
│                       │              │  — no verdict yet on master) │    │
│                       │              └──────────────────────────────┘    │
│                       │                                                   │
│                       │              ┌──────────────────────────────┐    │
│                       │              │ Model provider tuning        │    │
│                       │              │ (LLM budget/routing/failure  │    │
│                       │              │  handling — no touches)      │    │
│                       │              └──────────────────────────────┘    │
│                       │                                                   │
│                       │              ┌──────────────────────────────┐    │
│                       │              │ Test infra / CI recovery     │    │
│                       │              │ (CI is currently unavailable │    │
│                       │              │  — all merges via bypass)    │    │
│                       │              └──────────────────────────────┘    │
│                       ▼                                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Recommended next foci, ranked by unblock value**:

1. **`admit_shadow_trial` real authenticated owner route** — this is the single hard-manual gate blocking every learning experiment from advancing to `paper_10`. Currently a fail-closed stub returning `authenticated_owner_admission_unavailable`. Until this exists, everything Learning-related is theoretical: exposures accumulate, verdicts materialize, but no experiment ever leaves shadow. Highest structural unblock.

2. **CI recovery** — CI unavailable means every merge is admin-bypass. Fast-regression + secrets-scan failures need to be fixed at the pipeline level; individual PRs cannot be validated without it. Second unblock in importance.

3. **Universe allocator verdict flow validation** — this is the ONLY path with meaningful exposure volume (18k+). If the verdict layer has bugs, they'd show here first. Post-#141+#143, a targeted end-to-end audit against the actual exposure data on running instances would let us either kill the shadow lock or discover the next structural issue.

4. **Model provider tuning / observability** — zero commits in 4 days. LLM budget, routing, failure recovery, and cache behaviour affect every agent (System Operator, market_observer, dreaming_agent). Worth a targeted audit on cost/latency/failure metrics.

5. **Live-mode readiness path** — clearly documented as "off by default" in AGENTS.md. No commits touch the credential boundary, live-execution wiring, or the mode-transition ceremony. When admit_shadow_trial lands and shadow → paper starts producing verdicts, live-mode readiness will become the next bottleneck.

6. **Strategy lifecycle rehabilitation** — marked LEGACY in Position C. No promotion path exists. If we're not going to bring it back, it should be actively deprecated in code (not just docs); if we are, that needs its own plan.

---

## 4. Where to look — future navigation

### 4.1 Learning module (heaviest work area)

| Question | Where to look |
|---|---|
| What's the current path inventory? | `aletheia/learning/registry.py` — the 15 declared paths |
| Which paths can produce verdicts? | `aletheia/learning/cohorts.py::SUPPORTED_PATH_IDS` |
| How does A/B assignment work? | `aletheia/learning/experiments.py::apply_numeric_treatment` + `apply_boolean_treatment` |
| Why is an exposure "not applied"? | `aletheia/learning/authority.py::resolve_learning_authority` + `stable_assignment` |
| Why does shadow → paper_10 not advance? | `aletheia/learning/lifecycle.py::admit_shadow_trial` (fail-closed stub) |
| What's the design intent vs current state? | `docs/archive/legacy/plans/learning/evolution_module_overview.md` (rewritten this session) |
| What's the target contract? | `docs/design/contracts/learning_evolution_contract.md` |
| Historical audit + repair progress | `docs/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md` §8.1 |
| Why isn't filter_controller → hypotheses auto-wired? | PR #145 description + `docs/archive/legacy/plans/learning/filter_parameter_tune_design.md` |
| Persistent controller signals | `aletheia/analytics/filter_controller.py::compute_persistent_recommendations` |
| Learning UI card | `frontend/src/pages/Learning.tsx::FilterControllerPersistentCard` |

### 4.2 Shadow trades (now cost-aware, since PR #147)

| Question | Where to look |
|---|---|
| How is a shadow trade resolved? | `aletheia/analytics/shadow_trades.py` — three paths: `resolve_shadow_trades_for_position` (event), `resolve_stale_shadows` (24h sweep), `settle_linked_shadow_outcomes` (piggyback from filter settler) |
| Are cost fields populated? | Yes since PR #147 for Path 1+2. Path 3 always did. See `_compute_shadow_economics`. |
| What outcome contract? | `aletheia/universe/outcome_economics.py::estimate_outcome_economics` — the canonical cost helper |

### 4.3 Observability signals

| Question | Where to look |
|---|---|
| Persistent overrestrictive filter signals | Learning → Reports tab (frontend), CLI `ato opportunity audit --view controller`, `GET /analytics/filter-controller` |
| Learning exposure counts by path | `GET /api/analytics/learning-contribution` (per GAP-18 in #141) |
| Learning experiments list | `GET /api/settings/learning/experiments` (per GAP-18) |
| Contribution verdict details | `inspect_learning_experiment` command |

### 4.4 Boundaries (must-read before touching adjacent code)

| Document | Governs |
|---|---|
| `docs/design/subsystems/causal_counterfactual_research_guidance.md` | L0–L5 evidence ladder; observational-vs-prospective boundary |
| `docs/design/contracts/learning_evolution_contract.md` | Section 2.2 declared-path registry contract |
| `docs/design/contracts/opportunity_outcome_contract.md` | Fixed-horizon outcome semantics |
| `docs/design/contracts/filter_evidence_contract.md` | pass/reject cohort persistence |
| AGENTS.md project root | Trust boundaries, live-trading rules, secrets discipline |

---

## 5. Remaining blockers on this session's PRs

### 5.1 Structural (block downstream work)

| # | Blocker | Impact | Owner |
|---|---|---|---|
| B1 | `admit_shadow_trial` returns `authenticated_owner_admission_unavailable` | No experiment can advance from shadow → paper_10. All this session's wiring is theoretical without this. | Requires authenticated owner-mutation route — outside Learning's scope. Change-controlled. |
| B2 | CI unavailable | Every merge this session was admin-bypass. Fast regression / secrets scan / integration failing at pipeline level. | Infra owner. |
| B3 | `paper_influence_paths` config allowlist empty by default | Even IF admit_shadow_trial worked, `_promotion_gate_reason` blocks auto-promotion with `path_not_allowlisted`. | Operator config decision after first shadow verdict lands. |

### 5.2 Small gaps not addressed in each PR

| PR | Small gap | Why deferred |
|---|---|---|
| #143 | 3 pre-existing master test failures pass through untouched: `test_r0_opportunity_id_flows_opportunity_to_decision`, `test_auto_tightened_confidence_recovers_when_ranker_starves_pi`, `test_reviewed_opportunity_roundtrips_through_market_pending_signal` | Verified unrelated via stash-and-run. Belong to separate defect fixes. |
| #145 | Stability window is unweighted — a cell that flipped hold→suspect→suspect→suspect is treated the same as suspect×3. | Kept simple deliberately; recency weighting is a future refinement if operators find it noisy. |
| #145 | `_is_fresh_prior_run` (existing) is not reused as a data-freshness gate for `compute_persistent_recommendations`. | Independent function, no need to couple. |
| #146 | Card location is Learning → Reports tab. Could also live in a dedicated Filter Controller admin panel. | Scope kept minimal per user request; a proper admin panel deferred. |
| #146 | No drill-down / expand behaviour — full reasoning shown inline, no click-through. | Persistent list is expected small (<10 entries typically). |
| #147 | Path 1 uses wall-clock position lifetime as `hold_seconds`. If a position is held for < 15m or > 24h, the cost economics won't map to any standard `filter_candidate_outcomes` horizon exactly. | The `settlement_lag_sec` field records the actual hold time so downstream consumers can filter. Fixed-horizon mapping is a separate concern. |
| #147 | Path 2 hardcodes 24h horizon (matching the settler contract). Multi-horizon backfill deferred. | The 24h path already covers `assess_candidate_outcome`'s cost-model gate. |
| #148 | Skeleton only — no override table, no outcome contract, no consumer, no auto-hypothesis generator. | By design — this is a naming/inventory change. The 8-stage sequence in the design doc governs the actual wiring, each stage its own PR. |
| #148 | `filter_parameter_tune` is marked `is_learning_treatment=False` so it stays out of `supported_paper_influence_path_ids()`. When wiring lands, that flag must flip together with `consumer_wired=True`. | Documented in the design doc §4 landing sequence. |
| #149 | The doc rewrite doesn't touch `docs/design/contracts/learning_evolution_contract.md` §12 claim tracker. | The contract is target-state; the audit doc's §8.1 is the current-state ledger. |

---

## 6. Carry-over PRs (not this session, need attention)

### PR #110 — `feat: add economic viability audit`

- Created 2026-07-16, last updated 2026-07-17 (stale)
- Substantial feature — economic viability audit machinery
- ALL CI checks failing (secrets scan, fast regression, integration, dashboard build, active paper chain, packaged runtime, etc.)
- **NOT auto-merged** this session — feature PR needs actual review, not just CI bypass
- Recommendation: read the diff, decide whether to close-as-stale or resurrect with a rebase

### PR #134 — `docs(sdd): align Opportunity lifecycle contracts`

- Created 2026-07-18, updated 2026-07-18
- Merge status: `CONFLICTING`
- Docs-only per title but conflicts against master (likely against subsequent doc changes)
- **NOT auto-merged** this session — needs rebase + conflict resolution to decide which version of the SDD narrative is canonical
- Recommendation: rebase, resolve, either merge or supersede with a fresh PR

---

## 7. Critical files this session touched

```
aletheia/agents/market_observer.py            + learning_candidate_key metadata write
aletheia/learning/cohorts.py                  + SUPPORTED_PATH_IDS expanded 2→6
aletheia/learning/registry.py                 + filter_parameter_tune skeleton entry
aletheia/analytics/filter_controller.py       + PersistentRecommendation + compute_persistent_recommendations
aletheia/analytics/shadow_trades.py           + _compute_shadow_economics + Path 1/2 enrichment
aletheia/storage/repositories.py              + resolve_shadow_trade_if_pending accepts optional economics
aletheia/config/schema.py                     + ControllerConfig.persistent_min_consecutive_runs / max_runs_lookback
frontend/src/api/client.ts                    + persistent_recommendations type
frontend/src/pages/Learning.tsx               + FilterControllerPersistentCard
docs/archive/legacy/plans/learning/evolution_module_overview.md     — full rewrite (current-state)
docs/archive/legacy/plans/learning/filter_parameter_tune_design.md  — new design blueprint
docs/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md — §8.1 added
tests/filter_eval/test_filter_controller_eval.py     + 9 stability tests
tests/test_shadow_trade_economics_enrichment.py      — new file, 5 tests
tests/specs/spec_learning_registry.py                + filter_parameter_tune assertions
```

---

## 8. Recommended next-session opening move

Given B1 is the single largest structural unblock: start the session by inspecting `aletheia/learning/lifecycle.py::admit_shadow_trial` and adjacent authority code, plus `aletheia/agent_runtime/permissions.py`. Decide whether the authenticated owner-mutation route is:
(a) already partially wired somewhere (a `owner_action_route` module? `POST /api/admin/*`?),
(b) requires a full new subsystem (session auth, ephemeral signing keys, request replay protection), or
(c) can be short-circuited with a config-only knob for interim testing.

Whichever it is, that decision unblocks 6 months of Learning verdict work.
