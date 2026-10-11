
# research/ - Evidence And Investigations

Research files are evidence, audits, and explorations. They can guide work but do not override contracts.

## Subfolders

| Folder | Use |
|---|---|
| `audits/` | Code, system, data-flow, platform, and lifecycle audits |
| `agents-learning/` | Agent behavior, OpenAlice/Serenity, learning-system research |
| `trading-strategy/` | Strategy, sizing, entry/exit, position research |
| `trading-theory/` | Falsifiable market theory, evidence grades, and AI feature candidates |
| `market-data/` | Market context, new data sources, xStocks support |
| `integrations/` | Demo/live parity and infrastructure integrations |
| `knowledge-bases/` | Imported or synthesized domain knowledge bases |
| `explorations/` | Smaller exploratory reports and postmortems |
| `tokenomics/` | LLM harness and cost-control research |
| `llm_harness/` | Dated inference-harness, context-adapter and token-observation evidence |
| `counterfactual/` | Shadow/counterfactual data audit, calibration and RDD findings (2026-07-31) |
| `universe-scoring/` | Universe continuous-scoring and relative-rank research (2026-08) |

## Current audit entry points

- [TSMOM trend on Binance perpetuals — 2026-10-11](trading-theory/15_tsmom_trend_results_2026-10-11.md)
  **fails** its pre-registration ([14](trading-theory/14_tsmom_trend_preregistration_2026-10-11.md)):
  in-sample Sharpe 0.89 replicates; holdout 2024–2026 Sharpe 0.59 but the bootstrap CI crosses zero and only the
  20-day lookback contributes (60/120-day ≈ 0). Drawdown half of BTC's; not evidence of edge; no live capital.
- [Forced-flow event study — 2026-10-10](trading-theory/13_forced_flow_event_study_results_2026-10-10.md)
  **fails** its pre-registration ([12](trading-theory/12_forced_flow_event_study_preregistration_2026-10-10.md)):
  resting a bid under extreme taker selling in 30 thin Binance perps loses net of 14 bp in every in-sample month
  (Apr–Aug 2026); only the up-drifting September holdout is positive; the liquidation-like OI-drop subset is worst.
- [Where AstraQuant and VergeX make money — 2026-10-09](trading-strategy/COMPETITOR_PNL_SOURCE_ASTRAQUANT_VERGEX_2026-10-09.md)
  finds no verifiable net-of-cost user profit for either; decomposes the only ledger (an owner-run AstraQuant demo,
  43 trades) into market beta, demo-price distortion and concentration; documents that platform revenue comes from
  referral/broker tags, builder fees and data resale, independent of user PnL; lists retail-scale edges that fit our
  product and a track-record checklist. Evidence only.
- [Five-pattern disconnection sweep — 2026-10-09](audits/FIVE_PATTERN_DISCONNECTION_SWEEP_2026-10-09.md)
  verifies 66 disconnections across six subsystems (orphaned producer/consumer, unused features, silent healthy
  fallbacks, untested-in-runtime paths), groups the fixable ones into 19 PR-sized batches, and lists the owner
  questions and three mechanical prevention gates. Frozen evidence; progress lives in GitHub.
