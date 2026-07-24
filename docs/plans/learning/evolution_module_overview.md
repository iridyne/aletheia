# Learning Module — Architecture Overview

> Reflects code state as of 2026-07-19, including in-flight PRs #143 / #145 / #146 / #147 / #148.
> Upstream contract: `docs/design/contracts/learning_evolution_contract.md` (L2 target).
> Boundary doctrine: `docs/design/subsystems/causal_counterfactual_research_guidance.md` (L2/L3/L4 evidence ladder).
> Historical audit: `docs/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md`.

---

## 1. Core role

Learning is Aletheia's **closed-loop behavior verification system**. It does not produce
trading decisions. Its single job is to answer:

> "Did injecting this experience actually improve trading outcomes?"

Every attempt to influence a downstream module (LLM prompt, ranker score, risk
constraint, universe multiplier, sizing baseline) MUST first pass a bounded A/B
experiment that produces a positive contribution verdict. Before the verdict lands,
every consumer receives the static baseline (empty string, zero delta, unchanged value).

## 2. Inputs — three material streams

| Source | Data produced | Producer |
|---|---|---|
| Trade outcomes | Fixed-horizon net-of-cost returns per candidate/opportunity | `filter_candidate_outcomes` + `universe_forward_outcomes` (settlers) |
| Knowledge artifacts | `trade_case_study`, `research_note`, `dreaming_log`, `trading_guard` | `dreaming_agent` → `knowledge_items` (FTS) |
| Structured lessons | `ps_lessons` + `ps_policy_versions` | `lesson_reviewer` |
| **Shadow trades (2026-07-19+)** | Cost-aware counterfactual returns for unselected candidates | `resolve_shadow_trades_for_position` (Path 1) + `resolve_stale_shadows` (Path 2), both now enriched with `estimate_outcome_economics` per PR #147 |

## 3. Outputs — 10 injection paths