- [Entry geometry phase-1 research (#3850) — 2026-10-09](trading-strategy/ENTRY_GEOMETRY_PHASE1_RESEARCH_2026-10-09.md)
  records how price-action schools, competitors and open-source frameworks place entry, stop and target. It
  includes a Fedora MAE/MFE study of the planner's hypotheses, three candidate geometry designs and an
  evaluation protocol. A critic reviewed it; it is unapproved evidence.
- [Selection alpha table — 2026-10-09](audits/SELECTION_ALPHA_TABLE_2026-10-09.md)
  is read-only Fedora evidence of net-of-cost 4h/15m/1h/24h outcomes by opportunity family, direction, regime
  and horizon for USDT perpetuals, pooled across every code version. No cell has a 95% CI lower bound above
  zero. It also records why `family_scorecard` and measured admission produce no usable result.
- [Opportunity lifecycle capability tracking — 2026-10-09](audits/OPPORTUNITY_LIFECYCLE_CAPABILITY_TRACKING_2026-10-09.md)
  is frozen read-only evidence of what each of the eleven lifecycle stages could and could not do on Fedora that day,
  plus a position-management chapter and end-to-end traces. Its gap list, owner-decision list and ordering live in
  the roadmap issue (#3883), not in the repository; capability definitions live in
  [CAPABILITY_MAP.md](../product/CAPABILITY_MAP.md).
- [Entry funnel 14-day reject attribution — 2026-10-09](audits/ENTRY_REJECT_ATTRIBUTION_14D_2026-10-09.md)
  attributes every planning generation on Fedora to one terminal reason, split by runtime period, then gives
  root causes and a tiered solution draft. The draft has been reviewed by an independent critic. It is
  unapproved discussion material.
- [Entry, stop, sizing and leverage research — 2026-10-09](trading-strategy/ENTRY_STOP_SIZING_LEVERAGE_RESEARCH_2026-10-09.md)
  compares how AstraQuant, VergeX, nofx, OpenAlice, nof1 Alpha Arena, open-source
  frameworks, retail bot platforms and prop-firm rules place stops, size positions
  and tier leverage. It also records Aletheia's own trade statistics, the 500 USDT
  Monte Carlo, and a draft definition of "best". It is unapproved evidence for
  the entry-order-risk-sizing proposal.
- [Slim Core private-I/O reachability — 2026-09-13](audits/SLIM_CORE_PRIVATE_IO_2026-09-13.md)
  distinguishes stored-account probes, candidate credentials, TP/SL recovery and
  public listing reads at the remaining concrete-client imports. It is bounded
  source evidence, not whole-product ownership or runtime qualification.
- [OKX order-book continuity audit — 2026-08-17](audits/OKX_ORDERBOOK_CONTINUITY_AUDIT_2026-08-17.md)
  records the #1002 repository-wide scope check against OKX's checksum
  deprecation and the active REST-only snapshot boundary.
- [Backend state, authority, and production-parity audit — 2026-09-06](audits/BACKEND_STATE_AUTHORITY_PRODUCTION_PARITY_AUDIT_2026-09-06.md)
  identifies the shared ownership failure behind weak lifecycle boundaries,
  distributed authority enforcement, and test/production semantic drift; it records
  evidence and dependency-ordered repair contracts without changing Runtime behavior.
- [Dual-mainline convergence plan — 2026-09-07](audits/DUAL_MAINLINE_CONVERGENCE_PLAN_2026-09-07.md)
  measures the red baseline #2945 left, disposes of four divergent branches against
  content evidence, and orders the ownership and dual-mainline work by lever rather
  than by list. It also records two findings that only appeared while executing it:
  two reported P1 defects were misdiagnosed (one would have blocked candidate
  selection if "fixed"), and closing a status vocabulary requires enumerating the
  values stored rows already hold — six were missing, each named by a read path.
- [Vergex platform analysis process evidence — 2026-08-11](trading-strategy/VERGEX_PLATFORM_ANALYSIS_PROCESS_2026-08-11.md)
  records the source-bundle inventory, safety boundary, capability decomposition,
  evidence classification, multi-temporal method, and algorithm-fidelity review.
- [Vergex platform gap and implementation candidate report — 2026-08-11](trading-strategy/VERGEX_PLATFORM_GAP_AND_IMPL_CANDIDATE_REPORT_2026-08-11.md)
  compares current Aletheia with the reusable Vergex ideas and presents
  unapproved repair priorities, feature candidates, metric benchmarks, and
  implementation decision gates for owner review.
- [Minimum Profit Loop Convergence Evidence — 2026-08-08](audits/MINIMUM_PROFIT_LOOP_CONVERGENCE_2026-08-08.md)
  records the merged PR chain and a dated Fedora checkpoint for the narrowed
  scan-to-Learning loop. It is historical evidence, not current Runtime truth.
- [TickFlow first-batch selection — 2026-08-09](explorations/TICKFLOW_FIRST_BATCH_SELECTION_2026-08-09.md)
  records the seven selected implementation boundaries and their acceptance evidence.
- [TickFlow first-batch implementation evidence — 2026-08-09](explorations/TICKFLOW_FIRST_BATCH_IMPLEMENTATION_EVIDENCE_2026-08-09.md)
  records the resulting design decisions, focused validation, observed bundle effect, and remaining ceilings.
- [TickFlow remaining merge candidate catalog — 2026-08-09](explorations/TICKFLOW_REMAINING_MERGE_CANDIDATE_CATALOG_2026-08-09.md)
  classifies 60 post-first-batch candidates by domain, cost, dependency, and merge readiness.
- [TickFlow Wave A implementation evidence — 2026-08-09](explorations/TICKFLOW_WAVE_A_IMPLEMENTATION_EVIDENCE_2026-08-09.md)
  records the nine implemented small boundaries, schema-v15 retry identity, focused verification.
- [TickFlow Wave B-01 semantic data-state evidence — 2026-08-09](explorations/TICKFLOW_WAVE_B_01_SEMANTIC_DATA_STATE_EVIDENCE_2026-08-09.md)
  records the shared dashboard presentation vocabulary and the first explicit DataTable consumer.
- [TickFlow Wave B-01 retrospective contract review — 2026-08-10](explorations/TICKFLOW_WAVE_B_01_RETROSPECTIVE_CONTRACT_REVIEW_2026-08-10.md)
  reconstructs the missed owner-review gate and records the owner-approved narrow outcome.
- [TickFlow Wave B-03 dataset readiness evidence — 2026-08-10](explorations/TICKFLOW_WAVE_B_03_DATASET_READINESS_EVIDENCE_2026-08-10.md)
  records exact OHLCV coverage boundary and defers generic publication completeness.
- [TickFlow Wave B-04 Serenity attempt truth evidence — 2026-08-10](explorations/TICKFLOW_WAVE_B_04_SERENITY_ATTEMPT_TRUTH_EVIDENCE_2026-08-10.md)
  records cooperative cancellation acknowledgement, guarded terminal writes and restart reconciliation.

## Agent Rule

Use research to find evidence and hypotheses. If a conclusion becomes authoritative, promote it into `design/`, `product/`, `governance/`, or `operations/`.