| Path id | Consumer | Effect | Wired? | Current stage |
|---|---|---|---|---|
| `universe_family_calibration` | universe allocator | score multiplier | ✅ | shadow — 18k+ exposures, verdict layer active |
| `observer_advisory_bundle` | market_observer LLM | advisory prompt block | ✅ | shadow — 135+ exposures, verdict-eligible per P2 |
| `operator_learning_bundle` | System Operator LLM | Layer2/Layer3 prompt bundle | ✅ | shadow — 739+ exposures, `exact_candidate_scope` gate |
| `ranker_case_score` | paper ranker | score delta | ✅ | shadow — writes exposures as of P1 (PR #143) |
| `ranker_memo_score` | paper ranker | score delta | ✅ | shadow — same |
| `ranker_factor_filter` | paper ranker | filter/score | ✅ | shadow — factor-key scoped only |
| `ranker_learned_guard` | ranker / guards | candidate block | ✅ | shadow — 210+ exposures |
| `risk_lesson_constraint` | Risk Engine | proposal reject | ⚠️ | AI lessons gated; owner/manual/human rules bypass |
| `persona_sizing` | paper sizing policy | base notional | ✅ | shadow — sparse |
| **`filter_parameter_tune`** *(new, PR #148)* | *(none yet)* | filter parameter override | ❌ skeleton | not wired — see design doc |

**Read-only paths** (`is_learning_treatment=False`, listed for completeness):
`advisory_skill`, `exit_policy_advice`, `filter_ttl_shadow`, `operator_safety_control`, `trust_guard_observability`, `filter_parameter_tune` (skeleton).

**Today's live effect on trading**: zero. All authorized paths remain in `shadow`, so
`applied=False` on every exposure and every downstream consumer sees the static baseline.

## 4. Internal structure — 4 layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  A — Production (knowledge accumulation)                              │
│                                                                       │
│  dreaming_agent ───→ knowledge_items                                  │
│                       (trade_case_study / research_note /             │
│                        dreaming_log / trading_guard)                  │
│  lesson_reviewer ──→ ps_lessons + ps_policy_versions                  │
│  paper_trader     ──→ shadow_trades (top-N counterfactuals)           │
│                        cost-aware since PR #147                       │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────────┐
│  B — Experiment gating (A/B assignment layer)                         │
│                                                                       │
│  apply_boolean_treatment / apply_numeric_treatment                    │
│    ├── stable_assignment: deterministic arm from candidate_key        │
│    ├── resolve_learning_authority: check stage (shadow / paper_10+)   │
│    ├── record exposure → learning_exposures                           │
│    └── InfluenceDecision(value, applied, assignment, reason)          │
│           applied=True  → treatment arm, injection authorized         │
│           applied=False → baseline (shadow OR control arm)            │
│                                                                       │
│  P1 fix (PR #143): opportunity.metadata now carries                   │
│    learning_candidate_key so ranker paths record exposures            │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────────┐
│  C — Evaluation (verdict production)                                  │
│                                                                       │
│  learning_contribution_evaluator (scheduled)                          │
│    └── build_experiment_cohorts (filtered by SUPPORTED_PATH_IDS)      │
│          ├── join exposures ↔ outcomes via _load_exact_outcomes       │
│          │     filter_candidate_outcomes (family key + horizon)       │
│          │     universe_forward_outcomes (family key + horizon)       │
│          └── produce contribution_verdict                             │
│                {positive, insufficient_data, invalid}                 │
│                                                                       │
│  P2 fix (PR #143): SUPPORTED_PATH_IDS now covers 6 paths              │
│    (universe_family_calibration, operator_learning_bundle,            │
│     observer_advisory_bundle, ranker_case_score, ranker_memo_score,   │
│     ranker_factor_filter)                                             │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────────┐
│  D — Lifecycle (stage progression / refreeze)                         │
│                                                                       │
│  learning_lifecycle_controller (scheduled)                            │
│                                                                       │
│  shadow → paper_10 → paper_25 → paper_50 → paper_active               │
│                ↑                                     │                │
│                └── negative verdict → refreeze ─────┘                │
│                                                                       │
│  shadow→paper_10 is HARD MANUAL — admit_shadow_trial is fail-closed   │
│  stub. Auto-promotion within paper_10+ requires config allowlist.     │
└──────────────────────────────────────────────────────────────────────┘
```

## 5. Observational surface (2026-07-19)

**`filter_controller` — cross-run stability signals** (PR #145 + PR #146):

`compute_persistent_recommendations` walks the last N runs of `controller_recommendations`,
returns cells whose `evidence_verdict='overrestrictive_suspect'` has held across
`min_consecutive_runs` consecutive runs (default: ≥3 of last 10). Surfaced via:

- `GET /analytics/filter-controller` — new `persistent_recommendations` + `persistent_config` fields
- CLI: `ato opportunity audit --view controller`
- Frontend: Learning → Reports tab card

**Deliberately NOT auto-generating Learning hypotheses**: `filter_controller`'s signal
lives in a `(filter_name, scanner_family, direction, market_type, horizon)` space that
NO existing learning-treatment path can consume (the natural home would be a filter
parameter mutation; every current path either rejects candidates, scores them, or
injects prompt text). The path shape for this — `filter_parameter_tune` — is declared
as a skeleton by PR #148 with `consumer_wired=False, is_learning_treatment=False` until
the outcome contract, override table, and consumer wiring land as separate PRs.

## 6. Safety invariants

| Constraint | Location |
|---|---|
| All orders route via Execution Gateway | `aletheia/agent_runtime/permissions.py` |
| `FORBIDDEN_LAYERS` = risk_limits/execution_gateway/credentials/live_mode/order_submission | `aletheia/learning/skills.py` |
| Experiments cannot lower existing safety controls | `learning_evolution_contract.md §2` |
| `_curated_learning_context` forced to baseline within `dry_run_until` window | `agent_shell/trading_context.py` (GAP-15) |
| Harm gate can only tighten authority | `learning/authority.py` (GAP-14) |
| Negative-verdict refreeze independent of `lifecycle_automation_disabled` | `learning/lifecycle.py` (GAP-07) |
| filter_parameter_tune fail-closed by construction | `learning/registry.py` (PR #148) |
| Shadow-trade cost economics computed with same helper as filter_candidate_outcomes | PR #147 |

## 7. Current blockers

1. **Shadow lock (root cause)** — no experiment has reached `paper_10`. Path forward:
   `POST /api/settings/learning/experiments/{id}/admit-trial` → paper_10 → accumulate
   50 samples/arm × positive verdict → auto-promote.
2. **Owner admission prerequisites** — the protected owner-mutation route now
   exists, while the public `admit_shadow_trial` stub remains fail closed.
   Admission still requires a current identity-matched shadow verdict and every
   default-closed paper gate; until those runtime facts are true, no path can
   enter `paper_10`.
3. **exact_market_candidate scope** for `operator_learning_bundle` — multi-signal
   operator queues gate the bundle to disabled. Working as designed for experiment
   purity; may need broader-scope variant if data volume proves insufficient.

## 8. Repair history

| Batch | Contents | Date |
|---|---|---|
| G1–G4 contribution gap repair | timestamp bug / epoch churn / hardcoded path / shadow deadlock | 2026-07-19 |
| GAP-01–GAP-20 SDD-vs-reality repair | 20 gaps (GAP-13 architectural deferral) | 2026-07-19 |
| P1 wiring (PR #143) | market_observer writes `learning_candidate_key` to opportunity metadata | 2026-07-19 |
| P2 supported paths (PR #143) | `SUPPORTED_PATH_IDS` expanded 1 → 6 | 2026-07-19 |
| Persistent controller signals (PR #145) | Cross-run stability analyzer, HTTP/CLI surface | 2026-07-19 |
| Persistent signals UI (PR #146) | Learning → Reports card | 2026-07-19 |
| Shadow trade cost enrichment (PR #147) | Path 1 + Path 2 populate cost model fields | 2026-07-19 |
| filter_parameter_tune skeleton (PR #148) | Path declared + design doc; no consumer wiring | 2026-07-19 |

Full detail: `docs/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md`.

## 9. Design boundary (observational ↔ prospective)

`docs/design/subsystems/causal_counterfactual_research_guidance.md` defines the
5-level evidence ladder:

```
L0 integrity → L1 fixed-horizon outcome → L2 descriptive diagnostic
   → L3 bounded replay → L4 prospective experiment → L5 human governance
```

`filter_controller` produces L2 diagnostics. Learning A/B experiments live at L4.
The persistent-recommendation signal added by PR #145 is L2 output presented for
L5 (human) review — not a fast-lane into L4 auto-execution. This boundary is why
`filter_parameter_tune` remains a skeleton rather than auto-consuming
`filter_controller` output: automatic L2 → L4 promotion is a governance decision,
not a technical convenience.

## 10. Anticipated near-term work

1. **PR #146 land** → operators start monitoring persistent signals in the UI
2. **PR #147 land** → reflection.py + guard_effectiveness begin producing
   cost-aware signals from resolved shadow trades
3. **PR #148 land + follow-ups** — see `docs/plans/learning/filter_parameter_tune_design.md`
   for the 8-stage sequence before `filter_parameter_tune` becomes consumer-wired
4. **admit_shadow_trial real implementation** — the manual admission surface must
   exist before any shadow experiment can advance to paper_10. Outside Learning's
   scope; requires an authenticated owner-mutation route
