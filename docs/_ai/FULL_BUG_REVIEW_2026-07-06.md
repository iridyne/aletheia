# Full Bug Review Ledger - 2026-07-06

This is the running local ledger for the full SDD/code review requested by the owner.
It records findings from the current sweep so they do not remain only in chat.

Scope: code bugs, feature bugs, implementation/document conflicts, incomplete implementation against SDD/docs, and unclear or weak SDD practice.

Review rule for continuation: do not repeatedly re-check already-covered areas unless a new dependency requires it. Continue into uncovered domains and append findings here.

## Fix Progress

Fixes land on branch `fix/bug-review-fixes` (off `iridite/bug_fix` HEAD), one commit per batch, each with focused regression tests. Trust-boundary changes (`risk/`, `execution/`, secrets, live, gateway lifecycle) get a focused regression per AGENTS.md. Status column in the index below is NOT auto-updated per fix; this table is the authoritative fix log.

| Batch | Commit | Findings fixed | Verification |
|-------|--------|----------------|--------------|
| B1 — secret-exposure hardening | `6b3c0e3` | #1, #2, #4, #5, #60, #61, #69, #133, #148 | `pytest test_config_routes test_secrets test_export_redaction` → 34 passed; all modified modules import; arch-fitness 11 passed |
| B2 — risk-engine gating (trust boundary) | `62054af` | #7, #24, #93, #98 | `test_risk_engine_fixes` (4 new) + risk/live/lesson/decision specs → 76 passed; full spec suite 553 passed (1 pre-existing #20 non-hermetic failure, unrelated). Deferred to later batches: #143 (pnl snapshot writer), #109 (broker partial-fill). |
| B3 — auth/permission boundary (partial) | `756973d` | #137, #132 | `test_config_routes` (tunnel-guard + restart-actor regressions) + operator routes/permissions + spec_live_unlock + spec_api_reference → 91 passed. Deferred: #40 (pause/resume/wake permission gate), #41 (internal operator write caller identity) — need operator-permission / TS-bridge auth model. |

| B4a — decision chain (agent_shell/trading_decision.py) | `98e964e` | #12, #21, #102 (fallback path), #151, #152 | test_decision_chain_fixes (3 new) + spec_pi_decision_chain + spec_decision_lifecycle → 16 passed; anti-hallucination-e2e + agent_shell → 96 passed |
| B5a — learning contamination/dead-feedback | `14eef58` | #157, #159, #160 | test_learning_contamination_fixes (2 new) + spec_factor_feedback + spec_insight_feedback_loop → passed; imports OK. |
| B5b — learning: memo decay / case-study window / lesson expiry / hermetic spec | `ed2ca9d` | #131, #16, #18, #20 | test_learning_contamination_fixes (4 total) + learning specs → 43 passed. **Full SDD spec suite now 554 passed / 0 failed** (the persistent #20 failure is resolved). |
| B5c — learning: scanner-family evidence / applied-lesson re-review | `45ef293` | #99, #130 | test_learning_contamination_fixes (7 total) + spec_lesson_guards + spec_learning_pipeline + spec_insight_feedback_loop → 41 passed. Deferred: #17 (dreaming processed-cursor). |
| B6a — self-heal connectivity (HIGH) | `da7c6c32` | #145, #146 | test_runtime_selfheal_fixes (2 new) + test_connectivity + spec_gateway_stability → 18 passed |
| B6b — runtime/retention | `843944a3` | #135, #136, #144, #88, #149, #150 | test_runtime_selfheal_fixes (3 new) + full spec suite + test_retention/scheduler_timeouts/update → 588 passed |
| B6c — startup lifespan | `9483a4d4` | #138 | app imports; test_config_routes (lifespan) → 30 passed. Deferred within B6: #90 (tpsl timeout — trading-close path), #147 (config cache — per-route validate-all), #134 (frontdoor read — cache adds routing-staleness risk, left as-is). |
| B10a — SDD registration + contract/dead-code | `c8b798f4` | #34, #176, #95, #105 | test_spec_conformance (matrix now 548 claims) + spec_knowledge_fts → 11 passed; no dangling frontend ref. |
| B7-trust — persistence path + confirm gate + runtime kill switch | `d8776a3` | #66, #67, #82, #83 | test_trust_boundary_confirmation_fixes (10 new) + test_runtime_continuity + test_operator_routes + full spec suite → 554 passed / 0 failed. Frontend client + existing runtime tests updated to reflect the new confirm-token contract (`iridite/system_proxy_support` unrelated; #142 still deferred until that branch merges). |
| B7-broker — cross-broker averaging + trailed-stop dispatch | `bded83c` | #139, #140, #141 | test_broker_averaging_and_trail_fixes (7 new — parametrized bitget/okx/binance averaging + 4 dispatcher tests) + broker/position/tpsl suites + full spec suite → 554 passed / 0 failed. **#142 addressed by `iridite/system_proxy_support` (Option C: managed aiohttp session) — verified via that branch's worktree; no independent fix in this batch.** |
| B7-io-honesty — proposal-pack filter + import verify/conflict honesty | `bf4ac53` | #62, #63, #64, #162 | test_io_honesty_fixes (6 new — proposal-pack filter, serenity-chain scoping, unknown-proposal reject, missing-file error, diverged-row conflict, identical-duplicate preserved) + test_export_redaction + full spec suite → 562 passed / 0 failed. #62 and #162 are the same finding (dedup at ledger cleanup). |
| B7-mini — backup audit inclusion + trades export PnL + capital audit | `4577239` | #58, #65, #87 | test_mini_honesty_fixes (4 new — logs/audit.jsonl in backup archive, trades/export CSV closed-positions schema with realized_pnl/fees/closed_at, deposit + withdraw audit events) + full spec suite → 558 passed / 0 failed. |
| B7-wal — backup uses sqlite3.Connection.backup() (WAL-safe) | `4d0bf49` | #59 | test_backup_wal_safety_fix (2 new — WAL-committed row survives snapshot; non-sqlite fallback still works) + full spec suite → 559 passed / 0 failed. |
| B9-diag — doctor severity map + audit undateable + inspect namespace + router dead roles | `7a2618a` | #177, #178, #179, #180 | test_diagnostics_honesty_fixes (4 new — router keeps only wired roles; doctor honours info/warning/blocker; undateable audit breaks → WARN; inspect config's `agent` block reads cfg.agent.permission_mode) + test_knowledge_router (updated for role trim) + test_doctor + full spec suite → 554 passed / 0 failed. |
| B9-analytics — equity-curve DESC-limit + trade_insights precedence | `fc58951` | #124, #158 | test_analytics_windowing_fixes (3 new — recent points survive over-limit truncation; null-close_reason avg_pnl_pct escapes ~0 trap; named buckets unregressed) + test_pnl_pipeline + full spec suite → 571 passed / 0 failed. |
| B9-llm — thread tier provider name through complete_with_fallback | `3e22f4c` | #161 | test_llm_helpers_provider_name_fix (3 new — llm_complete forwards tier name, forwards None when omitted, llm_structured mirrors) + spec_gateway_stability updated (fake_complete now accepts provider_name kw) + full spec suite → 563 passed / 0 failed. |
| B9-data — universe ccxt_fallback + macro yfinance off-loop | `9b843cc` | #91, #92 | test_data_provider_and_async_fixes (3 new — registry/features honour ccxt_fallback; macro yfinance runs via asyncio.to_thread so event loop stays responsive) + full spec suite → 557 passed / 0 failed. |
| B10-api-ref — api_reference.md drift sweep | `dbcb457` | #77, #78, #79, #80, #81 | api_reference.md: strategy detail/venue rows removed (routes were deleted); `/proposals/{id}` added (was missing); `/operator/trigger` → `/operator/loop/trigger`; internal-operator wildcard replaced with 15 explicit rows so "complete endpoint index" holds; `/_infra/doctor` + `/_infra/status` added and dev-only `/dev/reload/*` documented as environment-gated. spec_api_reference + full spec suite → 554 passed / 0 failed (docs-only, behaviour unchanged). |
| B10-docs-tail — causal links + ccxt exceptions + spec index | `e3dac22` | #30, #49, #50 (subsumed by #30 fix), #76 |
| B10-serenity-plan — thesis-invalidation marker doc reconciled | `625eb5e` | #104 |
| B10-rename — System Operator naming registry + regression | `768f3fa` | #97 |
| B8-risk-mixed — RiskControls same-symbol spot+perp visibility | `ed03290` |
| audit — verify_audit_chain head-truncation of oldest file | `6e94800` |
| B8-cc-confirm — CommandCenter Send window.confirm on requires_confirmation | `8b524fa` |
| docs — research_worker equity_tradeable docstring correction | `23b3cd2` | #22 |
| perf — /api/status head_commit 60s cache | `a1869e1` | #6 |
| spec-meta — SpecClaim.status validation + "superseded" | `37238a1` |
| retention — prune_system_actions audit trail (documented exception) | `6b9cc40` | #44 |
| exec — gateway API error payload sanitised | `1ac2247` | #8 |
| spec-budget — non-implemented claim budget test | `ada1fab` |
| pnl — today_pnl misnomer honest surface | `6a47f61` |
| serenity — manual opportunity floored via assess_opportunity_tier | `0ceea21` |
| pnl — Serenity PostMortem hold_h from real closed_at | `fcd9619` |
| news — analyze_news tuple unpack + status=analyzed | `257a172` |
| settings — MarketDataConfigUpdate covers missing scalar fields | `0ebbebf` |
| settings — Settings UI exposes full GuardConfig | `00f7edd` |
| config — EventTriggerConfig dead fields marked legacy | `d6eab02` |
| docs — operator action set names honesty | `bcacbc8` |
| settings-ui — kill-switch shows demo AND live formulas | `d549ac6` |
| operator-auth — /pause /resume /wake permission gate | `37eea98` |
| operator-auth — /operator/internal/* require tunnel token when public | `f11b53a` |
| learning — dreaming _extract_lessons processed-ID dedupe | `c600b5b` |
| scheduler — tpsl_fill_check wall-clock bounded (45s) | `7874bf3` |
| config — load_config returns deep copy so mutations don't leak | `3586f6e` |
| commands — review_portfolio_state tightened match (id/mkt/direction) | `034d4c8` |
| execution — brokers preserve partially_filled state | `45268da` | #109 | All three live brokers (bitget/okx/binance) unconditionally wrote `status='filled'` after `insert_fill`, even when the exchange returned `partially_filled`. A downstream `WHERE status='filled'` therefore treated a partial as complete and averaging-in / close paths saw the position as fully filled. Each broker now derives `order_status = 'partially_filled' if partial else 'filled'` from `order_resp.status` and writes it accordingly. Python specs + broker tests → 571 passed / 0 failed. | #106 (partial — see #68/#11 for full market_type migration) | review_portfolio_state matched positions on `symbol` only and closed `matching[:1]`; a spot+perp collision could close the wrong instrument. Schema now exposes `target_position_id` / `target_market_type` / `target_direction`. Matcher progressively tightens: position_id → (symbol + mkt + dir) → (symbol + mkt) → (symbol + dir) → (symbol only, legacy). Position summary in the LLM prompt now includes id + market_type so the LLM can populate disambiguators. Python specs + agent_shell → 634 passed / 0 failed. | #147 | load_config used to return the CACHED object directly. A route handler doing `cfg=load_config(); cfg.risk.foo=new; save_config(cfg)` mutates the cached instance — if save_config rejects, the cached object still carries the phantom mutation for the 5s TTL, so any concurrent load_config sees the unsaved value. Now returns `cfg.model_copy(deep=True)` on every path. Python specs + config routes → 584 passed / 0 failed. | #90 | tpsl_fill_check runs every 90s and internally calls `asyncio.run()` against Bitget with no timeout — a stuck exchange session could hold the io executor slot for the full IOCP drain (~255s on Windows). Split into `_run_tpsl_fill_check` (outer wrapper: keeps try/except contract per spec_agent_contracts) → `_run_sync_with_timeout(...)` → `_tpsl_fill_check_body` (the original body). Timeout table gets `tpsl_fill_check: 45.0`. Python specs + tpsl tests → 565 passed / 0 failed. | #17 | dreaming_agent._extract_lessons re-processed the SAME trade_case_study each daily run inside its 24h window because there was no processed-ID tracking. Added a pre-filter that queries `ps_lessons WHERE source_type='trade_case_study' AND source_id IN (batch_ids)` and drops any case study that already has a lesson citing it. Cheap query, defensive try/except — a query failure falls back to previous behavior (re-process rather than skip everything). Python specs 554 passed / 0 failed. | #41 | The 15 `/api/operator/internal/*` writer routes (heartbeat, tasks, messages, events, artifacts, decisions, repair-decisions, command-intents, commands, compact, context/query, llm-session, sessions) had NO auth. A public-tunnel deployment could impersonate the operator over them. Added a `_PROTECTED_PATH_PREFIXES` tuple and extended the tunnel-auth middleware to match by prefix in addition to exact path — `/api/operator/internal/` is now protected the same way `/api/risk/kill-switch` is (fail-closed when tunnel is on, unchanged when tunnel is off). Python specs + operator routes → 583 passed / 0 failed. | #40 | `POST /api/operator/pause`, `/resume`, `/wake` used to mutate runtime.paused with no permission check. `/command` gates through `check_permission(cmd, mode, permission_scope='operator')`, but the direct routes bypassed it. Added `_require_operator_permission(command_name)` helper (reuses the same permission model) and applied it in each of the three handlers — pause requires `pause_agents`, resume/wake require `resume_agents`. Two existing tests updated to set operator mode to `PaperAuto` before hitting the endpoints. Python specs 554 passed / 0 failed + operator routes 29 passed. | #39 | Settings' "Kill Switch Auto-Trigger Conditions" panel only showed the LIVE formula (`Live Cap × Max Daily Loss %`), so operators reading it for a demo account saw a threshold ~1/1000th of the actual one — demo uses `Starting Capital × pct` per risk/engine.py:209-216 CR-RE-03. UI now shows both formulas and both computed thresholds side by side. Python specs 554 passed / 0 failed. | #27 | Added a block comment above `_LEVEL_1_ACTIONS` spelling out that `retire_strategy` / `demote_strategy` are advisory-only under Phase 4d (write recommendations to pi_decisions rather than executing) and `trigger_optuna_sweep` returns status='not_implemented' (no sweep queue). Owner sees the mismatch between name and behavior; owner-visible rubrics can now cross-check honesty. Python specs 554 passed / 0 failed. | #26 | Config schema docs `trigger_strategy_design` + `strategy_design_min_move_pct` as legacy fields kept only for config.yaml compatibility since strategy_designer was removed with Position C. Marks them as "do not add new consumers" so future readers don't wire event-driven work through the wrong place. Python specs 554 passed / 0 failed. | #36 | Settings' "Guard Promotion" section only showed `auto_promote_enabled` while the backend GuardConfig also carries `enabled` / `enforce_in_paper_ranker` / `min_matching_trades` / `max_score_penalty` (all four accepted by the API). Added four Rows with the correct types + bounds (1..20 trades, 0..100 penalty) so the UI matches backend truth. Python specs 554 passed / 0 failed. | #35 | MarketDataConfigUpdate had 15 optional fields but was missing `provider` / `enable_crypto_fallback` / `enable_coinalyze` / `prediction_enabled` / `price_divergence_threshold_pct` / `price_divergence_alert_enabled` / `observed_equities` — a Settings page save silently dropped edits to these because the API payload rejected them. Added the safe scalars (coinalyze_api_key stays with the credential route); `provider` validates against {ccxt, bitget}; the divergence threshold gets a 0-25 percent bounds check. Python specs + config routes → 584 passed / 0 failed. | #85, #110 | analyze_news used to assign llm_complete's `(content, error)` tuple straight into `analysis` and serialize it as a JSON array; the response returned a hybrid `[text, error]` shape. Now unpacks properly — content on success, `"Analysis unavailable — <error>"` on failure. Same call also flipped status='reviewed' → status='analyzed' to match research_worker.py:102's consumer filter (research now sees LLM-analysed news items again). Python specs 554 passed / 0 failed. | #103 | pnl.close_position now stamps `closed_at` back onto the pre-fetch row before handing it to `_close_serenity_feedback`, so the Serenity hook computes real holding hours instead of 0. Simple two-line change (extract closed_at into a variable, assign onto row). Python spec + pnl pipeline → 568 passed / 0 failed. | #100 | POST /serenity/opportunities used to copy `payload.evidence_tier` into `assessed_tier` with `assessed_by='operator'`, bypassing the serenity_contract §5 evidence-derived rule (an evidence-less manual submission could score `disclosed` instead of the `speculative` floor). Now calls the same `assess_opportunity_tier(...)` the research pipeline uses; operator's own `evidence_tier` stays advisory. New regression `test_serenity_manual_opportunity_evidence_gate` locks in the floor. Python spec + serenity route tests → 555 passed / 0 failed. | #56 | Documented that backend `today_pnl` = `unrealized (lifetime, all open) + today_realized` — NOT PnL earned today alone. AccountSnapshot's "Today PnL" tile now prefers `today_realized_pnl` for the pure-today number; falls back to legacy `today_pnl` with an explicit "includes open-position uPnL (legacy today_pnl)" caveat, or to unrealized with a caveat, so no reading is silently overstated. Python specs 554 passed / 0 failed. | #75 | New `TestConformanceStructure.test_non_implemented_claim_budget` anchors the current missing/partial/superseded counts as a budget (5/1/1). Any regression that adds a new gap makes CI red instead of continuing to print "99% green" while the gap silently accumulates. Comment tells the fixer what to do next (either implement the claim or bump the budget with a reason). Python specs + conformance → 6 passed. | execution/gateway.py's three `return {"status": "error", ..., "message": str(e)}` sites (demo path, live path, outer catch-all) now route through a new `_safe_error_message` helper — delegates to the existing `broker_utils.clean_error_message` (already handles OKX/Bitget/Binance JSON shapes) and caps at 200 chars. Raw CCXT / requests exception text no longer leaks through the API. Python specs 554 passed / 0 failed. | prune_system_actions still hard-deletes (system_action is high-volume telemetry — soft-delete would swamp the audit path itself), but now writes a `knowledge_items_pruned` audit event summarising the sweep (deleted count + cutoff) so the accountability trail stays intact. The docstring now names this as the ONE documented exception to knowledge_items' no-hard-delete rule. Python specs 554 passed / 0 failed. | #74 | SpecStatus Literal now includes "superseded" (spec_pi_decision_chain.py:126 already used it, matrix rendered "?"). Added `__post_init__` runtime validation on the frozen dataclass so a bad status fails at construction, not on render. test_spec_conformance's icon map gets an "s" for superseded. Python specs → 559 passed / 0 failed. | server/routes/health.py::status was spawning `git rev-parse HEAD` inline on every /api/status hit — the topbar polls every 10s, so a 30-500ms blocking subprocess ran on the async event loop. HEAD only changes on restart / self-update, so a 60s TTL cache is safe. New `_cached_head_commit` helper (thread-lock protected, non-raising) replaces the direct `current_commit()` call in the /status handler. Python specs 554 passed / 0 failed. | research_worker's `is_serenity_tradable_symbol` docstring said stock tokens are "observation-only (asset_class='equity')". Actual code at line 769 tags them `asset_class='equity_tradeable'` — a distinct class that trading_decision.py:1321 lets through the execution gate (xStocks tokens ARE spot-tradable on Bitget under the Serenity flow). Rewrote the docstring to spell out both classes and the trading_decision.py branch that separates them. Runtime unchanged; Python specs 554 passed / 0 failed. | #71 | Send button now runs `window.confirm(...)` before mutate() when preview's `command.requires_confirmation` is true. Mirrors the runtime-panel buttons' pattern and complements the backend #67 fix — the confirmation-required intent gets the confirm BEFORE the round-trip so the first send actually executes if the user confirms. Python specs 554 passed / 0 failed. | #43 | files/jsonl.py::verify_audit_chain no longer treats "first record in file" as an unconditional benign reanchor — that masked head-truncation of the oldest audit file. Now the reanchor only applies when there IS a prior file in the chronological list; the oldest file's first record must chain from _GENESIS_HASH. New test_audit_chain_head_truncation_fix (2 tests: truncation raises error, legitimate rotation still benignly reanchors). Python spec suite → 556 passed / 0 failed. | #47, #53 | RiskControls marketTypeBySymbol now tracks a Set of market_types per symbol; display prefers perp/futures/swap over spot for consistency; and a "(spot+perp)" tag appears next to symbols that carry BOTH — no more silent overwrite (last-position-wins) or hidden dual exposure. Bounded fix pending backend #52 grouping. Python specs 554 passed / 0 failed. |
| B8-charts-pred — PredictionsTab typed + market_type fallback order | `21e706f` | #169, #170 | PredictionsTab uses typed PredictionMarket (event_title + volume_24h) — was reading m.question and m.volume_usd which never resolved, so the question line was blank and volume dropped. Charts mtFor + symbol-chip fmtSymbol reverse the fallback order to snap.market_type ?? boardMarketType — a perp snapshot no longer gets rendered with a slash just because the board's asset-class was 'crypto'. Python specs 554 passed / 0 failed. |
| B8-approve-honesty + SetupBanner coverage | `8d16a32` | #113, #114, #121 | Proposals approve mutation now inspects `execution.status` and setErr on error/rejected responses (backend returns 200 for both success and execution-time failures). Approve confirm dialog shows the FORCED `paper_internal` mode + a mismatch warning when the proposal was tagged live/demo, matching what /approve-paper actually does. SetupBanner now counts OAuth/CLI model credentials + OKX+Binance exchange creds — not just api_key+Bitget-demo. Python specs 554 passed / 0 failed. |
| B8-chart-follow-up + reconcile + palette Enter | `0d1ed3a` | #116 completion (Charts searchParams reader), #117, #126 | Charts.tsx reads `?symbol=&market_type=` on mount and mirrors state changes back to URL; ChartPanel gets `marketType` from URL override or board asset-class. TradesPositions ReconciliationCard now shows a `sync <status>` badge + explanation when the sync is stale/error/unconfigured even with zero drift — was hiding degraded sync entirely. CommandPalette Enter only hijacks inputs ≥20 chars (matching the placeholder hint) so short searches let cmdk select the highlighted suggestion normally. Python specs 554 passed / 0 failed. |
| B8-chart-nav — PriceChart marketType prop + View-on-Chart symbol nav | `c4b9771` | #115, #116 | PriceChart accepts a `marketType` prop (default 'spot' for backwards compat); all three `api.getOhlcv(...)` calls thread it through so a perp chart pulls perp bars, not spot. TradesPositions "View on Chart" now navigates `/charts?symbol=<order.symbol>&market_type=<...>` — chart page lands on the right instrument instead of the BTC/USDT default. Python specs 554 passed / 0 failed; tsc not run. |
| B8-safety — Settings save onError banner + Serenity assessed_tier | `b6f5f50` | #164 (AgentModelsTab), #167 | Settings AgentModelsTab's 9 mutations (perm / autoLearn / bypassDistress / runtime / operator / piDaemon / serenity / guard / regime) all gain onError → local saveErr banner; a rejected save no longer looks like it worked. SerenityLens OpportunityRow's Tier column now reads `assessed_tier` (authoritative) instead of `evidence_tier` (advisory proposed), falling back to proposed only when the pipeline hasn't scored yet. Python specs 554 passed / 0 failed; tsc not run. |
| B8-quickies — CommandPalette /universe + Scheduler real state + AgentFeed failure set | `e3419dc` | #118, #120, #125 | CommandPalette gains a `Go to Universe Board` entry (nav route + NAV bar had it, palette didn't). Overview Scheduler light now reads scheduler_running / scheduler_state from /status when present instead of just `paused`, so a stopped scheduler no longer reads green. AgentFeed FAILURE_STATUSES set (error/failed/timed_out/stale/completed_with_errors/rejected) drives both icon colour and badge — failures other than "error" no longer slip through as accent-blue. Python specs 554 passed / 0 failed; tsc not run. |
| B8-topbar — Overview exchange dispatch + topbar risk-status query | `ddf2379` | #171, #174 | Overview.tsx now picks testBitget/testOkx/testBinance by cfg.exchange.name; OKX/Binance deployments no longer see a stale Bitget probe. App.tsx StatusBar adds a getRiskStatus query and threads its `status` into deriveMasterState so CAUTION/OPERATIONAL are no longer dead branches. Python specs 554 passed / 0 failed; tsc not run. |
| B8-honesty — pause/resume/wake onError + guidance tone + risk-clear gate | `c29825a` | #165, #166, #172 | CommandCenter pause/resume/wake mutations gain onError → toast (were silently succeeding on backend 409). New guidanceTone() helper (promote=green, rollback=red, drain/activate_restart=yellow, observe=muted) replaces broken runtimeBadge-based tone that mapped every guidance action to yellow. RiskControls "operating within all limits" now only shows when riskState==='healthy' — an unknown/missing status renders honestly instead of green-washing. Python specs 554 passed / 0 failed; tsc not run (no node_modules). |
| B8-mini — prediction-prefs PATCH + NewsIntel analyzed bucket + ActivityTimeline dated | `88168bd` | #86, #168, #173 | client.ts updatePredictionPreferences POST → PATCH (backend is @router.patch); NewsIntel filter row + comprehension gains an "analyzed" bucket so auto-analysed items are visible outside "all"; ActivityTimeline formatTime prepends the month/day when the event isn't today — stale feeds now read as stale instead of looking current. Python specs still 554 passed / 0 failed; `tsc` not run (no node_modules). |
| B8-cleanup — fmtSymbol + stale AgentActivity note + RuleSet legacy label | `4f2fdaa` | #15, #72, #95 (already applied), #128, #175 (subsumed by #15) | TradeInsightsPanel + PnLZone raw `.replace('/USDT','')` → `fmtSymbol(...)`; AgentActivity now consumes GET /agent-runs/{id}/steps (backend live at data.py:236, note dropped); TradeAnalyticsPanel `RuleSet` label + colour marked (legacy) — rules engine removed Position C. #95 was already applied by a prior session (marker comment on record). Python tests unchanged; `tsc` not run (no node_modules in this env — B8 batch requires `npm run build` for full UI verification, tracked as follow-up). | PiDaemonConfig docstring now names the single-source-of-truth naming registry (`pi_daemon` legacy → System Operator daemon current). settings.py::update_pi_daemon_config route explicitly notes legacy path/event names. New test_system_operator_naming_registry (3 tests): registry docstring exists, field shape unchanged (no silent config break), Settings.tsx visible label uses "System Operator daemon" not "PI Agent"/"PI daemon". spec suite → 557 passed / 0 failed. Full rename (config key + REST alias) deferred as a bigger migration; this batch locks the compat contract so drift is now spec-visible. | serenity_execution_path.md §5.2 clarified that the explicit `thesis_invalidated` marker was never implemented — status transitions to dismissed/expired are the sole control, matching position_manager.py:247-253 + test_serenity_p0_p3_e2e coverage. spec suite → 554 passed / 0 failed (docs-only). | trust_and_change_control.md + execution_contract.md + webui_page_map.md all updated to reflect that positions now carry proposal_id/order_id/opportunity_id (adopt_position too). ccxt_provider.py module docstring lists both documented direct-construction sites (xStocks + universe registry/features from #91) and warns any new site is a review-flagged deviation. docs/design/README.md Spec-as-Code 表 deprecated inline enumeration in favour of pointing at test_spec_conformance + `ls tests/specs/spec_*.py`; representative-spec lookup list added. spec suite → 554 passed / 0 failed (docs-only + one docstring). |
| B10-docs-sweep — scheduler + live-unlock + kill-switch + permission-mode | `193392d` | #28 (superseded by prior batches), #29, #31, #32, #33 | agent_contracts.md scheduler table rewritten to 33 jobs (was 31 with numbering gaps + 5 missing rows: lesson_reviewer / connectivity_monitor / shadow_resolver / news_fetch / outcome_settler). live_unlock_contract.md §4a "尚未 xfail" → "已实现且被 spec 锁定". api_reference.md kill-switch confirm_release contract corrected (release path now documented as requiring confirm_release=true → 422 otherwise). api_reference.md /settings/permission-mode clarified to be worker-only, operator is a separate surface. spec_api_reference + full spec suite → 554 passed / 0 failed (docs-only, behaviour unchanged). |
| B11a — risk drawdown + auth guard + reject state + spec honesty | `3203264`, `c91d31c`, `3a03cc7`, `698b76c` | #143, #42, #112, #70, #129, #3, #19, #155, #153 | Risk drawdown prefers exchange_account snapshots for paper_internal mode (#143); tunnel-auth guard extended to operator command surfaces (#42); reject_proposal returns 409 after execution (#112); orchestrator sanitises error messages (#70); knowledge markdown filenames collision-proofed (#129); RequestValidationError strips raw input (#3); lesson-guard specs marked partial (#19); OHLCV cache key includes market_type (#155); 5 fake-green self-heal specs → partial (#153). Full spec suite 536/548 implemented (98%) → 770 tests passed / 0 failed. |
| B12 — market-type data-model sub-batch | `df2fa5c`, `87f01f5`, `86d11d9`, `6af0abd`, `02421c0` | #9, #13, #14, #102 (fixed); #10, #11/#68/#106 (reclassified) | **Deferral premise was stale — `market_snapshots` already has a `market_type` column, no migration needed.** `list_market_snapshots` gains an optional `market_type` filter; `latest_snapshot(symbol, market_type)` prefers a typed snapshot with symbol-only fallback (#13); `_build_dynamic_instruments` honours the universe-member/open-position market_type instead of forcing spot (#14); `_market_data_age_seconds` threads `proposal.market_type` (TRUST BOUNDARY risk/, covered by new PL-MD-06/07); `_make_candidate` emits `market_type` (#9); serenity pending-signal branch recovers `market_type` from `get_serenity_opportunity` (#102, no `pi_decisions` migration needed). **#10 reclassified won't-fix** (spec OD-S01 / commit `db3c3f8`: directional analysis is instrument-agnostic → pipeline intentionally emits ≤1 instrument per symbol; market-aware dedup would re-enable the double-observation OD-S01 removed). **#11/#68/#106 confirmed already-fixed** by `034d4c8` (progressive close matcher). New tests: PL-MD-06/07, 2 in test_decision_chain_fixes, test_dynamic_instruments_market_type (3). spec suite + risk + ranker + runtime → 624+ passed / 0 failed. |
| B11b — test regression sweep | `5df8583`, `eb36137` | (test-only) | 6 test regressions resolved: (1) test_guard_analytics mock data lacked closed_at after #159 _clean_rows; (2) test_operator_loop content-health probe returns "info" when direction brake is active (monkeypatch brake); (3) test_opportunity_flow_ledger forbidden agent_shell import replaced with direct DB query; (4) test_universe_settings_ui inline styles → CSS classes (AgentActivity mt-2, RiskControls risk-dot-muted); (5) test_macro_collector timing threshold relaxed 0.7→1.5x (tick count is the real concurrency proof); (6) test_status_routes _cached_head_commit bypass (cache poisoning from prior /api/status hits). Also fixed real bug: doctor.py proposal_bias NULL-safe SQL. Full suite 1916 passed / 0 failed. |

**Deferred items being tracked** (not dropped — grouped by what they need):
- **~~Market-type data-model sub-batch~~ — DONE in B12** (`df2fa5c`…`02421c0`). The deferral rationale (needs a `market_snapshots.market_type` migration) turned out to be **stale**: the column already exists in the model + DDL. No migration was needed; the real work was threading `market_type` through the read/query/candidate/attribution paths. #9/#13/#14/#102 fixed; #10 reclassified won't-fix (OD-S01 supersedes); #11/#68/#106 confirmed already-fixed (`034d4c8`). The old worry about a "`pi_decisions.market_type` migration" for #102 was avoided — the pending-signal branch recovers market_type from the source serenity_opportunity instead.
- **#134** (frontdoor route-file read — LOW; a cache adds backend-routing staleness risk > the microsecond gain, left as-is).

**Previously deferred, now FIXED:** #40 (`37eea98`), #41 (`f11b53a`), #109 (`45268da`), #143 (`3203264`), #17 (`c600b5b`), #90 (`7874bf3`), #147 (`3586f6e`), #9/#13/#14/#102 (B12 `df2fa5c`…`6af0abd`).

## Finding Index (navigation + verification status)

> **How to use this ledger for debugging.** This is the running local ledger of a full-project SDD/bug review. Findings are numbered `#1`-`#180`, grouped into thematic `##` sections below in ascending order - jump to `### N.` for the full write-up (impact, exact `file:line` evidence, reproduction, and fix direction). The table below is the triage index; the per-finding bodies are the source of truth.

**Legend** - Sev: **HIGH** = safety/trust/secrets/fund-correctness or a broken core loop; MED = real defect with bounded/conditional impact or an honesty gap; LOW = cosmetic / analytics-only / dormant-by-default / doc-drift. Status: `verified` = confirmed against current code (all of `#132`-`#180` were adversarially double-verified when found; `#1`-`#131` were re-verified against current code on 2026-07-06); `corrected` = a 2026-07-06 verify-pass correction note is appended to the finding body (line-drift or over-stated scope - read it before acting).

**Totals:** 180 open findings - 9 HIGH, 99 MED, 72 LOW. Plus 9 candidates investigated and **refuted** (see "Investigated and refuted" - do not re-report). Coverage: full-project pass complete (see "Coverage Status").

### Fix-first (HIGH severity)

- **#1** - `aletheia/server/routes/settings.py:79` - GET /api/settings dumps market_data (coinalyze_api_key) and network (proxy_url) raw — secrets in GET
- **#2** - `aletheia/server/routes/settings.py:624` - POST /api/settings/webhook echoes secret in {updated} and writes it to audit before/after (GET redacts it)
- **#7** - `aletheia/risk/engine.py:121` - Live gating fails open: only == 'live_disabled' blocks live orders; any other live.state permits them
- **#21** - `aletheia/agent_shell/trading_decision.py:1321` - Serenity attach reads o.get('market') but context stores key 'market_type' → spot short becomes usdt_futures
- **#42** - `aletheia/server/app.py:436` - _PROTECTED_PATHS tunnel guard covers only 5 paths; permission-mode/exchange-mode/risk/operator writes exposed
- **#60** - `aletheia/sync/exporter.py:82` - Export + backup copy config.yaml unredacted while manifest dishonestly sets secrets_included:False
- **#61** - `aletheia/sync/exporter.py:151` - Exporter artifact copy misses nested .env/.key/.pem; only top-level dir checked, categories unfiltered
- **#145** - `aletheia/agent_runtime/connectivity.py:170` - Connectivity auto-resume task orphaned on ephemeral per-job loop -> mid-run connectivity loss pauses trading FOREVER
- **#159** - `aletheia/analytics/guard_promotion.py:40` - Guard promotion/effectiveness use synthetic-close-contaminated rows + skip clean-window -> promote/deprecate live guards on fabricated evidence

> Also high-value despite a MED rating (touch trust boundaries / core honesty): #137 (unauth risk-config via public tunnel), #159/#160/#157 (learning/operator fed contaminated or dead-on-arrival data), #133 (secret leak in log tail), #98 (entry-plan stop/TP geometry), #109 (entry partial-fill state).

### All findings

| # | Sev | Location (open first) | Summary | Status |
|---|-----|-----------------------|---------|--------|
| 1 | **HIGH** | `aletheia/server/routes/settings.py:79` | GET /api/settings dumps market_data (coinalyze_api_key) and network (proxy_url) raw — secrets in GET | verified |
| 2 | **HIGH** | `aletheia/server/routes/settings.py:624` | POST /api/settings/webhook echoes secret in {updated} and writes it to audit before/after (GET redacts it) | verified |
| 3 | MED | `aletheia/server/routes/settings.py:27` | extra=forbid 422 echoes rejected input; no RequestValidationError sanitizer exists app-wide | verified |
| 4 | MED | `aletheia/server/routes/settings.py:1236` | POST /api/settings/network returns proxy_url raw and audits credential-bearing proxy URL verbatim | verified |
| 5 | MED | `tests/test_config_routes.py:166` | Secret-coverage test omits coinalyze_api_key/proxy_url from hidden set — actively asserts them visible | verified |
| 6 | MED | `aletheia/server/routes/health.py:222` | 10s topbar polling hits async /api/status that runs blocking git subprocess (current_commit) on the loop | verified |
| 7 | **HIGH** | `aletheia/risk/engine.py:121` | Live gating fails open: only == 'live_disabled' blocks live orders; any other live.state permits them | verified |
| 8 | LOW | `aletheia/execution/gateway.py:449` | Order/approval error responses return raw str(e) to API (gateway.py 351/437/449, data.py:649) | verified |
| 9 | MED | `aletheia/agents/paper_trader_ranker.py:560` | Ranker _make_candidate drops market_type; _attach_opportunity_context returns early on opposite-dir same-symbol opp | verified |
| 10 | MED | `aletheia/agents/paper_trader.py:140` | paper_trader signal dedupe is symbol-only (seen_symbols, recent pairs, open/pending sets), not market-aware | verified |
| 11 | MED | `aletheia/agent_shell/commands.py:1664` | review_portfolio_state closes by symbol match [:1], prompt omits market_type/position_id | verified |
| 12 | MED | `aletheia/agent_shell/trading_decision.py:1196` | _parse_decision does unguarded int(raw['conviction']); non-int LLM output raises (leverage/_num are guarded) | verified |
| 13 | MED | `aletheia/trading/market_data.py:164` | latest_snapshot() queries by symbol only; risk freshness _market_data_age_seconds also symbol-only | verified |
| 14 | MED | `aletheia/trading/market_data.py:50` | _build_dynamic_instruments hardcodes MARKET_SPOT for universe members (l50) and open positions (l63) | verified |
| 15 | LOW | `frontend/src/charts/TradeInsightsPanel.tsx:449` | Symbol rendered via .replace('/USDT','') bypassing fmtSymbol in TradeInsightsPanel:449 and PnLZone:173 | verified |
| 16 | MED | `aletheia/analytics/trade_case_builder.py:58` | Case builder windows on opened_at, so trades opened >7d ago but closed today are skipped | verified |
| 17 | MED | `aletheia/agents/dreaming_agent.py:158` | Dreaming _extract_lessons only reads case studies created in last 24h; no processed-ID tracking | verified |
| 18 | MED | `aletheia/agent_shell/trading_context.py:836` | Prompt injects applied lessons with no expires_at filter while risk engine skips expired ones | corrected |
| 19 | MED | `tests/specs/spec_lesson_guards.py:74` | LG-S03/LG-S04 accept() lessons (status='accepted') but runtime reads status='applied' → tests pass on empty result | verified |
| 20 | LOW | `tests/specs/spec_learning_pipeline.py:115` | Spec asserts n>0 against production data/app.db trade_case_study rows — non-hermetic, machine-dependent | verified |
| 21 | **HIGH** | `aletheia/agent_shell/trading_decision.py:1321` | Serenity attach reads o.get('market') but context stores key 'market_type' → spot short becomes usdt_futures | verified |
| 22 | MED | `aletheia/agents/research_worker.py:769` | research_worker tags stock tokens equity_tradeable (executable) while its own prompt/docstring+docs say observation-only | verified |
| 23 | LOW | `aletheia/server/routes/data.py:392` | SerenityOpportunityCreate lacks asset_class/market_type, but no UI caller actually posts them | corrected |
| 24 | MED | `aletheia/risk/engine.py:176` | Risk engine has no spot+short guard — only global allow_short check; a spot token short passes to broker as a sell | verified |
| 25 | LOW | `frontend/src/pages/Settings.tsx:613` | Strategy Factory/Optuna/backtest UI+config residue contradicts Position-C removal direction | verified |
| 26 | LOW | `aletheia/config/schema.py:694` | EventTriggerConfig.trigger_strategy_design is dead config — never consumed by any code | verified |
| 27 | LOW | `aletheia/agent_shell/operator_loop.py:700` | Operator action set lists trigger_optuna_sweep (returns not_implemented) + retire/demote that only queue recs | verified |
| 28 | LOW | `docs/design/api_reference.md:394` | api_reference.md route table stale: wrong operator trigger path, missing infra/proposal routes, dead strategy routes | verified |
| 29 | LOW | `docs/design/agent_contracts.md:10` | Scheduler docs stale: agent_contracts job table omits 5 jobs; header says 31/32 but 33 registered | verified |
| 30 | LOW | `docs/governance/trust_and_change_control.md:39` | Docs still say Position lacks proposal/order/opportunity links; code Position now has all three | verified |
| 31 | LOW | `docs/design/live_unlock_contract.md:75` | live_unlock_contract.md says scorecard gate unimplemented/xfail, but code enforces composite_score>=70 | verified |
| 32 | LOW | `docs/design/api_reference.md:362` | api_reference.md flags kill-switch release as not requiring confirm token; code now enforces confirm_release (422) | verified |
| 33 | LOW | `docs/design/api_reference.md:194` | api_reference.md implies /settings/permission-mode sets both worker+operator; code sets only worker mode | verified |
| 34 | MED | `tests/test_spec_conformance.py:122` | Conformance loader has duplicate pi_decision_chain import and omits skill_store + universe_distribution_audit specs | verified |
| 35 | LOW | `aletheia/server/routes/settings.py:961` | MarketDataConfig fields (provider/coinalyze/divergence/crypto_fallback) not in update API or Settings UI | verified |
| 36 | LOW | `frontend/src/pages/Settings.tsx:402` | GuardConfig UI exposes only auto_promote; enabled/enforce/min_trades/max_penalty hidden (API accepts all) | verified |
| 37 | LOW | `frontend/src/pages/Settings.tsx:1011` | scanner_weights / scanner_regime_scoring shown read-only with 'aletheia config set' CLI hints, no UI edit | verified |
| 38 | MED | `aletheia/server/routes/settings.py:149` | exchange-markets API accepts binance_live/okx_live but execution only knows *_live_limited; UI narrower | corrected |
| 39 | MED | `frontend/src/pages/Settings.tsx:2617` | Kill-switch UI always shows daily-loss = Live Cap × pct, but engine uses starting_capital for demo mode | verified |
| 40 | MED | `aletheia/server/routes/operator.py:980` | /operator/pause,resume,wake call _set_paused directly with no check_permission (unlike /command at 1001) | verified |
| 41 | MED | `aletheia/server/routes/operator.py:1139` | /operator/internal/* writers (tasks/messages/events/artifacts/heartbeat) have no auth/caller-identity guard | verified |
| 42 | **HIGH** | `aletheia/server/app.py:436` | _PROTECTED_PATHS tunnel guard covers only 5 paths; permission-mode/exchange-mode/risk/operator writes exposed | verified |
| 43 | MED | `aletheia/files/jsonl.py:344` | verify_audit_chain treats first-line-in-file mismatch as benign re-anchor, masking head truncation | verified |
| 44 | LOW | `aletheia/storage/repositories.py:1180` | prune_system_actions hard-DELETEs knowledge_items rows (item_type=system_action), violating no-hard-delete rule | verified |
| 45 | LOW | `frontend/src/components/MarketTicker.tsx:64` | Top ticker shows DB snapshots via /market/snapshots, not a live price feed | verified |
| 46 | LOW | `frontend/src/pages/Universe.tsx:143` | Universe symbol formatting uses board-level snapshot.market_type for all rows | verified |
| 47 | LOW | `frontend/src/pages/RiskControls.tsx:92` | RiskControls marketTypeBySymbol map is keyed by symbol only; dup symbol overwrites | verified |
| 48 | LOW | `aletheia/storage/models.py:883` | UniverseForwardOutcome lacks market_type; settle fetches bare symbol (defaults spot) | verified |
| 49 | LOW | `aletheia/data/ccxt_provider.py:11` | Docstring claims xStocks is the only direct CCXTProvider() site; 3 others exist | verified |
| 50 | LOW | `aletheia/execution/bitget_broker.py:169` | Execution causal links present in models+brokers; docs stale (informational, not a bug) | verified |
| 51 | MED | `aletheia/portfolio/pnl.py:384` | unrealized_by_symbol keyed by symbol only; same-symbol spot+futures overwrite, wrong per-symbol sign | verified |
| 52 | MED | `aletheia/portfolio/pnl.py:274` | exposure_by_symbol merges spot+futures notional into one symbol bucket (local and exchange paths) | verified |
| 53 | LOW | `frontend/src/pages/RiskControls.tsx:92` | RiskControls marketTypeBySymbol keyed by symbol only; last-seen market_type wins for display | verified |
| 54 | MED | `aletheia/portfolio/position_reconciler.py:72` | reconciler _key omits market_type; same-symbol+dir spot/futures locals overwrite, one vanishes from classify | verified |
| 55 | MED | `aletheia/portfolio/pnl.py:81` | exchange uPnL index keyed by (symbol,dir) no market_type; can attribute uPnL to wrong local position | verified |
| 56 | MED | `aletheia/portfolio/pnl.py:288` | today_pnl = total_unrealized + today_realized; labels lifetime-open uPnL as "Today" (honesty gap) | verified |
| 57 | LOW | `aletheia/portfolio/pnl.py:246` | today_pnl_by_symbol groups realized by symbol only; no market_type in output key (mode is a filter) | verified |
| 58 | MED | `aletheia/backup/service.py:58` | Backup omits data/logs/audit.jsonl hash-chain; only app.db/config/events/artifacts copied | verified |
| 59 | MED | `aletheia/backup/service.py:76` | Backup raw-copies app.db under WAL mode without checkpoint/backup-API; may miss committed rows | verified |
| 60 | **HIGH** | `aletheia/sync/exporter.py:82` | Export + backup copy config.yaml unredacted while manifest dishonestly sets secrets_included:False | verified |
| 61 | **HIGH** | `aletheia/sync/exporter.py:151` | Exporter artifact copy misses nested .env/.key/.pem; only top-level dir checked, categories unfiltered | verified |
| 62 | MED | `aletheia/sync/exporter.py:98` | proposal-pack requires proposal_id but never filters; exports ALL trade_proposals | verified |
| 63 | MED | `aletheia/sync/importer.py:116` | Import dry-run checksum verify skips missing files silently; no error appended | verified |
| 64 | MED | `aletheia/sync/importer.py:138` | Import conflict detection unimplemented; diverged rows silently counted as skipped duplicates | verified |
| 65 | MED | `aletheia/server/routes/data.py:674` | /trades/export docstring says tax/PnL but exports orders-only; no fills/fees/realized PnL | verified |
| 66 | MED | `aletheia/server/routes/persistence.py:136` | persistence verify/import endpoints take arbitrary caller path with no root constraint or local guard | verified |
| 67 | MED | `aletheia/agent_shell/executor.py:141` | requires_confirmation is metadata only; execute_typed_command runs handler with no confirm gate | verified |
| 68 | MED | `aletheia/agent_shell/commands.py:1664` | PI portfolio review closes by symbol only (matching[:1]); can close wrong instrument | verified |
| 69 | MED | `aletheia/net.py:137` | Proxy fallback logs full proxy_url (user:pass@host) with no redaction | verified |
| 70 | MED | `aletheia/agent_runtime/orchestrator.py:36` | Agent run errors persist raw traceback.format_exc + f"raised: {e}" into agent_runs.errors/audit | verified |
| 71 | LOW | `frontend/src/pages/CommandCenter.tsx:1218` | Command Center renders 'confirmation' as a badge; Send always one-clicks mutate(), no gate | verified |
| 72 | LOW | `frontend/src/pages/AgentActivity.tsx:275` | Agent Activity says steps backend 'not yet available' but GET /agent-runs/{id}/steps exists | verified |
| 73 | LOW | `tests/test_spec_conformance.py` | Conformance matrix omits spec_universe_distribution_audit (real UP-DIST-01) and spec_skill_store | verified |
| 74 | LOW | `tests/specs/spec_meta.py:19` | SpecClaim.status is unvalidated frozen dataclass; PI-S04 uses 'superseded', matrix renders '?' | verified |
| 75 | MED | `tests/test_spec_conformance.py:231` | Conformance suite passes green while missing/partial/gap-marker claims remain; matrix is informational-only | verified |
| 76 | LOW | `docs/design/README.md:93` | README Spec-as-Code index stale: lists non-existent spec_promotion_gates.py, risk_engine=7 (actual 34), 14 of 59 files | verified |
| 77 | LOW | `docs/design/api_reference.md:93` | api_reference documents /strategies/summary, /strategies/{id}, PATCH /strategies/{id}/venue; only GET /strategies exists | verified |
| 78 | LOW | `docs/design/api_reference.md:394` | Docs say POST /operator/trigger; real route is /loop/trigger -> POST /api/operator/loop/trigger | verified |
| 79 | LOW | `docs/design/api_reference.md:65` | api_reference omits GET /proposals/{id} which exists at data.py:609 | verified |
| 80 | LOW | `docs/design/api_reference.md:402` | 15 individual /operator/internal/* routes collapsed to one wildcard row vs 'Complete endpoint index' claim | verified |
| 81 | LOW | `docs/design/api_reference.md:522` | api_reference omits /_infra/doctor, /_infra/status, /api/dev/reload/{status,events} | verified |
| 82 | MED | `aletheia/runtime_continuity/service.py:539` | Runtime-continuity write methods never check self.enabled; kill switch ignored on all mutations | verified |
| 83 | MED | `aletheia/server/routes/runtime.py:64` | Runtime transition routes accept caller-supplied `actor` and require no `confirm` token | verified |
| 84 | LOW | `aletheia/model_provider/oauth.py:201` | OAuth error page cannot inject HTML: _ERROR_HTML.format() raises KeyError on CSS braces first | corrected |
| 85 | MED | `aletheia/server/routes/news.py:100` | analyze_news returns raw (content,error) tuple from llm_complete as `analysis`, serialized as JSON array | verified |
| 86 | MED | `frontend/src/api/client.ts:557` | Frontend saves prediction prefs via POST but backend only exposes PATCH /prediction/preferences | verified |
| 87 | MED | `aletheia/server/routes/capital.py:37` | Capital deposit/withdraw create_event writes capital_events row but emits no audit record | verified |
| 88 | MED | `aletheia/server/routes/capital.py:12` | Capital async handlers run run_migrations() on every request via unconditional _ensure_db() | verified |
| 89 | LOW | `docs/design/agent_contracts.md:10` | Scheduler docs drift: agent_contracts says 32/31 jobs, omits 5 real jobs; code registers 33 | verified |
| 90 | MED | `aletheia/agent_runtime/scheduler.py:1081` | tpsl_fill_check (every 90s, io exec) calls Bitget via bare asyncio.run() with no timeout wrapper | verified |
| 91 | MED | `aletheia/universe/registry.py:105` | Universe registry/features build CCXTProvider directly, bypassing get_market_data_provider factory | verified |
| 92 | MED | `aletheia/data/collectors/macro.py:74` | MacroCollector.collect() calls blocking yf.Ticker via sync _fetch_tradfi inside async tick | verified |
| 93 | MED | `aletheia/risk/engine.py:384` | Risk lesson hard-blocks branch only on rec.action; lesson.category never checked despite docstring | verified |
| 94 | LOW | `aletheia/trading/strategy_lifecycle.py:111` | Write-capable strategy FSM (create/promote/gates) + strategy_designer refs remain as dead residue | verified |
| 95 | LOW | `frontend/src/api/client.ts:537` | client.ts setStrategyVenue PATCHes deleted /strategies/{id}/venue; no backend route, no TSX caller | verified |
| 96 | LOW | `aletheia/analytics/trade_insights.py:388` | Symbol×Direction matrix keys omit market_type, merging spot+perp into one win-rate row | verified |
| 97 | LOW | `aletheia/server/routes/settings.py:314` | System Operator rename incomplete: pi_daemon persists in config/API/audit/client contracts | verified |
| 98 | MED | `aletheia/risk/engine.py:106` | Entry-plan gate checks stop/TP/RR presence, not side-of-entry geometry; inverted plan passes | verified |
| 99 | MED | `aletheia/agents/lesson_reviewer.py:54` | Lesson reviewer _fetch_evidence ignores scanner-family; block_scanner approved on global/symbol evidence | verified |
| 100 | MED | `aletheia/server/routes/data.py:428` | Manual POST /serenity/opportunities copies submitted evidence_tier into assessed_tier, no evidence check | verified |
| 101 | MED | `frontend/src/pages/SerenityLens.tsx:700` | Serenity opportunity Tier column shows proposed evidence_tier, not authoritative assessed_tier | verified |
| 102 | MED | `aletheia/agent_shell/trading_decision.py:1321` | Serenity market_type not copied to TradingDecision: fallback reads wrong key 'market', direct branch skips it | verified |
| 103 | MED | `aletheia/portfolio/pnl.py:203` | Serenity auto PostMortem computes hold_h from pre-close row lacking closed_at, records hold_h=0 | verified |
| 104 | LOW | `docs/plans/serenity_execution_path.md:165` | Plan promises explicit thesis_invalidated marker exit; code only supports opp status!=active | verified |
| 105 | LOW | `docs/design/execution_contract.md:170` | execution_contract §10.1 still lists deleted ruleset_exit as active exit priority 4 | verified |
| 106 | MED | `aletheia/agent_shell/commands.py:1664` | review_portfolio_state closes by target_symbol only; picks newest matching row, no position_id | verified |
| 107 | MED | `frontend/src/pages/AgentActivity.tsx:33` | AgentActivity manual-run list exposes deleted strategy_designer + obsolete stock_observer, omits equity_observer | verified |
| 108 | MED | `frontend/src/pages/Learning.tsx:66` | Learning page presents legacy (deleted) strategy lifecycle as active 'Strategies in paper' KPI | verified |
| 109 | MED | `aletheia/execution/bitget_broker.py:142` | Entry partial fills overwritten to order status='filled' and proposal 'executed'; close path uses 'partial' | verified |
| 110 | MED | `aletheia/server/routes/news.py:105` | NewsIntel Analyze Impact writes only status='reviewed'; research_worker consumes only status='analyzed' | verified |
| 111 | MED | `aletheia/server/routes/prediction.py:122` | PredictionLens card exposes external Polymarket market_id; history/claims/Serenity endpoints key on internal id | verified |
| 112 | MED | `aletheia/server/routes/data.py:660` | POST /proposals/{id}/reject overwrites any status to rejected with no state guard or before/after audit | verified |
| 113 | MED | `frontend/src/pages/Proposals.tsx:80` | approve mutation onSuccess ignores execution.status; 200 body with status=error clears inline error | verified |
| 114 | MED | `frontend/src/pages/Proposals.tsx:94` | Approve confirm shows proposal's demo/live mode but backend always forces paper_internal | verified |
| 115 | MED | `frontend/src/charts/PriceChart.tsx:52` | PriceChart takes no marketType prop; all 3 getOhlcv calls default to spot even for perp boards | verified |
| 116 | MED | `frontend/src/pages/TradesPositions.tsx:330` | Order 'View on Chart' navigates('/charts') with no symbol/market; chart inits to BTC/USDT (no searchParams) | verified |
| 117 | MED | `frontend/src/pages/TradesPositions.tsx:235` | ReconciliationCard returns null on zero drift rows, hiding stale/error/unconfigured sync_status | verified |
| 118 | MED | `frontend/src/pages/Overview.tsx:171` | Overview & RiskControls derive Scheduler light from paused only, ignore scheduler_running/state | verified |
| 119 | MED | `aletheia/analytics/go_live_scorecard.py:308` | Scorecard model-health does snap.get('providers') on a list snapshot; swallowed to neutral 50 | corrected |
| 120 | MED | `frontend/src/components/AgentFeed.tsx:208` | AgentFeed colors only status=='error' red; failed/completed_with_errors/timed_out/stale render yellow | verified |
| 121 | LOW | `frontend/src/components/SetupBanner.tsx:16` | SetupBanner counts only api_key.configured + Bitget demo; ignores OAuth/CLI providers and OKX/Binance | verified |
| 122 | MED | `frontend/src/components/CausalChain.tsx:184` | CausalChain Fill node built from order.price/filled_at; never reads fills table (no getFills route) | verified |
| 123 | MED | `frontend/src/components/CausalChain.tsx:220` | Order-anchor CausalChain always renders Position 'not recorded'; never resolves positions by order_id | verified |
| 124 | MED | `aletheia/server/routes/analytics.py:79` | Equity-curve applies ORDER BY closed_at ASC LIMIT before date cutoff; old rows can starve in-window points | verified |
| 125 | LOW | `frontend/src/components/CommandPalette.tsx:40` | Command Palette nav list omits /universe (Universe Board) though route+NAV entry exist | verified |
| 126 | MED | `frontend/src/components/CommandPalette.tsx:74` | Command root onKeyDown hijacks every Enter when input non-empty -> draft nav, blocking cmdk selection; no length threshold | verified |
| 127 | LOW | `frontend/src/charts/ProbabilityChart.tsx:38` | ProbabilityChart parses ts via new Date() with no NaN filter; Polymarket fallback epoch strings -> NaN | verified |
| 128 | LOW | `frontend/src/charts/TradeAnalyticsPanel.tsx:90` | Trade analytics 'By Strategy Source' still presents deleted RuleSet as active dimension (union baked into API type) | verified |
| 129 | MED | `aletheia/files/markdown.py:68` | Knowledge markdown filenames (timestamp+title, no unique id) collide within a second and overwrite evidence | verified |
| 130 | MED | `aletheia/agents/lesson_reviewer.py:190` | Overturn pass re-judges only status=accepted; consumed status=applied lessons never re-reviewed | verified |
| 131 | MED | `aletheia/agents/paper_trader_ranker.py:432` | TradingMemo half-life applied only in prompt render, not in ranker _memo_scoring candidate sort | verified |
| 132 | LOW | `aletheia/server/routes/gateway.py:79` | Gateway restart audit hardcodes actor="human"; source taken from caller-supplied reason (automated restarts logged as human) | verified |
| 133 | MED | `aletheia/gateway/logs.py:26` | Log-tail redaction misses bare `secret` + ACCESS-KEY/ACCESS-SIGN headers -> secrets leak via doctor log tail | verified |
| 134 | LOW | `aletheia/gateway/frontdoor.py:288` | Frontdoor /api/* proxy does sync route-file read+json.loads every request, no TTL cache | verified |
| 135 | MED | `aletheia/update/applier.py:123` | Self-update rollback reports success without checking reset_hard() result (false clean-revert) | verified |
| 136 | LOW | `aletheia/update/applier.py:19` | _safe_env strips only BITGET_; COINALYZE_/FRED_ (read from os.environ) not stripped for update subprocesses | verified |
| 137 | MED | `aletheia/server/app.py:436` | Tunnel auth guard omits POST /api/settings/risk + /exchange-mode -> unauth kill-switch disable/risk widening when tunnel public | verified |
| 138 | MED | `aletheia/server/app.py:322` | sync_all_exchanges() runs sync pre-yield in lifespan -> blocks event loop, delays port bind | verified |
| 139 | LOW | `aletheia/portfolio/position_manager.py:213` | Exchange stop replacement is bitget-only; okx/binance trailed stops never reach the exchange (flag default off) | verified |
| 140 | LOW | `aletheia/execution/bitget_broker.py:145` | Averaging-in doesn't accumulate entry_fee in okx/binance/bitget brokers (paper does) -> overstates realized PnL | verified |
| 141 | LOW | `aletheia/execution/paper_broker.py:81` | liquidation_price not recomputed on averaging-in in the 3 live brokers (display/LLM only, not risk-gate) | verified |
| 142 | MED | `aletheia/exchanges/binance/client.py:75` | Binance client bypasses system proxy (no session/trust_env) - CONFIRM vs branch iridite/system_proxy_support before fixing | verified |
| 143 | MED | `aletheia/risk/engine.py:508` | Risk check #5 drawdown reads paper_internal position NOTIONAL as equity -> can zero size cap (dormant under default bitget_demo) | verified |
| 144 | LOW | `aletheia/agent_runtime/scheduler.py:1008` | Reconciler run recorded status=completed while carrying errors -> evades health failure-ratio signal | verified |
| 145 | **HIGH** | `aletheia/agent_runtime/connectivity.py:170` | Connectivity auto-resume task orphaned on ephemeral per-job loop -> mid-run connectivity loss pauses trading FOREVER | verified |
| 146 | MED | `aletheia/agent_runtime/connectivity.py:214` | Dual auto-resume loops share runtime.paused; first-to-recover resumes whole system while other outage persists | verified |
| 147 | LOW | `aletheia/config/loader.py:130` | load_config() returns shared mutable config; rejected multi-field settings update leaks unsaved values ~5s | verified |
| 148 | LOW | `aletheia/diagnostics/config_consistency.py:66` | demo-credential fix hints name env vars no code ever reads (get_secret has no environ branch) | verified |
| 149 | LOW | `aletheia/storage/repositories.py:1918` | onchain_snapshots grows unbounded - prune_onchain_snapshots() defined but never wired into cleanup job | verified |
| 150 | LOW | `aletheia/storage/repositories.py:277` | prediction_probability_snapshots has an active producer but no prune (gated on prediction_enabled default False) | verified |
| 151 | LOW | `aletheia/agent_shell/trading_decision.py:356` | Explicit LLM market_type=spot + direction=short not normalized -> impossible spot-short reaches broker | verified |
| 152 | LOW | `aletheia/agent_shell/trading_decision.py:1256` | Attribution severed when PI trades opposite a same-symbol opp; ranker/serenity fallback skipped (DL-INV-4) | verified |
| 153 | MED | `aletheia/agent_shell/operator_loop.py:590` | L1/L2 self-heal actions are unreachable dead code + PI approval gate is a no-op rubber stamp (fake-green specs) | verified |
| 154 | LOW | `aletheia/agents/divergence_scanner.py:170` | divergence_scanner infers direction from prob-change sign, ignores question polarity -> inverted signals (gated) | verified |
| 155 | LOW | `aletheia/data/cache.py:41` | OHLCV TTL cache key omits market_type -> spot/perp candles collide for same symbol (latent) | verified |
| 156 | MED | `aletheia/data/collectors/coinalyze.py:153` | Coinalyze OI: guessed keys (may be dead feed) + not in _CROSS_VENUE_TAGS pollution - VERIFY live /open-interest schema first | verified |
| 157 | MED | `aletheia/analytics/confidence_calibration.py:161` | render_calibration_for_pi crashes on every real report (broken f-string) -> calibration feedback to LLM silently dead | verified |
| 158 | LOW | `aletheia/analytics/trade_insights.py:517` | trade_insights avg_pnl_pct for unknown buckets always ~0 (operator-precedence bug) | verified |
| 159 | **HIGH** | `aletheia/analytics/guard_promotion.py:40` | Guard promotion/effectiveness use synthetic-close-contaminated rows + skip clean-window -> promote/deprecate live guards on fabricated evidence | verified |
| 160 | MED | `aletheia/analytics/insight_synthesizer.py:176` | Memo synth + reasoning_feedback fit on positions incl. synthetic closes -> PI prompt built on fabricated exits | verified |
| 161 | MED | `aletheia/agents/helpers.py:22` | complete_with_fallback resolves fallback config + health vs default_provider, not the tier-selected provider | verified |
| 162 | MED | `aletheia/sync/exporter.py:98` | proposal-pack export ignores proposal_id and dumps ALL proposals + serenity research tables | verified |
| 163 | MED | `frontend/src/pages/Settings.tsx:2817` | Config Coverage Matrix mislabels ~18 editable fields (incl. leverage/margin) as read-only/config.yaml | verified |
| 164 | LOW | `frontend/src/pages/Settings.tsx:321` | Multiple Settings save mutations lack onError -> rejected save fails silently (no-op) | verified |
| 165 | MED | `frontend/src/pages/CommandCenter.tsx:762` | Pause/Resume/Wake + runtime mutations swallow backend 409/404 -> operator believes action succeeded | verified |
| 166 | LOW | `frontend/src/pages/CommandCenter.tsx:378` | Runtime Guidance status color dead - always yellow (promote and rollback render identically) | verified |
| 167 | LOW | `frontend/src/pages/SerenityLens.tsx:772` | Serenity Evidence tab shows advisory proposed tier, not authoritative assessed_tier (cf #101) | verified |
| 168 | LOW | `frontend/src/pages/NewsIntel.tsx:73` | NewsIntel status filter has no 'analyzed' bucket -> auto-analyzed rows only visible under All | verified |
| 169 | MED | `frontend/src/pages/Charts.tsx:323` | Charts PredictionsTab reads nonexistent m.question/m.volume_usd -> blank question, volume dropped | verified |
| 170 | MED | `frontend/src/pages/Charts.tsx:367` | Charts feeds board asset-class 'crypto' into fmtSymbol -> perps render with slash; per-snapshot fallback dead | verified |
| 171 | MED | `frontend/src/pages/Overview.tsx:29` | Overview Exchange light always probes Bitget, ignoring configured exchange -> OKX/Binance shown wrong | verified |
| 172 | LOW | `frontend/src/pages/RiskControls.tsx:233` | RiskControls shows green 'operating within all limits' when risk status missing/unknown | verified |
| 173 | LOW | `frontend/src/components/ActivityTimeline.tsx:53` | ActivityTimeline renders time-only (HH:MM) timestamps -> a stale feed looks current | verified |
| 174 | MED | `frontend/src/App.tsx:241` | Topbar master state never reflects risk engine (riskState always '') -> CAUTION/OPERATIONAL dead, false STANDBY | verified |
| 175 | LOW | `frontend/src/charts/TradeInsightsPanel.tsx:449` | SymbolDirectionMatrix renders via .replace('/USDT') not fmtSymbol (specific instance of #15) | verified |
| 176 | MED | `docs/design/knowledge_contract.md:291` | knowledge_contract 3.4/7.2 still document post_trade_review as retrieved lesson type (contradicts test-locked code) | verified |
| 177 | LOW | `aletheia/knowledge/router.py:31` | Knowledge router has 3 unreachable role entries; equity_observer runs but retrieves no knowledge | verified |
| 178 | MED | `aletheia/diagnostics/doctor.py:1822` | config_consistency info-severity finding escalated to a doctor BLOCKER (drives doctor exit 1) | verified |
| 179 | LOW | `aletheia/diagnostics/doctor.py:1794` | audit_chain breaks with no parseable timestamp always classified historical INFO (hides post-fix corruption) | verified |
| 180 | MED | `aletheia/cli/inspect_cmd.py:367` | inspect config prints trading-agent permission_mode from cfg.operator.* (wrong namespace) | verified |

## Coverage Status

Covered or substantially covered:

- `discussion.md` product direction and gold-standard expectations.
- SDD/spec conformance tests and conformance matrix.
- API reference and scheduler documentation drift.
- Settings/config UI coverage.
- API performance and frontend polling.
- Risk/execution fail-open boundaries.
- Minimal profit loop: opportunity -> decision -> proposal -> order -> position.
- Market data symbol/market-type handling.
- Learning loop and lesson lifecycle.
- Live unlock, permission modes, operator command boundaries.
- Serenity/xStocks execution boundary.
- Strategy/Evolve/Strategy Factory residual implementation.
- Secrets/credentials/config redaction chain.
- Frontend symbol display rule.
- Audit/knowledge hard deletion and audit-chain behavior.
- Initial pass on portfolio/positions/PnL domain is in progress.
- Backend control routes (telegram/update/gateway/dev_reload/command) — trust/audit/permission boundaries (2026-07-06 sweep; #132).
- Gateway lifecycle + recovery FSM wiring honesty; frontdoor/tunnel/autostart/logs redaction; pidfile/git-hook safety (2026-07-06 sweep; #133–#134).
- Self-update apply/smoke/rollback path and credential isolation for update subprocesses (2026-07-06 sweep; #135–#136).
- Server app assembly: middleware, tunnel auth-guard coverage, lifespan blocking I/O (2026-07-06 sweep; #137–#138).
- Position manager exit rules + exchange-side TP/SL replacement (2026-07-06 sweep; #139).
- Exchange sync + all four brokers' averaging-in cost-basis/liquidation; low-level okx/binance/bitget clients (symbol/contractSize/proxy/secret) (2026-07-06 sweep; #140–#142).
- Risk engine full ~20-check guard chain + kill_switch (TRUST BOUNDARY) — clean except the paper_internal drawdown notional bug (2026-07-06 gap sweep; #143).
- Execution chokepoint `gateway.py` (TRUST BOUNDARY) — order-flow invariants all verified clean (2026-07-06 gap sweep; lane clean).
- Permissions/tool-registry (TRUST BOUNDARY) — restricted-tool/forbidden-intent enforcement verified fail-closed; runtime self-heal/scheduler job bodies/connectivity (2026-07-06 gap sweep; #144–#146).
- Config/secrets (TRUST BOUNDARY) + storage integrity (was_in_tx discipline / audit hash chain / retention) (2026-07-06 gap sweep; #147–#150).
- agent_shell decision chain + operator layer (2026-07-06 gap sweep; #151–#153).
- agents producers (risk_critic/post_trade_reviewer/research_worker/divergence_scanner/…) (2026-07-06 gap sweep; #154).
- data provider/symbols + collectors/sources (2026-07-06 gap sweep; #155–#156).
- analytics (all 28 files across pnl/attribution + learning/feedback) (2026-07-06 gap sweep; #157–#160).
- model_provider routing/fallback/oauth internals (2026-07-06 gap sweep; #161).
- universe/trading/sizing/regime/policies — clean (persona/dynamic-sizing verified dormant); operator-shell + sync/trust/uta/doctor/telegram (2026-07-06 gap sweep; #162).

- Frontend page-by-page (all 13 pages, 5 charts, all components incl. shared App/client/types/lib) — UI-honesty/symbol/timestamp/loading-lane pass (2026-07-06 final sweep; #163–#175).
- `knowledge/` internals (manager/retrieval/maintenance/guards/router/FTS) — FTS write-path integrity + contract drift (2026-07-06 final sweep; #176–#177).
- `diagnostics/doctor.py` line-by-line probe-honesty + `cli/` (incl. inspect_cmd) (2026-07-06 final sweep; #178–#180).
- `runtime_continuity/` service internals + `portfolio/uta/` adapters (2026-07-06 final sweep; findings refuted — clean).

Coverage status: **full-project pass complete (2026-07-06).** Three verified fan-out sweeps (9 + 16 + 12 lanes) plus the pre-existing #1–#131 manual sweep now cover the frontend end-to-end and every backend subsystem (routes, gateway, execution, risk, permissions, runtime/self-heal, config/secrets, storage, agent_shell decision+operator, agents, data provider/collectors/sources, all analytics, model_provider, universe/trading/sizing, knowledge, doctor, cli). Remaining work is verification-gated maintenance rather than unscanned territory:

- Re-scan on material change: any new subsystem, new data source, new learning consumer (apply the synthetic-close filter check), or new frontend page must get its own pass — do not assume the ledger stays complete.
- Deeper-than-review items flagged for the fixer, not re-review: exchange low-level clients beyond the open path; `tests/test_runtime_continuity.py` beyond happy-path; the live Coinalyze `/open-interest` schema (#156) needs an actual API check.
- The ledger records findings; it does not prove the fixes. Every fix must add its own focused regression per AGENTS.md change discipline.

## High Priority Findings

### 1. Settings API returns sensitive config values

Impact: API keys or credential-bearing URLs can be exposed through normal Settings calls, violating the trust rule that secrets must never be returned by GET endpoints.

Evidence:

- `aletheia/server/routes/settings.py`: `GET /api/settings` returns `cfg.market_data.model_dump(mode="json")`.
- `aletheia/config/schema.py`: `MarketDataConfig.coinalyze_api_key` exists as a plain config field.
- `aletheia/server/routes/settings.py`: `GET /api/settings` returns `cfg.network.model_dump(mode="json")`.
- `aletheia/config/schema.py`: `NetworkConfig.proxy_url` is a plain string and may contain `user:pass@host`.

Reproduction performed with a temporary config, no real secrets:

- `GET /api/settings` returned dummy `coinalyze_api_key`.
- `GET /api/settings` returned dummy proxy URL `http://user:pass@127.0.0.1:7890`.

Expected direction: move Coinalyze key fully to secret storage, remove it from config responses, and redact credential-bearing proxy URLs in all API responses and audit events.

### 2. Webhook secret is not actually write-only

Impact: saving a webhook HMAC secret exposes the secret in the HTTP response and writes it into audit logs.

Evidence:

- `aletheia/server/routes/settings.py`: `WebhookConfigUpdate` accepts `secret`.
- `aletheia/server/routes/settings.py`: `update_webhook_config()` returns `{"updated": updates}`.
- `aletheia/server/routes/settings.py`: `update_webhook_config()` writes audit with raw `before` and `after` dumps.
- `frontend/src/pages/Settings.tsx`: UI labels the HMAC secret as write-only, which conflicts with backend behavior.

Reproduction performed with dummy value:

- `POST /api/settings/webhook` returned the dummy secret in response text.
- The dummy secret appeared in `audit.jsonl`.

Expected direction: never return `secret` in `updated`; audit only field names or redacted before/after.

### 3. Settings validation errors can echo sensitive rejected input

Impact: when Settings endpoints reject unknown fields with Pydantic 422, the error body can echo the submitted value. A user trying to save a secret into an unsupported Settings field can receive the secret back in the HTTP error.

Evidence:

- `aletheia/server/routes/settings.py`: `SettingsRequest` uses `ConfigDict(extra="forbid")`.
- `MarketDataConfigUpdate` does not accept `coinalyze_api_key`, while the root `MarketDataConfig` still defines it.

Reproduction performed with dummy value:

- `POST /api/settings/market-data` with `{"coinalyze_api_key": "cz_dummy_secret_abcdef"}` returned 422 and response text contained the dummy secret.

Expected direction: add a FastAPI validation-error sanitizer for sensitive-looking input, and remove secret fields from normal Settings request bodies.

### 4. Proxy URL with credentials is returned and audited

Impact: a proxy URL can contain username/password. The current network settings route returns and audits it verbatim.

Evidence:

- `aletheia/server/routes/settings.py`: `update_network_config()` writes audit with raw `cfg.network.model_dump()`.
- `aletheia/server/routes/settings.py`: `update_network_config()` returns raw `network`.
- `frontend/src/pages/Settings.tsx`: `NetworkCard` displays and edits `proxy_url` directly.

Reproduction performed with dummy value:

- `POST /api/settings/network` returned `http://user:pass@127.0.0.1:7890`.
- Dummy `user:pass` appeared in audit JSONL.

Expected direction: split proxy credentials into secret storage or redact credentials in responses/audit.

### 5. Existing secret tests give false confidence

Impact: current tests pass while missing real Settings leaks.

Evidence:

- `tests/test_config_routes.py::test_settings_response_covers_sanitized_config_schema` hides `api_key_secret_ref`, `oauth_token_ref`, `oauth_refresh_ref`, `tunnel.credentials_path`, and `webhook.secret`, but not `market_data.coinalyze_api_key` or credential-bearing `network.proxy_url`.
- `tests/specs/spec_api_reference.py` focuses on exchange credentials status helpers in `routes/bitget.py`, not generic Settings config or audit events.

Verification:

- `uv run pytest tests/test_secrets.py tests/specs/spec_api_reference.py -q --tb=short -r xX` passed.
- `uv run pytest tests/test_config_routes.py -q --tb=short -r xX` passed.

Expected direction: add regression coverage for Settings response redaction, Settings mutation response redaction, audit redaction, and 422 sanitization.

### 6. API hot paths violate project performance rules

Impact: the dashboard can repeatedly freeze or stress the backend event loop and external services.

Evidence:

- `frontend/src/App.tsx`: topbar polls `status`, `operator-status`, `kill-switch`, `agent-runs`, `health`, and `infra-events` every 10 seconds.
- `aletheia/server/routes/health.py`: `/api/status` calls config load, health rollup, scheduler snapshot, infra events, LLM health snapshot, and `current_commit()`.
- `aletheia/update/git_ops.py`: `current_commit()` runs blocking `subprocess.run`.
- `frontend/src/pages/Overview.tsx`: model health every 5 seconds and Bitget test every 15 seconds.
- `frontend/src/pages/Settings.tsx`: model health every 5 seconds; exchange connection tests every 12/15 seconds.
- `aletheia/server/routes/model.py`: `/api/model/health` runs `healthcheck_all()`, not a cached snapshot.
- `aletheia/server/routes/bitget.py`: connection-test endpoints construct exchange clients and perform real network probes.
- `aletheia/server/routes/data.py`: `/api/agent-runs` is async but calls sync DB functions.

Conflict:

- `AGENTS.md` performance rule says status/kill-switch polling must be pure memory and <5ms; exchange health probes must be >=60s; LLM related polling must be >=30s.

Expected direction: separate cheap snapshots from active probes; cache or background-refresh model/exchange health; avoid blocking subprocess/SQLite in hot async handlers.

### 7. Risk/execution live-state guards are fail-open for unknown states

Impact: a future or corrupted live state can bypass the intended disabled/live gating.

Evidence:

- `aletheia/risk/engine.py`: blocks live execution only when `live_state == "live_disabled"`.
- `aletheia/execution/gateway.py`: similar equality check for `live_disabled`.
- `aletheia/execution/gateway.py`: unknown execution mode returns without hard failure in some paths.

Expected direction: whitelist known live-enabled states and fail closed for unknown states/modes.

### 8. Execution/API errors can expose raw exception strings

Impact: backend responses can include internal exception text and possibly upstream request details.

Evidence:

- `aletheia/execution/gateway.py`: several error responses use `str(e)`.
- `aletheia/server/routes/data.py`: proposal approval error returns `{"message": str(e)}`.

Expected direction: categorize errors for API responses, log sanitized internal details separately.

## Market Type / Symbol / Trading Loop Findings

### 9. Paper trader candidates can lose market type

Impact: same symbol on spot and futures can be confused; short decisions can default into futures even when the opportunity was spot.

Evidence:

- `paper_trader_ranker._make_candidate()` does not carry `opp.market` or `market_type`.
- `trading_context.py` expects candidates to carry `market_type`.
- `trading_decision._attach_opportunity_context()` can return after seeing same-symbol opposite-direction opportunity, before checking other candidate sources.

Reproduction previously performed:

- Candidate from opportunity lacked `market_type`.
- A BTC long opportunity blocked a BTC short candidate from later ranker/pending signal context.

Expected direction: carry explicit market type through every candidate and match opportunity context by symbol + direction + market type + source.

### 10. Symbol-only dedupe blocks valid cross-market opportunities

Impact: spot and futures for the same symbol can incorrectly suppress each other.

Evidence:

- `paper_trader.py`: `_recent_signal_pairs` keys only `(symbol, direction)`.
- `paper_trader.py`: `_select_signal_candidates` uses `seen_symbols`.
- `paper_trader.py`: open/pending skip sets are symbol-only in several places.

Expected direction: dedupe by canonical instrument identity, at minimum `(symbol, market_type, direction)`.

### 11. Portfolio review can close the wrong market

Impact: if spot and futures positions share a symbol, the agent can reason about one and close another.

Evidence:

- `review_portfolio_state()` prompt/open positions omit `market_type`.
- Close matching is symbol-based.

Expected direction: include market type and position ID in review prompt and close commands.

### 12. Decision parser can crash on non-integer conviction

Impact: malformed LLM output such as `"high"` for conviction can raise instead of degrade.

Evidence:

- `trading_decision._parse_decision` casts `int(raw.get(...))`.

Expected direction: safe parse with validation and bounded fallback/rejection.

### 13. Market snapshots are read by symbol only

Impact: futures freshness, entry/SL/TP/RR, and position PnL can use a spot snapshot for the same symbol.

Evidence:

- `aletheia/trading/market_data.py`: `latest_snapshot(symbol)` filters by symbol only.
- `aletheia/risk/engine.py`: market-data freshness check calls symbol-only path.
- `aletheia/agent_shell/trading_decision.py`: entry plan uses symbol-only latest snapshot.
- `position_enrichment.py` and context builders also use symbol-only market data in some paths.

Reproduction previously performed:

- Older futures snapshot plus newer spot snapshot caused futures freshness to pass using the spot row.

Expected direction: market data lookup APIs must accept and require market type where trading decisions or risk decisions are involved.

### 14. Dynamic market-data instruments can force spot

Impact: universe members and open positions may be treated as spot instruments even when they are not.

Evidence:

- `aletheia/trading/market_data.py`: `_build_dynamic_instruments()` forces some universe/open-position paths to `MARKET_SPOT`.

Expected direction: derive instrument market type from universe member/position/proposal data.

### 15. Frontend symbol display still has bypasses

Impact: owner rule says spot keeps slash and futures/perp strips slash. Some views bypass `fmtSymbol`, so display can lie.

Evidence:

- `frontend/src/components/PnLZone.tsx`: direct symbol rendering exists.
- `frontend/src/charts/TradeInsightsPanel.tsx`: earlier direct symbol rendering found around symbol tables.
- `frontend/src/charts/TradeInsightsPanel.tsx:449` renders `row.symbol.replace('/USDT', '')` in the Symbol x Direction trade-stat table instead of using `fmtSymbol(row.symbol, row.market_type)`. Because this is a trade analytics table, the slash/no-slash convention matters; stripping `/USDT` unconditionally can make spot look like a derivative-style symbol.

Expected direction: every symbol render with market type must go through `fmtSymbol(symbol, marketType)`.

## Learning Loop Findings

### 16. Trade case builder filters by open time instead of close time

Impact: a trade opened long ago but closed today can be skipped by post-trade learning.

Evidence:

- `trade_case_builder.build_case_studies()` filters by opened time.
- Helper `_opened_after` enforces opened-time window.

Expected direction: post-trade case study windows should use close time for closed-trade learning.

### 17. Dreaming agent can permanently miss case studies

Impact: if case-study creation and dreaming schedule are delayed, lessons can never be extracted.

Evidence:

- `dreaming_agent._extract_lessons` reads only recent 24h-created case studies.
- Scheduler cadence is daily/4h, so delayed generation can fall out of the window.

Expected direction: track processed IDs or use close time / unprocessed status rather than created-at-only sliding window.

### 18. Applied lessons can expire in risk engine but still enter prompt

Impact: LLM can be influenced by lessons that risk logic no longer treats as active.

Evidence:

- `trading_context.py` reads `LessonStore().list_all(status="applied", limit=5)` without `expires_at` filtering.
- Risk engine skips expired lessons.

Expected direction: one shared active-lesson query should be used by both prompt and risk logic.

**Correction (verify pass 2026-07-06 — line drift):** confirmed present. Exact location is `agent_shell/trading_context.py:836` (`list_all(status="applied", limit=5)` with no `expires_at` filter); the risk engine skips expired lessons at `risk/engine.py:416`.

### 19. Lesson spec test is false green

Impact: tests do not prove the risk engine consumes accepted/applied lessons correctly.

Evidence:

- `tests/specs/spec_lesson_guards.py` accepts lessons but does not mark them applied, while runtime reads applied lessons.

Expected direction: spec should cover the real status transition and active lesson query.

### 20. Learning pipeline spec is not hermetic

Impact: spec result depends on production `data/app.db`, so green/red state can vary by machine.

Evidence:

- `tests/specs/spec_learning_pipeline.py::test_layer3_lessons_filter_aligned` depends on existing `trade_case_study` rows.

Verification:

- `uv run pytest tests/specs/ -q -r xX --tb=short` had one failure in this area during the sweep.

Expected direction: create isolated test data in tmp DB.

## Serenity / xStocks Findings

### 21. Serenity opportunity market type is dropped on decision attachment

Impact: a Serenity spot opportunity can become a futures short proposal by fallback.

Evidence:

- `trading_context` stores Serenity opportunity `market_type`.
- `trading_decision._attach_opportunity_context()` reads `o.get("market")` for opportunity context.
- `submit_decision_as_proposal()` defaults short with no market type to futures.

Reproduction previously performed:

- `rNVDA/USDT` short Serenity opportunity with `market_type="spot"` attached `opportunity_id` and `signal_source` but left `market_type=None`.

Expected direction: consistently use `market_type` for Serenity opportunity context.

### 22. xStocks docs disagree on tradability

Impact: one part of the system says tokenized equities are observation-only, another allows tradeable opportunities.

Evidence:

- `research_worker.py` prompt says exchange stock tokens are observation only.
- `docs/design/data_utilization.md` says xStocks are observation-only.
- `docs/design/serenity_contract.md` says `equity_tradeable` can be traded.
- `docs/design/serenity_instrument_roadmap.md` says do not flip execution eligibility.

Expected direction: owner decision needed: observation-only or explicitly tradable with risk/execution constraints.

### 23. Manual Serenity opportunity creation drops asset class / market type

Impact: frontend sends fields that backend ignores, producing misleading opportunities.

Evidence:

- Backend `SerenityOpportunityCreate` lacks `asset_class` and `market_type`.
- Frontend client type includes `asset_class` and `market_type`.
- Pydantic drops extra fields.

Expected direction: either accept and persist the fields or remove them from frontend/API expectations.

**Correction (verify pass 2026-07-06 — overstated):** the backend gap is real (`server/routes/data.py:392` `SerenityOpportunityCreate` omits both fields; create at `:429` never sets them), but `createSerenityOpportunity` (`client.ts:579`) has ZERO callers in `frontend/src` — no page posts these fields. So the "misleading opportunities" impact is not currently reachable; treat as a dormant contract gap, not an active defect. **Severity: low.**

### 24. xStock spot shorts are not clearly blocked

Impact: spot token short can be proposed/executed as sell logic even if exchange/instrument cannot actually short spot tokens.

Evidence:

- `research_worker.py` prompt allows `direction: long|short`.
- Risk engine checks only global short policy, not xStock spot borrowing/shortability.
- Broker maps short to sell.

Expected direction: explicitly disallow or model borrow/short support per xStock instrument.

## Strategy / Evolve / Product Direction Findings

### 25. Strategy Factory remains despite `discussion.md` direction

Impact: owner-facing UI suggests a strategy factory/backtest/Optuna capability that the product direction says should be removed or not emphasized.

Evidence:

- `discussion.md` says rule/DSL/backtest path is wrong and should be removed.
- `frontend/src/pages/Settings.tsx` still exposes Strategy Factory/backtest/Optuna controls.
- `aletheia/server/routes/settings.py` still persists `/settings/strategy_factory`.
- `aletheia/config/schema.py` still defines `StrategyFactoryConfig`.

Expected direction: remove from owner-facing UI or clearly mark dormant/internal if intentionally retained.

### 26. Event trigger strategy design config is dead

Impact: UI/config implies price/volume events can trigger strategy design, but code only notifies/wakes the operator.

Evidence:

- `EventTriggerConfig.trigger_strategy_design` exists in schema/UI/API.
- `price_feed._check_event_triggers()` does not create strategy designs.

Expected direction: remove the field or implement the promised behavior with specs.

### 27. Operator actions include strategy/Optuna actions that are not implemented

Impact: operator capabilities list overstates what the system can do.

Evidence:

- `operator_loop.py` lists `trigger_optuna_sweep`, `retire_strategy`, `demote_strategy`, `trigger_strategy_research`.
- `trigger_optuna_sweep` returns `not_implemented`.
- Spec xfail captures this gap.

Expected direction: remove from active action set or implement with real side effects.

## API / SDD / Documentation Drift

### 28. API reference route list is stale

Impact: docs cannot be used as an SDD source of truth.

Evidence:

- Implemented routes and documented routes diverge.
- Missing examples: `GET /_infra/doctor`, `GET /_infra/status`, `GET /proposals/{id}`, many `POST /operator/internal/*`, `PATCH /operator/internal/tasks/{id}`, `POST /operator/loop/trigger`.
- Stale examples: `/strategies/summary`, `/strategies/{id}`, `/operator/trigger`.

Expected direction: generate or spec-test the API inventory against the FastAPI app.

### 29. Scheduler docs are stale

Impact: operator and tests cannot reason from docs about what jobs actually run.

Evidence:

- Actual scheduler has jobs missing from docs, including `lesson_reviewer`, `connectivity_monitor`, `shadow_resolver`, `persona_rollout`, `news_fetch`, and `outcome_settler`.

Expected direction: sync `agent_contracts.md` and `SYSTEM_MAP.md` with the scheduler registration table.

### 30. Data model docs are stale on position causal links

Impact: docs say position schema lacks links that code now has.

Evidence:

- `docs/design/execution_contract.md`, `docs/governance/trust_and_change_control.md`, and `docs/design/webui_page_map.md` still describe missing proposal/order links.
- Code `Position` includes `proposal_id`, `order_id`, and `opportunity_id`.

Expected direction: update docs to current causal-chain model.

### 31. Live unlock docs are stale

Impact: docs understate current safety implementation.

Evidence:

- `docs/design/live_unlock_contract.md` says scorecard gate is not implemented/xfail.
- Code/tests show live unlock scorecard exists and passes.

Expected direction: update contract and remove stale xfail language.

### 32. Kill-switch API docs are stale

Impact: docs warn release lacks confirmation, but code has confirmation behavior.

Evidence:

- `docs/design/api_reference.md` says release does not enforce confirm token.
- Code has `confirm_release` validation and 422 behavior.

Expected direction: update docs and issue ledger status.

### 33. Permission-mode API docs are misleading

Impact: docs imply one endpoint controls both worker and operator permissions.

Evidence:

- `api_reference.md` says `/settings/permission-mode` handles worker + operator.
- Code comments and allowlist show it only controls worker/agent `Observe/Paper/Full`.

Expected direction: separate worker vs operator permission docs.

### 34. Spec conformance loader has duplication/missing modules

Impact: conformance matrix is not a reliable one-to-one inventory of specs.

Evidence:

- Conformance imports had duplicate `spec_pi_decision_chain`.
- Missing `spec_skill_store` and `spec_universe_distribution_audit` in the matrix during sweep.

Expected direction: make matrix discover specs dynamically or test exact inventory.

## Config / UI Coverage Findings

### 35. MarketDataConfig has many fields not editable in Settings

Impact: user is forced toward config file edits, violating the project rule that users do not edit `data/config.yaml` directly.

Evidence:

- `MarketDataConfig` has fields missing from update API/UI, including `provider`, `enable_crypto_fallback`, `enable_coinalyze`, `coinalyze_api_key`, `price_divergence_threshold_pct`, `price_divergence_alert_enabled`, `cross_source_check_enabled`.

Expected direction: either expose safe controls or explicitly move secret/unsupported fields out of user config.

### 36. GuardConfig UI coverage is incomplete

Impact: guard behavior is partly hidden from owner control.

Evidence:

- `GuardConfig` has multiple fields, while UI exposes mainly `auto_promote_enabled`.

Expected direction: add controls or document why fields are intentionally hidden.

### 37. Universe scanner weights remain read-only/config-file directed

Impact: UI tells owner to use CLI/YAML for some config fields.

Evidence:

- Settings shows `scanner_weights` / `scanner_regime_scoring` as read-only with config-file hints.
- API/client lack direct update fields.

Expected direction: add UI/API controls or remove from user-facing matrix.

### 38. Settings exchange mode values do not line up across UI/API/execution

Impact: user can save modes that execution code does not recognize, or docs describe values code rejects.

Evidence:

- `/settings/exchange-markets` permits `binance_live` and `okx_live`.
- Execution uses `binance_live_limited` and `okx_live_limited`.
- `/settings/exchange-mode` docs say `paper_internal/paper_demo/live`, while code accepts `demo/live`.

Expected direction: normalize enum values and update docs/tests.

**Correction (verify pass 2026-07-06 — overstated):** the enum inconsistency is real (`settings.py:149-150` accepts `binance_live`/`okx_live` and stores them verbatim; execution `gateway.py:112-118` only maps `*_live_limited`), BUT the Settings UI dropdown (`Settings.tsx:1455-1458`) only offers `okx_demo/binance_testnet/bitget_demo/paper_internal` — never the `*_live` values — so a normal Web-UI user cannot trigger it; only a direct API POST can. The "user can save" framing is overstated for the UI-only model. Also note `exchange.mode` (`settings.py:675`, accepts `demo/live`) is a separate field from `paper_execution_mode` — the third bullet conflates them.

### 39. Settings risk UI daily-loss explanation uses the wrong cap for some modes

Impact: owner may see a daily-loss threshold that does not match risk engine behavior.

Evidence:

- Settings text says daily loss formula uses `Live Cap x Max Daily Loss %`.
- Engine uses different basis for live vs demo.

Expected direction: display the actual backend threshold per current mode.

## Operator / Permissions Findings

### 40. Direct pause/resume/wake routes bypass operator permission checks

Impact: command preview can reject pause in ReadOnly while direct HTTP route performs it.

Evidence:

- `/api/operator/pause`, `/api/operator/resume`, and `/api/operator/wake` directly change state.
- Natural-language command routes run permission checks.

Expected direction: route-level permission checks should match command permission checks.

### 41. Internal operator write routes have no caller identity boundary

Impact: if exposed, a caller can forge tasks/messages/events/artifacts/heartbeat.

Evidence:

- `/api/operator/internal/*` write endpoints accept state mutation.
- Tunnel protected path list does not include these internal endpoints.

Expected direction: enforce local-only/internal auth or include in tunnel guard with stricter controls.

### 42. Tunnel auth guard protects only a small subset of trust-boundary endpoints

Impact: when exposed through a tunnel, many sensitive write routes remain outside the guard.

Evidence:

- Protected paths include live unlock/lock, kill switch, gateway restart/stop.
- Missing examples include permission mode, exchange mode, risk settings, operator command, operator internal writes.

Expected direction: review all trust-boundary write APIs and protect or explicitly justify each.

## Audit / Knowledge Findings

### 43. Audit-chain verification tolerates missing first line

Impact: deleting the oldest audit line can still verify as valid due to re-anchor behavior.

Evidence:

- `verify_audit_chain()` treats first-line mismatch as benign/re-anchor.

Reproduction previously performed:

- Deleting the first audit log line still returned valid.

Expected direction: verification should detect truncation unless an explicit rotation checkpoint exists.

### 44. Knowledge hard deletion violates project rule

Impact: `knowledge_items` are physically deleted for `system_action`.

Evidence:

- `repositories.prune_system_actions()` hard-deletes `knowledge_items` rows with `item_type='system_action'`.
- Project rule says hard deletion of knowledge is forbidden; archive/deprecate instead.

Expected direction: move telemetry out of `knowledge_items` or mark archived.

## Frontend / UI Honesty Findings

### 45. Top ticker is not real-time

Impact: `discussion.md` expects BTC/ETH/SOL real-time prices, but UI reads DB snapshots.

Evidence:

- `MarketTicker.tsx` uses `/market/snapshots`.
- Backend route reads SQLite snapshots.
- No market type dimension is used in ticker route.

Expected direction: either implement real-time/cached price feed or label as latest stored snapshot.

### 46. Universe page assumes board-level market type for members/opportunities

Impact: mixed-market rows can display symbols with the wrong slash/no-slash convention.

Evidence:

- `Universe.tsx` uses `snapshot?.market_type` for member/opportunity symbol formatting.
- Comment claims all members share market type.

Expected direction: pass row-level/member-level market type.

### 47. RiskControls exposure market-type map is symbol-only

Impact: same symbol spot/futures exposures can overwrite each other.

Evidence:

- `RiskControls.tsx` builds `marketTypeBySymbol[p.symbol] = p.market_type`.
- Portfolio exposure is keyed by symbol.

Expected direction: exposure should be keyed by instrument identity.

## Universe / Market Outcome Findings

### 48. Universe forward outcomes lack market type

Impact: forward-performance calibration can mix spot and futures for the same symbol.

Evidence:

- `UniverseForwardOutcome` lacks market type.
- `outcome_recorder.register_pending` drops market type.
- `_batch_fetch_prices` calls `provider.get_ticker(sym)` with bare symbol, which defaults to spot.
- Calibration stats group by scanner family/horizon/direction without market type.

Expected direction: include instrument identity in outcome records and calibration queries.

### 49. Data-source direct CCXTProvider exceptions exceed the documented exception list

Impact: docs say direct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` is only for Bitget-only xStocks, but code has other direct construction sites.

Evidence:

- Direct `CCXTProvider` use found in universe registry/features and equity observer paths beyond the documented exception.

Expected direction: either route through `get_market_data_provider()` or update docs/specs with justified exceptions.

## Execution Chain Findings

### 50. Execution position causal links are healthier than docs, but adopted/external paths need continued review

Evidence:

- `Order`, `Fill`, and `Position` models include causal IDs.
- Bitget/Binance/OKX brokers write `proposal_id`, `order_id`, and `opportunity_id` on new positions.
- Docs still claim weaker linkage.

Status:

- Basic execution chain tests passed during sweep:
  `uv run pytest tests/specs/spec_execution.py tests/specs/spec_pipeline_execution_chain.py tests/specs/spec_pipeline_entry_enrichment.py -q --tb=short`

Remaining risk:

- Portfolio/PnL/reconciliation domain is still being reviewed for market-type aggregation and UI truthfulness.

## Portfolio / PnL / Reconciliation Findings

### 51. Portfolio per-symbol PnL can show the wrong sign when spot and futures share a symbol

Impact: the Overview PnL tooltip and Command Center can show a per-symbol unrealized PnL that contradicts total unrealized PnL. Owner may see the right headline number but wrong attribution.

Evidence:

- `aletheia/portfolio/pnl.py`: `compute_portfolio()` returns `unrealized_by_symbol` as a dict comprehension keyed only by `p["symbol"]`.
- If multiple open positions share the same symbol, later rows overwrite earlier rows instead of summing or splitting by instrument.
- `frontend/src/components/PnLZone.tsx` consumes `unrealized_by_symbol` for the Unrealized hover tooltip.
- `frontend/src/pages/CommandCenter.tsx` consumes `unrealized_by_symbol` and finds the first position by symbol to choose display market type.

Temporary-DB reproduction:

- Inserted two open positions in an isolated in-memory DB:
  - `BTC/USDT` spot long, uPnL `+10`.
  - `BTC/USDT` USDT-futures short, uPnL `-20`.
- `compute_portfolio(execution_mode="paper_internal")` returned total unrealized `-10`, but `unrealized_by_symbol` returned `{"BTC/USDT": 10.0}`.

Expected direction: return per-instrument keys such as `(symbol, market_type)` or a list of rows; frontends should render row-level market type.

### 52. Portfolio exposure merges spot and futures into one symbol bucket

Impact: exposure can hide offsetting or duplicated market exposure. RiskControls and PnLZone may show a single BTC exposure while the account actually holds separate spot and futures instruments.

Evidence:

- `aletheia/portfolio/pnl.py`: exposure aggregation uses `exposure[p["symbol"]] += notional`.
- `frontend/src/components/PnLZone.tsx`: Exposure tile builds display text from `exposure_by_symbol`.
- `frontend/src/pages/RiskControls.tsx`: exposure list consumes `portfolio.exposure_by_symbol`.

Temporary-DB reproduction:

- Same two-position setup as above produced `exposure_by_symbol {"BTC/USDT": 330.0}`.
- This value is neither a row-level position exposure nor an instrument-level exposure; it is a symbol-only merge of spot and futures.

Expected direction: expose `exposure_by_instrument` or a structured list with `symbol`, `market_type`, `direction`, `notional`.

### 53. RiskControls chooses display market type using a symbol-only map

Impact: if spot and futures positions share a symbol, the exposure row can be formatted with whichever market type was seen last, so spot can display like futures or futures can display like spot.

Evidence:

- `frontend/src/pages/RiskControls.tsx`: `marketTypeBySymbol[p.symbol] = p.market_type`.
- The exposure rows are keyed by `symbol`, not instrument.

Expected direction: stop using symbol-only exposure keys, or include market type in the backend response.

### 54. Position reconciler drops duplicate local positions that differ only by market type

Impact: reconciliation can fail to report a local open position as managed or orphaned. That makes the UI and reconcile automation blind to part of the book.

Evidence:

- `aletheia/portfolio/position_reconciler.py`: `_key(symbol, direction)` omits market type.
- `classify_positions()` builds `local_by_key: dict[(symbol, direction), dict]`, so duplicate same-symbol same-direction positions overwrite each other.
- Exchange positions use the same key shape and also omit market type.

Temporary-DB reproduction:

- Inserted two local open positions:
  - `ETH/USDT` spot long.
  - `ETH/USDT` USDT-futures long.
- Mocked exchange cache with one `ETH/USDT` spot long.
- `classify_positions()` returned one managed item and no orphaned item; the futures local position disappeared from the result.

Expected direction: reconciliation identity must include market type and broker/account mode, not only symbol and direction.

### 55. Exchange uPnL mapping also omits market type

Impact: exchange unrealized PnL can be applied to the wrong local position when multiple markets share the same symbol and direction.

Evidence:

- `aletheia/portfolio/pnl.py`: `_build_exchange_upnl_index()` returns `(canonical_symbol, normalised_direction) -> exchange uPnL`.
- `mark_positions_to_market()` reads `exc_upnl_index.get((p["symbol"], norm_dir))`.
- No `market_type` or broker/account mode is part of the key.

Expected direction: exchange cache entries and local positions must match by instrument identity.

### 56. `today_pnl` remains semantically misleading

Impact: dashboard "Today PnL" can include all current unrealized PnL since entry, not only today's change. A position opened yesterday and unchanged today can still dominate today's displayed PnL.

Evidence:

- `aletheia/portfolio/pnl.py`: `final_today_pnl = total_unrealized + total_today_realized`.
- `frontend/src/components/AccountSnapshot.tsx`: comment says Today PnL prefers the real day figure when present.
- `frontend/src/components/PnLZone.tsx`: displays `today_pnl` as Today PnL.
- Existing project records already note related semantic drift:
  - `docs/product/EXPERIMENT_BACKLOG.md` mentions `today_pnl` naming/meaning inconsistency.
  - `docs/issues/BACKEND_ISSUES.md` has a `trading_context today_pnl` semantic issue.

Expected direction: split fields into `today_realized_pnl`, `current_unrealized_pnl`, and a clearly named combined field if needed. UI should not label combined lifetime-open unrealized as "Today".

### 57. `today_pnl_by_symbol` and realized PnL group only by symbol

Impact: today's realized symbol breakdown can merge spot and futures, while open-unrealized breakdown has a different, overwrite-prone behavior.

Evidence:

- `aletheia/portfolio/pnl.py`: `today_pnl_by_symbol()` groups by `r.get("symbol")`.
- It does not include market type or execution mode in the output key.

Expected direction: return a structured breakdown keyed by instrument and mode.

### 58. Backup omits the source-of-truth audit JSONL chain

Impact: a backup can restore the SQLite mirror but lose the append-only audit chain. That breaks the project's auditability rule because `data/logs/audit.jsonl` is the durable source of truth and the SQLite `audit_events` table is only a query mirror/best-effort projection.

Evidence:

- `aletheia/backup/service.py`: `create_backup()` copies `app.db`, `config.yaml`, `events/`, and `artifacts/`, but never copies `logs/`.
- `aletheia/audit.py`: `write_audit()` writes JSONL audit entries to the log path and then mirrors into SQLite.
- `AGENTS.md` requires audit logs to describe the real actor/source/entity/state change.

Temporary-directory reproduction:

- Created `data/logs/audit.jsonl` in an isolated temp data directory.
- Ran `BackupService(data_dir=temp).create_backup(label="audit-test")`.
- Inspected the tarball. Result: `has_audit_jsonl=False`, and no log members were present.

Expected direction: backup must include the append-only audit JSONL files or explicitly document and test why the SQLite mirror is considered sufficient. Given current design language, include the JSONL chain.

### 59. Backup copies `app.db` without WAL files or SQLite backup API

Impact: with WAL mode enabled, a raw copy of only `app.db` can miss recently committed data still in `app.db-wal`, or capture a database image that does not represent the current logical database. This weakens trust in backups for orders, fills, positions, and audit mirror rows.

Evidence:

- `aletheia/storage/sqlite.py`: every connection enables `PRAGMA journal_mode=WAL`.
- `aletheia/backup/service.py`: `create_backup()` uses `shutil.copy2(db_path, backup_dir / "app.db")`.
- No checkpoint, SQLite online backup API, or copying of `app.db-wal` / `app.db-shm` is visible in `BackupService`.

Expected direction: use SQLite's backup API from a live connection or perform a safe checkpoint/copy sequence under a clear lock. Add a focused restore test that proves rows committed immediately before backup survive restore.

### 60. Export and backup copy `config.yaml` with sensitive fields unredacted

Impact: exported bundles and backup archives can contain API keys or credential-bearing proxy URLs. This conflicts with the project rule that API keys and secrets must never be written to Markdown/logs/prompts or returned by ordinary GET endpoints; archives are another transport surface and can be shared or inspected by agents.

Evidence:

- `aletheia/sync/exporter.py`: `_export_config()` copies `data/config.yaml` unchanged.
- `aletheia/backup/service.py`: `create_backup()` copies `config.yaml` unchanged.
- Sensitive config fields already identified earlier in this review include `market_data.coinalyze_api_key` and potentially credential-bearing `network.proxy_url`.
- The export manifest sets `secrets_included: False` even when config may contain these values.

Expected direction: config export/backup needs a redaction policy or encrypted-secret split. If full-fidelity backup intentionally includes secrets, the manifest and UI/API must say so honestly and protect the archive path accordingly.

### 61. Artifact export secret filtering misses nested secret files

Impact: full exports and category exports can leak `.env`, `.key`, `.pem`, or `.secret` files if they are nested under an allowed artifact directory. This directly contradicts the exporter comment and manifest claim that secrets are excluded.

Evidence:

- `aletheia/sync/exporter.py`: `_SECRET_PATTERNS` exists, but `_copy_artifacts(categories=None)` checks only the first-level directory path before calling `shutil.copytree()`.
- For category exports, `_copy_artifacts(categories=[...])` calls `shutil.copytree()` with no secret filtering at all.
- The manifest always writes `secrets_included: False`.

Temporary-directory reproduction:

- Created `artifacts/evidence/nested/.env` in an isolated temp data directory.
- Ran `ExportService(data_dir=temp).export_bundle(mode="full-backup")`.
- Inspected tarball members. Result included `artifacts/evidence/nested/.env`.

Expected direction: implement per-file ignore filtering in every artifact copy path and add tests for nested secrets across full and category exports.

### 62. `proposal-pack` ignores `proposal_id` and exports all proposals

Impact: a user asking for a single proposal package can accidentally export unrelated trade proposals. This can leak strategy history and makes the bundle semantically dishonest.

Evidence:

- `aletheia/sync/exporter.py`: `_export_proposal(bundle_dir, proposal_id)` checks that `proposal_id` is present but does not use it.
- `_export_db_tables(..., where_clause=None)` receives no filter, and `_export_db_tables()` ignores its `where_clause` parameter anyway.

Temporary-DB reproduction:

- Created an isolated in-memory SQLite DB and ran migrations.
- Inserted two `trade_proposals`: `prop_a` and `prop_b`.
- Ran `ExportService(data_dir=temp).export_bundle(mode="proposal-pack", proposal_id="prop_a")`.
- Inspected `db/trade_proposals.jsonl`. Result: `exported_ids=['prop_a', 'prop_b']`.

Expected direction: filter `trade_proposals` by the requested ID and include only causally related evidence/claims/artifacts. If complete lineage is not yet knowable, the API should label this as a broader export, not a single-proposal pack.

### 63. Import checksum verification ignores missing files

Impact: a damaged or tampered bundle can pass dry-run checksum verification when `checksums.txt` lists files that are absent from the archive. That makes import preview less trustworthy than backup verification.

Evidence:

- `aletheia/sync/importer.py`: `_verify_checksums()` only calculates a hash inside `if file_path.exists()`.
- If a checksummed file is missing, no error is appended.
- `aletheia/backup/verify.py` handles missing checksum entries correctly, so the stricter behavior already exists elsewhere.

Temporary-archive reproduction:

- Created a tarball with `checksums.txt` containing a hash for `db/missing.jsonl`, but did not include that file.
- Ran `ImportService(data_dir=temp).dry_run(archive)`.
- Result: `{'new_rows': {}, 'skipped_duplicates': 0, 'conflicts': [], 'errors': []}`.

Expected direction: add `"Missing file in bundle: <path>"` errors for every checksum entry whose file is absent.

### 64. Import conflict detection is effectively unimplemented

Impact: if an incoming row has the same primary key as a local row but different content, import silently skips it as a duplicate. The caller sees no conflict, even though the data has diverged.

Evidence:

- `aletheia/sync/importer.py`: `ImportConflict` exists but is never constructed.
- `_import_db_tables()` checks `SELECT 1 FROM {table_name} WHERE {pk_col} = ?`; if found, it increments `skipped_duplicates` and continues.
- It does not compare local row hash vs incoming row hash.

Expected direction: compare canonical row hashes for duplicate primary keys and populate `conflicts` when content differs. Keep true identical rows as skipped duplicates.

### 65. Trade CSV export is orders-only, not tax/PnL-grade trade history

Impact: `/api/trades/export` claims to export trade history for tax/PnL reporting, but it omits the data needed for tax/PnL reconstruction. Owner may believe the CSV is enough for accounting when it is not.

Evidence:

- `aletheia/server/routes/data.py`: endpoint docstring says `Export trade history as CSV for tax/PnL reporting`.
- It exports `repo.list_orders(limit=limit)` only.
- Columns include order metadata such as `created_at`, `symbol`, `direction`, `quantity`, `price`, `status`, `filled_at`, and `exchange_order_id`.
- It does not include fills, fill prices, fill quantities, fees, fee currency, position ID, proposal ID, opportunity ID, realized PnL, close reason, or causal IDs.

Expected direction: either rename this as an order export or join orders/fills/positions/realized PnL into a proper accounting export. The UI/API description must match the actual content.

### 66. Persistence import/verify endpoints accept arbitrary local paths

Impact: if the API is exposed beyond the operator's local machine, these routes can make the backend read arbitrary local files as backup/import targets. The risk is amplified by the earlier finding that route-level tunnel/internal guards are inconsistent.

Evidence:

- `aletheia/server/routes/persistence.py`: `verify_backup_endpoint(path: str)` passes the caller-provided path directly into `BackupVerifier().verify(Path(path))`.
- `import_bundle(req.path)` checks `Path(req.path).exists()` and then passes the caller-provided local path into `ImportService`.
- Paths are not constrained to the configured backups/exports directory.

Expected direction: either enforce local-only/internal guard and document it, or constrain paths to the configured backup/export roots and expose only server-issued archive IDs.

### 67. `requires_confirmation` is only metadata; confirmed commands execute without confirmation

Impact: commands marked as requiring confirmation can execute immediately when called through the backend command executor. This is a direct implementation/document conflict: the UI may show a confirmation affordance, but the actual execution boundary does not require a confirmation token.

Evidence:

- `aletheia/agent_shell/registry.py`: `pause_agents`, `resume_agents`, `run_market_scan`, `rerun_failed_agent_task`, `set_symbol_observe_only`, and `reconcile_positions` set `requires_confirmation=True`.
- `aletheia/agent_shell/executor.py`: `execute_typed_command()` checks permissions and then calls the handler. It never checks a `confirmed`/`confirmation_token` field.
- `aletheia/agent_shell/runtime.py`: `AgentShellRuntime.handle()` also calls handlers directly and only places `needs_confirmation=cmd_def.requires_confirmation` in the returned response.
- `aletheia/server/routes/operator.py`: `/internal/commands/{command_name}` and `/internal/llm-session` call `execute_typed_command()` without any confirmation parameter.
- Existing tests such as `tests/test_operator_routes.py::test_operator_command_persists_structured_response_metadata_for_result_cards` assert that `resume_agents` is `permitted=True` while also returning `needs_confirmation=True`, which locks in the weak behavior.

Temporary-config reproduction:

- In an isolated temp config + in-memory DB, set `agent.permission_mode="Paper"` and `operator.permission_mode="PaperAuto"`.
- Called `execute_typed_command("pause_agents", ..., permission_scope="operator")` with no confirmation field.
- Result: `{'permitted': True, 'needs_confirmation': True, 'paused_after': True, ...}`.

Expected direction: confirmation-required commands should return a non-executing confirmation request until a server-issued confirmation token or explicit confirmed flag is supplied and audited. Tests should prove the state does not change on the first call.

### 68. PI portfolio review closes by symbol only, so it can close the wrong instrument

Impact: if spot and futures positions share the same canonical symbol, the LLM portfolio review can ask to close `BTC/USDT` and the handler will close the first matching open row, regardless of market type, execution mode, direction, or position ID. This is a separate operational manifestation of the earlier symbol-only identity problem because it can trigger a real demo exchange close for the wrong instrument.

Evidence:

- `aletheia/agent_shell/commands.py`: `review_portfolio_state()` asks the LLM for only `target_symbol`.
- It then executes `matching = [p for p in list_positions(status="open") if p["symbol"] == sym]` and closes `matching[:1]`.
- The prompt's position summary includes symbol/direction/qty/uPnL, but not position ID, market type, or execution mode.
- `close_position_routed()` correctly routes the selected row, including real exchange demo closes; the bug is in the ambiguous selector before the router.
- `docs/design/execution_contract.md §10` says PI exits run independently through `review_portfolio_state()` and converge at `close_position_routed()`, but it does not specify that the PI close target must be an unambiguous position ID.

Expected direction: the LLM should select a `position_id` from a structured list that includes market type, execution mode, direction, and size. The handler should reject ambiguous symbol-only closes.

### 69. Network proxy logging can disclose proxy credentials

Impact: if `network.proxy_url` contains credentials, routine network fallback logs can print the full URL, including `user:pass@host`. This extends the earlier proxy-secret risk from config/API surfaces into runtime logs.

Evidence:

- `aletheia/net.py`: `_probe_proxy_sync()` logs `logger.debug("Proxy %s unreachable ...", url)`.
- `invalidate_proxy_cache()` logs `logger.info("Proxy %s marked failed ...", url)`.
- The same file writes `HTTP_PROXY` and `HTTPS_PROXY` from `config.network.proxy_url`.
- There is no URL redaction helper before logging.

Expected direction: parse proxy URLs before logging and replace credentials with `***`, or log only scheme/host/port.

### 70. Agent run error storage persists raw tracebacks and exception text

Impact: agent failures can persist raw tracebacks and exception messages into `agent_runs.errors`, `agent_runs.jsonl`, and audit messages. If any lower layer exception includes a credential-bearing URL, token, prompt fragment, or provider response, the dashboard and logs can retain it permanently.

Evidence:

- `aletheia/agent_runtime/orchestrator.py`: on exception, `errors.append(traceback.format_exc())`.
- The same block writes an audit event with `message=f"Agent {agent_name} raised: {e}"`.
- `aletheia/agent_runtime/run_logger.py`: `end_run()` serializes `errors` directly into `agent_runs.errors`.
- `aletheia/agents/helpers.py`: LLM helper functions return `f"llm_unavailable: {e}"` and log `logger.warning(..., e)`.
- Some HTTP status errors are scrubbed by `raise_for_status_without_secrets()`, but not every possible exception source is guaranteed to be scrubbed, especially proxy/config/OAuth/runtime exceptions.

Expected direction: centralize redaction before writing agent errors or audit messages. Keep full stack traces only in a protected debug surface, or scrub known secret patterns and credential-bearing URLs before persistence.

### 71. Command Center frontend presents confirmation as a label, not a gate

Impact: the UI can show that a command needs confirmation while still sending it through the same one-click `Send` path. Combined with Finding 67, the operator sees a confirmation cue but the system has no actual confirmation stop.

Evidence:

- `frontend/src/pages/CommandCenter.tsx`: command preview renders `confirmation` when `commandPreview.command.requires_confirmation` is true.
- The send button always calls `commandMut.mutate()`; there is no separate confirm step, token, modal, or `confirmed` payload.
- Result cards can render `Executed` and `confirmation` together, which is semantically contradictory for a command that was supposed to wait for confirmation.
- Backend reproduction in Finding 67 proves the command executor executes immediately.

Expected direction: frontend should not use `confirmation` as a passive badge. It should require a second explicit action and send a server-recognized confirmation token/flag, after the backend implements that gate.

### 72. Agent Activity still says reasoning-step backend is missing even though the endpoint exists

Impact: the Agent Activity page understates available transparency and never shows the real step-by-step trace now written by `record_step()`/`log_tool()`. Owner sees a stale "pending backend" message instead of actual recorded inputs/outputs.

Evidence:

- `frontend/src/pages/AgentActivity.tsx`: comments and visible UI say `GET /api/agent-runs/{id}/steps` is "approved-but-unbuilt" and "not yet available".
- `aletheia/server/routes/data.py`: `GET /api/agent-runs/{run_id}/steps` exists and returns `repo.list_run_steps(run_id=run_id, limit=500)`.
- `aletheia/agents/helpers.py`: `log_tool()` and `record_step()` write to `agent_run_steps`.
- `frontend/src/api/client.ts` exposes `getAgentRunToolCalls()` but no `getAgentRunSteps()`.

Expected direction: add `api.getAgentRunSteps()`, render recorded steps in the drawer, and remove the stale "backend pending" copy. Keep tool-calls as a fallback for older runs.

### 73. Conformance matrix does not import every `tests/specs/spec_*.py` file

Impact: an executable spec can exist on disk and be collected by pytest, but still be absent from the conformance matrix that is supposed to show doc-to-code coverage. This makes the SDD coverage report incomplete.

Evidence:

- Scripted comparison of `tests/specs/spec_*.py` against imports in `tests/test_spec_conformance.py`:
  - `spec_files=58`
  - `imported=56`
  - `missing_from_conformance=['spec_skill_store', 'spec_universe_distribution_audit']`
- `tests/specs/spec_universe_distribution_audit.py` contains a real `@spec_claim` / `SpecClaim("UP-DIST-01", "universe_pipeline", ...)`, but it is absent from the conformance matrix.
- `tests/specs/spec_skill_store.py` is named like a spec file but has no `SpecClaim`, so its trust-boundary assertions are invisible to SDD reporting.

Expected direction: make conformance discovery automatic over `tests/specs/spec_*.py`, or add a hard test that fails whenever a spec file with claims is not imported. Files named `spec_*.py` should either use `SpecClaim` or be renamed to ordinary tests.

### 74. `SpecClaim.status` accepts invalid runtime values and the matrix silently prints `?`

Impact: SDD status vocabulary is not enforced. A claim can use a non-contract status and still pass the matrix, which undermines the stated `implemented | partial | missing` lifecycle.

Evidence:

- `tests/specs/spec_meta.py`: `SpecStatus = Literal["implemented", "partial", "missing"]`, but `SpecClaim` is a plain dataclass and performs no runtime validation.
- Registry stats from importing the current conformance matrix:
  - `status={'implemented': 540, 'missing': 5, 'partial': 1, 'superseded': 1}`
  - `bad_status=[('promotion_gates_contract', 'PI-S04', 'superseded', 'test_recommend_promote_gates_paper_running')]`
- `tests/specs/spec_pi_decision_chain.py`: `SpecClaim("PI-S04", ..., status="superseded")`.
- `tests/test_spec_conformance.py`: the matrix icon map knows only `implemented`, `partial`, `missing`; other statuses render as `?` but do not fail any structural test.

Expected direction: validate `SpecClaim.status` in `__post_init__` or in conformance structure tests. If `superseded` is a legitimate lifecycle state, add it explicitly to the SDD methodology and matrix semantics.

### 75. Conformance matrix can be green while documented gaps remain open

Impact: `uv run pytest tests/test_spec_conformance.py` can pass and print high coverage even when the registry contains missing/partial claims and "passing means gap still open" tripwires. This is dangerous for an owner reading "green tests" as "contract implemented".

Evidence:

- `uv run pytest tests/test_spec_conformance.py -q --tb=short` passed: `5 passed`.
- The printed matrix still contained:
  - `missing=5`
  - `partial=1`
  - `superseded=1`
  - Coverage line: `Total: 547 | Implemented: 540 | Coverage: 99%`.
- `tests/specs/spec_persistence.py::test_recovery_fsm_not_yet_wired_into_lifecycle` is marked `status="implemented"` while its invariant says the recovery FSM is *not* wired into lifecycle; passing means the recovery safety net is still inactive.
- `docs/design/persistence_recovery_contract.md` is honest about this gap, but the conformance matrix does not distinguish "implemented safety guarantee" from "implemented gap marker".

Expected direction: conformance should have separate categories for enforced claims, known gaps, and negative tripwires. The command should fail or at least produce a non-green summary when required contracts contain `missing`, `partial`, invalid, or gap-marker claims.

### 76. `docs/design/README.md` Spec-as-Code index is stale and incomplete

Impact: the main L2 contract index no longer reflects the real executable spec surface. This conflicts with SDD practice because the index is supposed to help the owner and future agents find the authoritative executable claims.

Evidence:

- `docs/design/README.md` says the Spec-as-Code table includes files such as `spec_promotion_gates.py`, but no such file exists under `tests/specs/`; promotion-gate claims now live in `spec_pi_decision_chain.py`.
- The table lists old claim counts such as `spec_risk_engine.py | 7`, while the current conformance registry reports `risk_engine_contract` with 38 claims.
- The table omits many current spec files and domains now present in `tests/specs/`, including model routing, market type dedup, persistence, kill conditions, factor feedback, knowledge FTS, equity observer, and the many Serenity-specific specs.
- `docs/design/README.md` says "当前真相看 ... conformance matrix", but the matrix itself is incomplete per Finding 73 and semantically ambiguous per Findings 74-75.

Expected direction: regenerate or maintain the Spec-as-Code index from the actual registry, including claim counts and contract mappings. Mark stale process text as historical if it is no longer authoritative.

### 77. `docs/design/api_reference.md` still documents removed strategy detail/update routes

Impact: the API reference tells the frontend/operator that strategy detail and venue mutation endpoints exist, but the backend currently exposes only the strategy list endpoint. A caller following the document will get 404, and the documentation also conflicts with its own note that strategy records are read-only legacy data after the rules engine removal.

Evidence:

- `docs/design/api_reference.md` lists:
  - `GET /strategies/summary`
  - `GET /strategies/{id}`
  - `PATCH /strategies/{id}/venue`
- Route scan over `aletheia/server/routes/*.py` found only:
  - `GET /api/strategies` at `aletheia/server/routes/data.py:766`
- Targeted search confirmed no backend route decorators for `/strategies/summary`, `/strategies/{id}`, or `/strategies/{id}/venue`.
- The same document says the rules engine was removed and `strategy_records` is now read-only legacy data, but `PATCH /strategies/{id}/venue` still describes a mutating strategy endpoint.

Expected direction: either remove these three rows from the API reference or restore the routes intentionally with tests and an explicit legacy compatibility contract. Given the current product direction, the safer fix is likely documentation cleanup plus a spec check that the API index does not list deleted routes.

### 78. Operator manual trigger route drift: docs say `/operator/trigger`, code exposes `/operator/loop/trigger`

Impact: the documented manual operator-loop trigger path is wrong. Any UI, script, or agent following the API reference will call `POST /operator/trigger` and miss the actual backend route.

Evidence:

- `docs/design/api_reference.md` lists `POST /operator/trigger` in the Operator / Command Center section.
- Actual backend route is `@router.post("/loop/trigger")` at `aletheia/server/routes/operator.py:938`, mounted under `/api/operator`, so the real path is `POST /api/operator/loop/trigger`.
- Route/doc comparison reported:
  - actual but undocumented: `POST /operator/loop/trigger`
  - documented but absent: `POST /operator/trigger`

Expected direction: update `docs/design/api_reference.md` to the real path, or add an explicit compatibility alias if old clients are expected to keep working. If adding an alias, add a route test so the old and new paths cannot drift silently.

### 79. API reference omits real proposal detail endpoint

Impact: the backend exposes a proposal detail endpoint that is not in the endpoint index. This makes the API reference incomplete for the core trading pipeline and weakens the SDD rule that new/deleted API endpoints must be synchronized with `api_reference.md`.

Evidence:

- `aletheia/server/routes/data.py` defines `GET /api/proposals/{proposal_id}` at `data.py:609`.
- `docs/design/api_reference.md` lists:
  - `GET /proposals`
  - `POST /proposals/{id}/approve-paper`
  - `POST /proposals/{id}/reject`
  - `GET /proposals/{id}/risk-decision`
- It does not list `GET /proposals/{id}`.
- Normalized route/doc comparison reported `GET /proposals/{}` as actual but undocumented.

Expected direction: add `GET /proposals/{id}` to the Trading Pipeline section, including a short note that it returns one proposal by ID and 404s when absent. Add or extend the API reference spec so this endpoint is covered.

### 80. Internal operator bridge endpoints are hidden behind a wildcard row, so the endpoint index is not actually complete

Impact: the API reference advertises itself as a complete endpoint index, but it collapses many real internal operator routes into `POST /operator/internal/*` and omits their individual paths. That may be acceptable for a public UI contract, but it conflicts with the document's "complete endpoint index" claim and with SDD endpoint synchronization rules.

Evidence:

- Actual backend routes include individual internal endpoints:
  - `POST /api/operator/internal/artifacts`
  - `POST /api/operator/internal/command-intents`
  - `POST /api/operator/internal/commands/execute`
  - `POST /api/operator/internal/commands/{command_name}`
  - `POST /api/operator/internal/compact`
  - `POST /api/operator/internal/context/query`
  - `POST /api/operator/internal/decisions`
  - `POST /api/operator/internal/events`
  - `POST /api/operator/internal/heartbeat`
  - `POST /api/operator/internal/llm-session`
  - `POST /api/operator/internal/messages`
  - `POST /api/operator/internal/repair-decisions`
  - `POST /api/operator/internal/sessions`
  - `POST /api/operator/internal/tasks`
  - `PATCH /api/operator/internal/tasks/{task_id}`
- `docs/design/api_reference.md` has only `POST /operator/internal/* | Internal operator-shell bridge endpoints; not public UI contracts`.
- The normalized comparison counted these as actual but undocumented and counted the wildcard as documented but not an actual route.

Expected direction: choose one contract style explicitly. If the file is truly a complete endpoint index, list every internal route with "internal only" descriptions. If internal routes are intentionally excluded, rename the document's promise from "complete endpoint index" to "public/UI endpoint index" and add a separate internal bridge contract.

### 81. API reference omits real infra/dev diagnostic routes

Impact: several diagnostic routes exist in code but are missing from the API reference. Some of these are intentionally non-public/dev-only, but the documentation does not define that boundary, so SDD endpoint synchronization is ambiguous.

Evidence:

- Actual routes not listed in `docs/design/api_reference.md`:
  - `GET /_infra/doctor`
  - `GET /_infra/status`
  - `GET /api/dev/reload/status`
  - `GET /api/dev/reload/events`
- `docs/design/api_reference.md` does list `/_infra/livez` and `/_infra/readyz`, so infra endpoints are not categorically excluded.
- `app.py` conditionally includes `dev_reload_router`, but the endpoint index does not say dev reload endpoints are environment-gated or intentionally omitted.

Expected direction: document the infra/dev route boundary. Either add these endpoints to a diagnostics/dev section, or state that dev-only reload routes are intentionally excluded and enforce that distinction in the API-reference spec.

### 82. Runtime continuity write APIs ignore the `runtime_continuity_enabled` kill switch

Impact: the settings UI and config schema present runtime continuity as an experimental feature that is disabled by default, but the backend write routes still allow candidate creation, shadow start, promote, drain, and rollback while the switch is off. This is a safety/state-consistency bug: an owner can believe the feature is disabled while API callers can still mutate runtime state.

Evidence:

- `aletheia/config/schema.py`: `RuntimeConfig.runtime_continuity_enabled: bool = False`.
- `frontend/src/pages/Settings.tsx`: the setting is exposed as "Runtime continuity" with the hint "Experimental: enables shadow gateway staging for zero-disruption upgrades. When off, updates use legacy restart."
- `aletheia/server/routes/runtime.py::_service()` passes `enabled=cfg.runtime.runtime_continuity_enabled` into `RuntimeContinuityService`, but only uses the flag to decide whether to attach `shadow_gateway_launcher`.
- `aletheia/runtime_continuity/service.py` stores `self.enabled` and returns it from `status()`, but no write method checks it. `rg "self\\.enabled|enabled"` in `service.py` shows only constructor assignment and status output.
- Write paths with no enabled check:
  - `prepare_candidate`
  - `start_shadow`
  - `validate_shadow`
  - `promote`
  - `drain`
  - `rollback`
- Existing test coverage locks in the current behavior: `tests/test_runtime_continuity.py::test_runtime_api_and_operator_heartbeat_surface_runtime_metadata` calls `POST /api/operator/runtime/candidate` and `POST /api/operator/runtime/shadow` after `setup_env(...)` without enabling `runtime_continuity_enabled`, and expects 200.
- `tests/test_runtime_continuity.py::test_runtime_api_exposes_and_drains_previous_runtime` similarly posts to `/candidate`, `/shadow`, `/promote`, and `/drain` without enabling the feature, and expects success.

Expected direction: enforce the flag on mutating runtime-continuity routes or in service methods. Keep read-only `GET /api/operator/runtime` available so the dashboard can show recorded state. Tests should assert 409/403 when disabled and success only after enabling.

### 83. Runtime continuity transition routes lack explicit confirmation and accept caller-supplied audit actor

Impact: runtime promotion/rollback/drain changes which code version the local gateway treats as current. These routes currently accept a JSON body with no `confirm` field and let the caller choose `actor`, which weakens audit honesty. The rest of the project treats live unlock, update apply, and update continuity as human-confirmed operations; runtime promotion is at least as stateful as those update operations.

Evidence:

- `aletheia/server/routes/runtime.py` request models:
  - `RuntimeCandidateRequest` has `actor: str = "human"` and no `confirm`.
  - `RuntimeTransitionRequest` has `actor: str = "human"`, `force: bool = False`, and no `confirm`.
- The route handlers pass `payload.actor` directly into the service:
  - `svc.prepare_candidate(actor=payload.actor, ...)`
  - `svc.start_shadow(runtime_id, actor=payload.actor, ...)`
  - `svc.promote(runtime_id, actor=payload.actor, ...)`
  - `svc.drain(runtime_id, actor=payload.actor, ...)`
  - `svc.rollback(actor=payload.actor, ...)`
- The service records that string in `runtime_events.actor`, so a caller can make audit events appear to come from `"system"`, `"human"`, or any arbitrary value.
- By contrast, `aletheia/server/routes/update.py::update_continuity` requires `confirm=true` and hardcodes `actor="human"` before preparing a runtime candidate.
- Existing route tests call `POST /api/operator/runtime/promote` and `POST /api/operator/runtime/drain` without any confirmation token and assert 200.

Expected direction: use server-derived actor/source values for route-level audit fields and require explicit confirmation for promote, rollback, forced drain, and possibly candidate/shadow start. If these endpoints are intended to be operator-internal automation only, document that boundary and gate them through permission mode instead of trusting a caller-provided actor string.

### 84. OAuth callback error page injects unescaped `error_description` into HTML before state validation

Impact: while the local OpenAI OAuth callback server is listening, any browser navigation to `http://127.0.0.1:1455/auth/callback?...` with an `error` query can make the callback page render attacker-controlled HTML. This is local-only, but it is still a security/UI-integrity bug in a credential-adjacent flow.

Evidence:

- `aletheia/model_provider/oauth.py` defines:
  - `_ERROR_HTML = "...<p>{error}</p>..."`
  - in `_handle_callback`, if `error` is present, `desc = request.query.get("error_description", error)` and the response is `text=_ERROR_HTML.format(error=desc)`.
- The `error` branch runs before:
  - checking that `code` exists,
  - checking `state == self._expected_state`.
- There is no `html.escape(desc)` or equivalent before inserting the query value into a `text/html` response.
- OpenAI OAuth uses this callback server (`openai_oauth.py`, redirect URI `http://localhost:1455/auth/callback`). Claude OAuth does not use the local callback server.

Expected direction: validate `state` before rendering provider/caller-supplied error details where possible, and always HTML-escape `error_description`. Keep the displayed message short and generic; put sanitized details only in logs if needed.

**Correction (verify pass 2026-07-06 — overstated, re-scoped):** the described XSS/attacker-HTML injection is NOT reachable. `_ERROR_HTML` (`oauth.py:161-166`) contains literal CSS braces (e.g. `body{font-family:...}`), so `_ERROR_HTML.format(error=desc)` (`:203/210/218`) raises `KeyError('font-family')` BEFORE substituting `{error}` (verified via `uv run python -c`) — the query value is never rendered; the handler raises and returns HTTP 500. The real defect is a **broken/500 error page on every error branch** (a `.format()` bug), not credential-flow XSS. Fix should both `html.escape` and use a placeholder-safe template (double the CSS braces or use a non-`.format` template). **Severity: low (local cosmetic), not security.**

### 85. News analysis endpoint returns an LLM result tuple instead of analysis text

Impact: the News Intel drawer expects `analysis` to be a string, but the backend returns the raw `(content, error)` tuple from `llm_complete()`. In a real FastAPI response this becomes a JSON array, so the UI may render an invalid React child or display a confusing list-like value rather than the analysis text.

Evidence:

- `aletheia/agents/helpers.py::llm_complete` returns `tuple[str | None, str | None]`.
- `aletheia/server/routes/news.py::analyze_news` does:
  - `analysis = await llm_complete("Market impact analysis", prompt)`
  - returns `"analysis": analysis`
- `llm_complete` normally catches provider failures and returns `(None, "llm_unavailable: ...")`; it does not raise. Therefore the surrounding `except Exception` in `analyze_news` does not handle ordinary LLM outages.
- Narrow reproduction with `llm_complete` patched to return `("impact text", None)` showed:
  - `type(result["analysis"]).__name__ == "tuple"`
  - `result["analysis"] == ("impact text", None)`
- `frontend/src/pages/NewsIntel.tsx` stores `data.analysis` in `analysis` state and renders it directly inside `<p>{analysis}</p>`.

Expected direction: unpack `content, error = await llm_complete(...)`. Return a string analysis on success and a clear unavailable/error string on failure. Consider not marking the news item `reviewed` when analysis fails.

### 86. Prediction preferences save is wired to POST in the frontend, but backend only exposes PATCH

Impact: the Prediction Lens preferences UI cannot save observed categories. The backend route and API reference agree on `PATCH /prediction/preferences`, but the frontend client sends `POST /prediction/preferences`, which will hit no route and fail.

Evidence:

- `aletheia/server/routes/prediction.py` defines `@router.patch("/preferences")`.
- `docs/design/api_reference.md` lists `PATCH /prediction/preferences | Update preferences`.
- `frontend/src/api/client.ts` defines:
  - `updatePredictionPreferences: (body) => post<any>('/prediction/preferences', body)`
- `frontend/src/pages/PredictionLens.tsx::PreferencesTab` calls `api.updatePredictionPreferences({ categories })` in the save mutation.
- The route extraction from the API audit confirmed there is no `POST /api/prediction/preferences`; only `PATCH /api/prediction/preferences` exists.

Expected direction: change the frontend client helper to use PATCH, or add a backend POST compatibility route and document it. The API reference already points to PATCH, so the smallest aligned fix is the client method.

### 87. Capital deposit/withdraw events mutate equity baseline without audit logging

Impact: manual capital events directly change net deposits and therefore equity-curve interpretation. These are trust-bearing financial state changes, but the route writes only the `capital_events` row and emits no audit record. This weakens post-hoc reconstruction of why performance baselines moved.

Evidence:

- `frontend/src/pages/Settings.tsx::CapitalEventsCard` lets the user create deposits/withdrawals from Settings.
- `aletheia/server/routes/capital.py::create_event` validates type/amount, builds `CapitalEvent`, calls `repo.insert_capital_event(event)`, and returns `{"id": event.id, "status": "created"}`.
- `aletheia/storage/repositories.py::insert_capital_event` is a plain `_insert("capital_events", ...)`.
- No `write_audit` call exists in `capital.py` or `insert_capital_event`.
- `docs/design/audit_chain_contract.md` says trust-bearing system events should be written to `audit.jsonl`; `docs/design/api_reference.md` explicitly treats trust-boundary state changes as requiring audit. Capital deposits/withdrawals are not trading orders, but they materially alter the owner's performance truth.

Expected direction: emit an audit event after successful capital event insert with actor/source, event_type such as `capital_event_created`, entity_id set to the capital event ID, and before/after fields for net deposits or at least the event payload. Add a route test.

### 88. Capital routes run migrations on every request inside async handlers

Impact: the capital endpoints do synchronous SQLite migration work on every request from `async def` handlers. This repeats the exact pattern the project rules call out as a known source of event-loop stalls and SQLite lock contention.

Evidence:

- `aletheia/server/routes/capital.py`:
  - `_ensure_db()` calls `run_migrations()` unconditionally.
  - `get_net_deposits`, `list_events`, and `create_event` are `async def` and call `_ensure_db()` on every request.
- `rg -n "run_migrations\\(\\)" aletheia/server/routes -S` shows:
  - `data.py` explicitly changed its `_ensure_db()` to a no-op because per-request migrations were "the dominant `database is locked` source".
  - `prediction.py` gates migrations behind a process-local `_db_ready` flag.
  - `capital.py` still runs migrations every time.
- The project API performance rule says initialization operations must run once, not on every request, and sync SQLite work inside `async def` handlers can freeze the uvicorn event loop.

Expected direction: match the data/prediction route pattern: rely on app startup migrations or guard the first-run check with a flag. Since these are async endpoints, avoid per-request synchronous DB setup.

### 89. Scheduler job documentation is stale/incomplete against the real 33-job scheduler

Impact: the SDD rule says scheduler job frequency changes must sync `agent_contracts.md` and `SYSTEM_MAP.md`. The current documents no longer give a complete, internally consistent map of autonomous jobs. This matters because scheduler jobs are the live trading system's background behavior; missing rows hide real state changes and side effects from review.

Evidence:

- AST extraction from `aletheia/agent_runtime/scheduler.py` found 33 registered jobs:
  - `market_data`, `market_observer`, `dreaming_daily`, `lesson_reviewer`, `insight_synthesizer`, `confidence_calibration`, `reconciliation`, `portfolio_snapshot`, `exchange_sync`, `paper_trader`, `pi_trading_decision`, `price_feed`, `connectivity_monitor`, `position_manager`, `position_reconcile`, `tpsl_fill_check`, `cleanup`, `shadow_resolver`, `health_check`, `persona_rollout`, `news_fetch`, `equity_observer`, `prediction_observer`, `post_trade_reviewer`, `risk_critic`, `universe_refresh`, `outcome_settler`, `data_collectors`, `guard_promotion`, `okx_demo_symbols`, `daily_summary`, `pi_operator_loop`, `ohlcv_nightly_backfill`.
- `docs/design/agent_contracts.md` is internally inconsistent:
  - header says "32 scheduler jobs";
  - section title says "Scheduler Job Overview (31 jobs)";
  - parsed table has only 28 rows.
- `agent_contracts.md` table omits real registered jobs:
  - `connectivity_monitor`
  - `lesson_reviewer`
  - `news_fetch`
  - `outcome_settler`
  - `shadow_resolver`
- `docs/design/SYSTEM_MAP.md` lists 23 scheduler jobs and omits 10 registered jobs:
  - `cleanup`
  - `connectivity_monitor`
  - `daily_summary`
  - `health_check`
  - `lesson_reviewer`
  - `news_fetch`
  - `outcome_settler`
  - `persona_rollout`
  - `reconciliation`
  - `shadow_resolver`
- Some omitted jobs are not decorative: `news_fetch` writes news data, `outcome_settler` feeds Universe learning outcomes, `shadow_resolver` feeds analytics, and `connectivity_monitor` can pause/resume health state.

Expected direction: regenerate scheduler job tables from code or add a spec that compares `scheduler.py` registered job IDs to the documentation tables. Distinguish public product pipelines from maintenance jobs if desired, but do not label a partial table as the scheduler overview/source map.

### 90. High-frequency TPSL fill checker calls Bitget through `asyncio.run()` without the scheduler hard-timeout wrapper

Impact: `tpsl_fill_check` runs every 90 seconds on the `io` executor and calls Bitget plan-order history for open positions with TP/SL orders. Unlike most external-IO jobs, it is not routed through `_run_async_with_timeout` or `_run_sync_with_timeout`, and it has no `_ASYNC_JOB_TIMEOUT_SEC` entry. If an exchange call hangs or asyncio teardown stalls, the single `io` worker can be occupied and delay reconciliation, position management support jobs, and other state-maintenance work.

Evidence:

- Scheduler registration: `id="tpsl_fill_check"`, interval `seconds=90`, executor `io`.
- `_ASYNC_JOB_TIMEOUT_SEC` has no `tpsl_fill_check` key.
- `_run_tpsl_fill_check()`:
  - imports `BitgetClient`;
  - defines async `_check_all()`;
  - awaits `client.get_plan_order_history(order_id=order_id)` for each candidate TP/SL order;
  - runs the coroutine via plain `asyncio.run(_check_all())`.
- Nearby external-IO jobs use timeout wrappers:
  - `market_data` uses `_run_async_with_timeout("market_data", ...)`;
  - `price_feed` uses `_run_async_with_timeout("price_feed", ...)`;
  - `connectivity_monitor` uses `_run_async_with_timeout("connectivity_monitor", ...)`;
  - `exchange_sync` uses `_run_sync_with_timeout("exchange_sync", ...)`.
- The scheduler comments explicitly say hard wall-clock bounds exist to keep stuck external I/O from starving scheduler pools and holding SQLite state indefinitely.

Expected direction: add a bounded wrapper for `tpsl_fill_check`, with a timeout key and a test that the registered high-frequency exchange poller cannot run unbounded. If it must remain sync, route the whole body through `_run_sync_with_timeout`.

### 91. Universe market discovery and feature collection bypass the required market-data provider factory outside the documented xStocks exception

Impact: Universe registry refresh and feature collection fetch exchange markets, tickers, and OHLCV through direct `CCXTProvider(...)` construction. That conflicts with the project rule that market data must go through `get_market_data_provider()` so each call gets a fresh event-loop-local provider while preserving the configured primary/fallback chain. It also conflicts with `ccxt_provider.py`'s own documentation, which says the direct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` construction is reserved for Bitget-only xStocks. If Universe intentionally needs a second low-level exception for exchange market discovery, the exception is not documented or enforced by spec.

Evidence:

- `aletheia/data/ccxt_provider.py` documents the factory rule and says the direct Bitget provider is only for ON-series xStocks.
- `aletheia/trading/market_data.py` follows that rule: normal crypto market data calls `get_market_data_provider()`, while the xStocks path has an explicit "Deliberate exception" comment before constructing `CCXTProvider(exchange="bitget", fallback_exchange="bitget")`.
- `aletheia/universe/registry.py:105` constructs `CCXTProvider(exchange=venue, fallback_exchange=venue)` directly, then calls `provider._ensure_primary()` and `exchange.load_markets()` to populate `universe_registry`.
- `aletheia/universe/features.py:331` constructs `CCXTProvider(exchange=venue, fallback_exchange=venue)` directly, then calls the raw CCXT exchange for batch tickers and OHLCV during feature collection.
- These paths are market-data paths, not execution adapter boundaries. They are also not the documented ON-series xStocks exception.
- `tests/specs/spec_data_layer.py` checks that `get_market_data_provider()` itself exists and is not cached, but it does not scan the codebase for forbidden direct provider construction sites. As a result, this architecture rule can drift without the SDD checks noticing.

Expected direction: either route Universe collection through `get_market_data_provider(exchange=venue)` or explicitly document Universe market discovery as a second allowed exception with a focused spec that distinguishes it from ordinary market-data reads. If direct CCXT access is required for `load_markets()`, the exception should be recorded in `docs/design/data_utilization.md` and covered by a spec that prevents unrelated direct provider use from spreading.

### 92. MacroCollector calls blocking yfinance directly inside an async data-collector tick

Impact: every Phase-B data collector tick can enter synchronous Yahoo Finance calls from inside `MacroCollector.collect()`. This defeats the collector's per-step `asyncio.wait_for(..., timeout=15)` budget: a coroutine blocked inside a synchronous `yf.Ticker(...).history(...)` call cannot be cancelled until the blocking call returns. In the bad case, the data collector thread waits until the outer scheduler wrapper abandons the thread after its timeout/grace window. This is exactly the class of issue the project documents elsewhere as requiring executor offload for yfinance.

Evidence:

- `aletheia/data/collectors/macro.py:74` calls `_fetch_tradfi()` directly from `async def collect(...)` when FRED values are missing or no `FRED_API_KEY` is configured.
- `_fetch_tradfi()` at `aletheia/data/collectors/macro.py:203` is synchronous and calls `yf.Ticker(ticker_sym)` plus `t.history(period="2d")` at lines 214-215.
- `aletheia/agent_runtime/scheduler.py:1693` wraps `macro_coll.collect(symbols)` in `_bounded(..., "macro", 15.0, ...)`, and `_bounded()` uses `asyncio.wait_for(coro, timeout=...)` at line 1614. That timeout cannot preempt a synchronous network call running inside the event loop thread.
- The project already encodes the correct yfinance pattern in `aletheia/data/yfinance_provider.py`: its module comment says yfinance is synchronous/blocking and should run in the default executor, and `fetch_macro_context()` uses `loop.run_in_executor(None, _fetch_macro_sync)` at line 84.
- Tests cover F&G isolation and stub `_fetch_tradfi`, but there is no spec/test that prevents blocking yfinance calls from being made directly inside `MacroCollector.collect()`.

Expected direction: convert the yfinance fallback in `MacroCollector` to the same executor pattern used by `yfinance_provider.py`, or remove the fallback from the scheduler-backed collector and keep FRED/CoinGecko/F&G as non-blocking HTTP-only paths. Add a focused test that `_fetch_tradfi` is not called directly on the async event loop.

### 93. Risk lesson hard-blocks are not limited to risk-category lessons despite the engine contract saying they are

Impact: any applied lesson with a `recommendation_json.action` of `block_symbol`, `block_direction`, or `block_scanner` can become a hard risk-engine rejection, even if the lesson category is `general`, `entry_timing`, `exit_timing`, or another non-risk category. This widens a learning output into a trading block beyond the stated contract and makes LLM/dreaming classification mistakes more dangerous: the action field alone controls enforcement.

Evidence:

- `aletheia/risk/engine.py::_check_lesson_constraints()` docstring says it checks "Lessons with category='risk' and recommendation_json containing..." block actions.
- The implementation loads `active_lessons = store.list_all(status="applied", limit=50)`, parses `lesson.recommendation_json`, and branches on `rec.get("action")`, but never checks `lesson.category`.
- `tests/specs/spec_lesson_guards.py` creates only `category="risk"` lessons and does not cover a non-risk lesson with block-style `recommendation_json`.
- `docs/design/learning_contract.md` describes `category` as a separate lesson field and says risk checks enforce non-expired block actions, but does not lock the category boundary in a spec.

Expected direction: decide whether hard enforcement is intentionally action-driven or risk-category-only. If category is meant to matter, `_check_lesson_constraints()` should skip non-risk lessons before reading block actions, and `spec_lesson_guards.py` should add a regression test proving a non-risk applied lesson cannot block a trade. If action-driven enforcement is intended, update the risk/learning docs to remove the misleading category='risk' claim.

### 94. Strategy engine deletion is not fully reflected in code: write-capable lifecycle FSM and stale strategy_designer references remain

Impact: the Position C docs say the rules engine is deleted and `strategy_records` / `backtest_*` tables are legacy read-only historical data. The exposed application paths appear mostly removed, but the codebase still contains the full write-capable strategy lifecycle FSM and stale `strategy_designer` references. That creates a misleading maintenance surface: future code can accidentally re-wire dead promotion gates or old strategy-designer expectations even though the current product contract says this path is gone.

Evidence:

- `docs/design/agent_contracts.md` says the rules engine (`strategy_designer` + `backtest_orchestrator`) was deleted and `strategy_records` / `backtest_runs` / `backtest_trades` are kept read-only.
- `docs/design/data_model.md` marks `strategy_records`, `strategy_experiments`, `backtest_runs`, and `backtest_trades` as "Legacy read-only".
- `docs/plans/strategy_engine_deletion_position_c_2026-07-02.md` says Phase B should shrink `strategy_lifecycle.py` to an attribution helper.
- `aletheia/trading/strategy_lifecycle.py` still defines:
  - `create_strategy()`, which inserts a new `StrategyRecord`;
  - `promote_strategy()`, which calls `update_strategy(...)` and can transition states;
  - `check_promotion_gates()` and `check_live_limited_gates()`, the old promotion-gate mechanics.
- `rg` found no active callers for `create_strategy()`, `promote_strategy()`, `check_promotion_gates()`, or `check_live_limited_gates()` outside `strategy_lifecycle.py` itself, so this is currently dead code rather than a live route.
- `aletheia/agent_shell/operator_recommendations.py` still contains `_expected_schedule("strategy_designer") == "daily at 01:00"` and queries recent `agent_runs` for `agent_name="strategy_designer"`, even though the agent was deleted.

Expected direction: either complete the Position C shrink by removing or clearly quarantining write-capable FSM functions, or update the docs to say these functions are intentionally retained but unexposed. Add a spec that prevents deleted-agent names (`strategy_designer`, `backtest_orchestrator`) from reappearing in active recommendation/scheduler surfaces, while allowing historical data reads.

### 95. Frontend API client still exposes a deleted strategy endpoint, and the IA test cannot see it

Impact: the visible strategy page/navigation removal is partially protected, but the frontend API client still contains a method that calls a backend route that no longer exists. There is no current TSX caller, so this is not a live broken button today. The risk is future regression: a later UI change can re-use `setStrategyVenue()` and immediately wire users to a 404 while the existing "no strategies route" test still passes.

Evidence:

- `frontend/src/api/client.ts:537-538` defines `setStrategyVenue(id, execution_venue)` and sends `PATCH /strategies/${id}/venue`.
- The backend data route exposes `@router.get("/strategies")` at `aletheia/server/routes/data.py:766`, but no `PATCH /strategies/{id}/venue` handler exists in the route module.
- `tests/test_frontend_navigation_ia.py:48` scans only `FRONTEND.rglob("*.tsx")`; the stale endpoint is in `frontend/src/api/client.ts`, so the test cannot catch it.
- `tests/test_frontend_navigation_ia.py:51-52` only looks for literal `"/strategies"` hits in TSX page/component files, which proves navigation/page links are gone but not that the client API surface is clean.
- This is adjacent to, but distinct from, Finding #94. #94 covers deleted strategy-engine backend/FSM residue; this finding covers a frontend API-client residue plus a test blind spot.

Expected direction: remove `setStrategyVenue()` if strategy venue editing is truly deleted, or reintroduce/document the backend route if it is intentionally retained. Expand the IA/deletion regression test to scan `.ts` API client files for deleted strategy endpoints, while allowing explicitly documented read-only historical strategy queries.

### 96. Trade Insights symbol-direction matrix conflates spot and derivative trades

Impact: the "Symbol x Direction Win Rate" analytics table can mix spot and derivative trades for the same canonical symbol and direction into one win-rate/average-PnL row. That makes the displayed performance metric operationally misleading: a BTC/USDT spot long and a BTCUSDT perp long have different market mechanics, display rules, and risk interpretation, but the matrix reports them as one bucket.

Evidence:

- `aletheia/analytics/trade_insights.py:20` selects `symbol, market_type, direction` from `positions`, so the source data has the field needed to keep instruments separate.
- `_symbol_direction_matrix()` at `aletheia/analytics/trade_insights.py:388` declares `cells: dict[tuple[str, str], list[float]]`, omitting `market_type`.
- The grouping loop at `aletheia/analytics/trade_insights.py:390-392` builds keys from only `sym` and `direction`.
- The returned row at `aletheia/analytics/trade_insights.py:400-408` includes `symbol`, `direction`, `win_rate`, `avg_pnl_pct`, and `trade_count`, but no `market_type`.
- The frontend type `SymbolDirectionEntry` in `frontend/src/api/client.ts:980-986` likewise has no `market_type`, so the UI cannot repair the ambiguity.
- `frontend/src/charts/TradeInsightsPanel.tsx:449` then renders `row.symbol.replace('/USDT', '')`, compounding the same ambiguity by stripping the spot slash unconditionally.

Expected direction: aggregate by `(symbol, market_type, direction)` and return `market_type` in each matrix row. Update the frontend type and render with `fmtSymbol(row.symbol, row.market_type)`. Add a focused analytics test with one spot and one perp row sharing the same canonical symbol/direction and assert they remain separate.

### 97. System Operator rename is not complete across active config/API/audit contracts

Impact: `discussion.md` explicitly says the old "PI Agent" name has been replaced by "System Operator" and asks that this rename be applied thoroughly. The visible settings label has mostly moved to "System Operator daemon", but active config fields, REST paths, client method names, and audit event/entity names still use `pi_daemon`. That keeps the old mental model alive in the product contract and makes SDD/API docs harder to reason about: new contributors and future tests have to know that "PI daemon" and "System Operator daemon" are the same subsystem.

Evidence:

- `discussion.md:24` says the top-level AI agent was formerly PI Agent and is now System Operator, and asks that the rename be applied more thoroughly across the project.
- `discussion.md:100` repeats that "PI agent" is now "system operator" and says not to forget the rename.
- `frontend/src/pages/Settings.tsx:553` shows the user-facing setting as `System Operator daemon (event-driven)`, so the UI copy has partly moved.
- `frontend/src/pages/Settings.tsx:69` still keeps `pi daemon` as a Settings search alias.
- `frontend/src/pages/Settings.tsx:341-342` calls `api.updatePiDaemonConfig(...)`.
- `frontend/src/pages/Settings.tsx:355` reads `settings?.pi_daemon`.
- `aletheia/server/routes/settings.py:89` returns a `pi_daemon` config object from `GET /api/settings`.
- `aletheia/server/routes/settings.py:314-315` exposes `POST /api/settings/pi_daemon`.
- `aletheia/server/routes/settings.py:327-330` writes audit records with `event_type="pi_daemon_config_changed"`, `entity_type="pi_daemon_config"`, and message text `PI daemon config updated`.
- `aletheia/config/schema.py:742` defines `PiDaemonConfig`; `aletheia/config/schema.py:818` stores it as `pi_daemon`.
- Some historical docs/tests may reasonably preserve old terminology when explaining migration history, but the paths above are active contracts, not archive-only references.

Expected direction: choose a migration plan that separates external compatibility from internal/product language. At minimum, document `pi_daemon` as a backward-compatible legacy key for the System Operator daemon and expose new `system_operator_daemon` naming in docs/API/UI where feasible. Add a small naming-regression test that prevents new active UI copy, audit messages, and public docs from reintroducing "PI daemon" except in explicitly allowed legacy/compatibility contexts.

### 98. Entry-plan gate checks that stop/TP/RR exist, but not that they are on the correct side of entry

Impact: `discussion.md` says every entry must commit entry price, stop loss, take profit, and expected reward:risk before opening, so the trade is a planned bet rather than a gambler-style entry. The current gate enforces field presence for native limit entries, but it does not validate the geometry of the plan. A long proposal can carry a stop above entry and a take-profit below entry, or a short can carry the inverse, and the risk engine still treats the entry plan as complete. Because `trading_decision.py` computes reward:risk with absolute distances, an inverted plan can still produce a positive `risk_reward` and pass the current gate.

Evidence:

- `discussion.md:66` says that before entry the system must clearly confirm entry price, take-profit/stop-loss points, and expected reward:risk.
- `aletheia/storage/models.py:122-129` documents the same entry-discipline intent: entry, stop, take-profit, and reward:risk must be committed before the limit order.
- `aletheia/risk/engine.py:100-117` checks only missing/zero fields for native limit entries: `price`, `stop_price`, `take_profit`, and positive `risk_reward`. It does not check `stop_price < price < take_profit` for long or `take_profit < price < stop_price` for short.
- `aletheia/agent_shell/trading_decision.py:319-321` computes `_risk_per_unit = abs(price - stop_price)` and `_reward_per_unit = abs(take_profit - price)`, so an upside-down stop/TP pair can still become a positive `risk_reward`.
- `tests/specs/spec_pi_decision_chain.py:363-409` covers default limit order type, missing fields, complete fields, and market-order exemption, but it does not test wrong-side stop/TP geometry.
- Temporary reproduction with `eng.insert_risk_decision = lambda decision: None`:
  - proposal: `direction="long"`, `price=50000`, `stop_price=51000`, `take_profit=49000`, `risk_reward=1.0`, `order_type="limit"`, `source="native"`;
  - `eng.evaluate_proposal(...)` returned `approved=True` and `violations=[]`.

Expected direction: add a semantic entry-plan validator at the risk gate, not only in the proposal builder. For long entries require `stop_price < price < take_profit`; for short entries require `take_profit < price < stop_price`; reject zero/equal distances and optionally recompute/compare `risk_reward` against the submitted value. Add spec coverage for wrong-side long and wrong-side short proposals.

### 99. Lesson reviewer can approve scanner-specific block lessons using only global/symbol evidence

Impact: the Learning contract says structured lessons become active guidance or risk constraints after evidence-gated review. `dreaming_agent` can generate a `block_scanner` recommendation from trade-case evidence, and once applied the risk engine can hard-reject proposals from that scanner. But `lesson_reviewer._fetch_evidence()` does not evaluate scanner-family evidence. It only builds a system-wide aggregate and, if recoverable, a symbol-specific slice. A scanner-specific block can therefore be accepted by the LLM against unrelated global/symbol outcome statistics, then become a hard trading block for a scanner family without the reviewer ever checking that scanner family's settled edge.

Evidence:

- `docs/design/learning_contract.md:35-41` says trade evidence becomes `ps_lessons`, reviewer accepts/rejects, applied lessons feed the System Operator prompt and risk checks.
- `docs/design/learning_contract.md:99-105` says risk checks consume applied lessons and enforce non-expired block actions.
- `aletheia/agents/dreaming_agent.py:202-206` permits recommendations including `{"action": "block_scanner", "scanner": "<scanner_family>"}`.
- `aletheia/agents/dreaming_agent.py:273-281` can deterministically generate `block_scanner` from aggregate loss patterns and `scanner_families`.
- `aletheia/agents/lesson_reviewer.py:54-110` queries `universe_forward_outcomes` for only:
  - system-wide aggregate rows from the last 30 days at `horizon='24h'`;
  - optional `symbol_stats` when a symbol can be recovered.
- `_fetch_evidence()` parses `lesson.recommendation_json` only for `rec.get("symbol")`; it does not read `rec.get("scanner")`, nor `scanner_families` from `lesson.evidence_json`.
- `universe_forward_outcomes` has scanner-family data: `storage/migrations.py:1092` defines `scanner_family TEXT`, and `storage/repositories.py:1592-1618` already aggregates grouped scanner-family outcomes.
- `storage/repositories.py:1623-1651` also has `family_direction_edge()`, grouping settled outcomes by `(scanner_family, direction)`.
- `tests/specs/spec_learning_pipeline.py:500-554` proves the reviewer can promote/apply when enough settled evidence exists and the LLM returns accept, but it seeds one generic set of `momentum` outcomes and does not assert that a `block_scanner` recommendation is judged against scanner-specific evidence or rejected when scanner-specific evidence contradicts the global aggregate.
- This is distinct from Finding #93: #93 covers category boundaries at risk enforcement time; this finding covers evidence mismatch at lesson promotion time.

Expected direction: make `_fetch_evidence()` recommendation-aware. For `block_scanner`, recover the scanner family and include scanner-family and preferably scanner+direction stats; for `block_direction`, include direction-specific stats; for `block_symbol`, include symbol stats. A hard-block lesson should not be applied unless the evidence pack contains enough samples for the exact dimension it will later block. Add a behavioral spec where global evidence is strong but the targeted scanner family is not, and assert the reviewer defers/rejects the scanner block.

### 100. Manual Serenity opportunity creation can bypass the evidence-derived assessed tier

Impact: Serenity's central safety rule is that an opportunity's scoring/promotion tier must be derived from independently assessed supporting evidence, not from the opportunity author's own tier. The automated research path follows that rule, but the manual `POST /serenity/opportunities` route directly copies the submitted `evidence_tier` into `assessed_tier` and labels it `assessed_by="operator"`. The route does not require linked supporting evidence, an explicit assessment payload, or a separate confirmation that the caller is making an operator-grade evidence judgment. A manually created opportunity with no evidence can therefore score as `disclosed` instead of the documented speculative floor.

Evidence:

- `docs/design/serenity_contract.md:39` states the generator's tier is only advisory and scoring/promotion must use the independently assigned `assessed_tier`.
- `docs/design/serenity_contract.md:86` says `assess_opportunity_tier()` derives the opportunity tier from the strongest supporting evidence and returns `speculative` when there is no supporting evidence, even if the opportunity self-grades as `disclosed`.
- `aletheia/trading/serenity_evidence.py:186-210` implements that exact behavior and intentionally does not trust the `opportunity` argument.
- The automated research path in `aletheia/agents/research_worker.py:776-779` follows the contract: it loads theme evidence, calls `assess_opportunity_tier({}, theme_evidence)`, and stores that result as `assessed_tier`.
- The manual API path in `aletheia/server/routes/data.py:422-445` does not call `assess_opportunity_tier()`. It normalizes `payload.evidence_tier`, writes the same value to both `evidence_tier` and `assessed_tier`, and sets `assessed_by="operator"`.
- `tests/test_serenity_routes.py:62-73` posts a manual opportunity with `evidence_tier="disclosed"`, but only asserts route success and score shape. It does not assert that evidence-derived assessment is preserved or that an evidence-less manual opportunity is floored.
- `tests/specs/spec_serenity_evidence.py:173-178` proves the pure evaluator floors an evidence-less opportunity to `speculative`, but there is no route-level regression test that the public create endpoint respects that invariant.
- Temporary reproduction of scoring impact:
  - `score_serenity_opportunity({"evidence_tier":"disclosed","assessed_tier":"disclosed"}, evidence=[], risk_gates=[])["evidence_score"]` returned `28`;
  - `score_serenity_opportunity({"evidence_tier":"disclosed"}, evidence=[], risk_gates=[])["evidence_score"]` returned `10`.

Expected direction: either make manual opportunity creation run the same evidence-derived assessment as the research path, or split the API into a clearly named operator-assessment workflow with explicit audit/confirmation and documented authority. Add a route-level test where a manual opportunity is submitted as `disclosed` with no supporting evidence and assert the persisted/scored `assessed_tier` cannot be silently elevated.

### 101. Serenity opportunity detail UI displays proposed tier, not authoritative assessed tier

Impact: the Serenity UI can show the user the opportunity's proposed/self-graded evidence tier while the backend scores and promotes using the authoritative `assessed_tier`. This violates UI honesty for Serenity: the operator looking at a theme detail row may see a higher "Tier" than the deterministic evaluator actually granted. That is especially risky because the adjacent evidence table correctly displays assessed evidence tier, so the opportunity table appears to mean the same thing while reading a different field.

Evidence:

- `docs/design/serenity_contract.md:39` says `evidence_tier`/`tier` is advisory and `assessed_tier` is the scoring/promotion authority.
- `aletheia/server/routes/data.py:162-169` returns `payload = dict(opportunity)` plus `serenity_score`, so backend responses include `assessed_tier` when present in the stored opportunity row.
- `frontend/src/api/client.ts:114-135` defines `SerenityOpportunity` with `evidence_tier`, but no `assessed_tier` or `researchability_grade`, even though both are part of the backend/data contract.
- `frontend/src/pages/SerenityLens.tsx:657` correctly renders evidence rows with `humanize(e.assessed_tier || e.tier)`.
- `frontend/src/pages/SerenityLens.tsx:692-700` renders opportunity rows with `researchability_grade` from an untyped `any`, but passes `evidenceTier={humanize(o.evidence_tier)}`.
- `frontend/src/pages/SerenityLens.tsx:566-571` labels that value simply as `Tier`, with no indication that it is the proposed tier rather than the authoritative assessed tier.
- The route tests and frontend type checks do not catch the mismatch: `tests/test_serenity_routes.py` checks score presence but not UI field semantics, and `frontend/src/api/client.ts` lacks the typed field needed for normal component use.

Expected direction: add `assessed_tier` and `researchability_grade` to the frontend `SerenityOpportunity` type, render the opportunity Tier column from `assessed_tier` with a fallback only for legacy rows, and if the proposed tier is still useful, show it under a distinct "Proposed" label. Add a focused UI/type regression so new Serenity opportunity views cannot display `evidence_tier` as the authoritative tier.

### 102. Serenity market_type is lost when a promoted or fallback pick is attached to a TradingDecision

Impact: Serenity opportunities carry `asset_class` and `market_type` so the System Operator can distinguish normal crypto spot, future stock-token swaps, and other instrument-specific execution paths. That field does reach Layer 3 context, but `_attach_opportunity_context()` does not actually copy it into the `TradingDecision` for Serenity matches. In the fallback branch it reads `o.get("market")` even though Serenity context uses `market_type`; in the promoted pending-signal branch it returns immediately after setting id/source and never looks up the opportunity's instrument fields. The later proposal builder then falls back to `"spot"` for longs and `"usdt_futures"` for shorts, which can silently change the intended instrument for Serenity-sourced picks.

Evidence:

- `docs/design/serenity_contract.md:178-182` says `research_worker` stamps stock-token opportunities with `asset_class="equity_tradeable"` and a `market_type`, and `trading_context` allows those to be executable rather than observation-only.
- `tests/specs/spec_serenity_xstock_execution.py:5-11` describes the same execution path: `research_worker` stamps `asset_class`, `trading_context` exposes execution eligibility, and `trading_decision` should allow those opportunities to anchor a trade.
- `aletheia/agents/research_worker.py:765-806` stores `_asset_class` and `_market_type` on each `SerenityOpportunity`.
- `aletheia/agent_shell/trading_context.py:791-822` copies those values into Layer 3 as `"asset_class": _asset_class` and `"market_type": _market_type`.
- `aletheia/server/routes/data.py:1518-1524` and `aletheia/agents/research_worker.py:839-845` create promoted `PiDecision(action="signal_pending", signal_source="serenity")` rows with symbol/direction/opportunity_id, but `PiDecision` has no `market_type` or `asset_class` field (`aletheia/storage/models.py:919-946`).
- `aletheia/agent_shell/trading_context.py:637-650` loads pending signals with only symbol, direction, reasoning, created_at, opportunity_id, and signal_source; no instrument fields are available in the direct pending-signal context.
- `aletheia/agent_shell/trading_decision.py:1281-1298` handles the Serenity pending-signal direct match by setting `opportunity_id` and `signal_source`, then returns without copying market type from either the pending signal or the source opportunity.
- `aletheia/agent_shell/trading_decision.py:1300-1322` handles the Layer 3 Serenity fallback but checks `o.get("market")`; the actual key is `market_type`, so `decision.market_type` remains unset.
- `aletheia/agent_shell/trading_decision.py:356-359` then falls back to `"usdt_futures"` for shorts and `"spot"` for longs.
- Temporary reproduction with a Layer 3 Serenity opportunity containing `"market_type": "usdt_futures"` produced `decision.signal_source == "serenity"` and `decision.market_type is None`.
- Temporary reproduction with a matching promoted pending signal and a Layer 3 opportunity containing `"market_type": "usdt_futures"` also produced `decision.signal_source == "serenity"` and `decision.market_type is None`.
- The existing specs are too structural to catch this: `spec_serenity_xstock_execution.py` verifies eligibility strings and `spec_serenity_attribution.py` verifies id/source attachment, but neither asserts market_type propagation into `TradingDecision`.

Expected direction: in `_attach_opportunity_context()`, copy `market_type` from Serenity Layer 3 with `o.get("market_type")` (optionally accepting legacy `market`). For direct promoted pending-signal matches, either include market_type/asset_class in the pending signal context by joining `serenity_opportunities`, or after matching by `opportunity_id`, look up the Serenity opportunity and copy its instrument fields before returning. Add a behavioral spec that constructs a Serenity opportunity with non-default `market_type` and asserts the resulting `TradingDecision.market_type` is preserved.

### 103. Serenity auto PostMortem computes holding time from the pre-close position row

Impact: Serenity's long-horizon feedback loop writes an automatic `SerenityPostMortem` when a Serenity position closes, and that post-mortem is later consumed by learning/dreaming flows. The current hook receives the position row as it looked before `close_position()` updated `closed_at`, so the generated attribution string records `hold_h=0` instead of the real holding duration. This pollutes exactly the data Serenity needs most: how long a thesis took to resolve.

Evidence:

- `docs/design/serenity_contract.md:162` says Serenity is a long-horizon school with separate holding behavior and a close-time PostMortem feedback loop.
- `docs/design/serenity_contract.md:162-163` and `spec_serenity_holding.py:466-472` treat close-time PostMortem creation as part of closing the Serenity attribution/learning loop.
- `aletheia/portfolio/pnl.py:152-164` updates the position with `status="closed"`, `current_price=exit_price`, `close_reason=reason`, and `closed_at=datetime.now(...)`.
- `aletheia/portfolio/pnl.py:200-203` then calls `_close_serenity_feedback(row, realized, reason)`, but `row` is the pre-update result from `repo._select_one("positions", position_id)` at `pnl.py:110`.
- `_close_serenity_feedback()` at `aletheia/portfolio/pnl.py:512` computes `hold_h` with `_compute_holding_hours(position.get("opened_at"), position.get("closed_at"))`; the pre-update row does not have the new `closed_at`.
- Temporary reproduction: a Serenity position opened 48 hours ago was closed with `exit_price=58000` and `reason="stop_loss"`. The inserted post-mortem attribution was `trade_school=serenity close_reason=stop_loss pnl=-20.71 pnl_pct=-3.5% hold_h=0`.
- `tests/test_serenity_p0_p3_e2e.py:613-630` asserts a post-mortem exists and contains symbol/reason, but does not assert `hold_h` or any close-time field accuracy.

Expected direction: pass the updated position row to `_close_serenity_feedback()` or pass the explicit `exit_price` and `closed_at` values into the hook. Add a focused regression that creates an opened-hours-ago Serenity position, closes it, and asserts the post-mortem attribution contains a non-zero/expected holding duration.

### 104. Serenity execution plan promises an explicit thesis-invalidated marker, but the implemented path only supports status changes

Impact: the Serenity execution plan says a long-horizon position exits when either the linked opportunity is no longer active or an explicit `thesis_invalidated` marker is set. The code and model only implement the first half. This matters operationally: an operator or research process may want to mark the thesis broken while preserving richer lifecycle status, but there is no explicit field/API path for that. As written, the only way to trigger the exit is to overload opportunity `status`, which collapses lifecycle state and thesis validity into one string.

Evidence:

- `docs/plans/serenity_execution_path.md:165` states the MVP thesis-invalidation exit should close when the linked opportunity `status != active` or an explicit `thesis_invalidated` marker is present.
- `docs/plans/serenity_execution_path.md:11` marks the plan as reconciled/implemented, so this is not clearly archived as an abandoned idea.
- `aletheia/storage/models.py:410-438` defines `SerenityOpportunity` fields including `status`, `verdict_json`, `researchability_grade`, `asset_class`, and `market_type`, but no `thesis_invalidated` or equivalent boolean/marker field.
- `aletheia/storage/migrations.py` contains additive columns for `verdict_json`, `researchability_grade`, `asset_class`, and `market_type`, but no thesis-invalidated column.
- `aletheia/portfolio/position_manager.py:247-253` implements thesis exit solely as `if opp and opp.get("status") != "active": return "thesis_invalidated", price`.
- `tests/test_serenity_p0_p3_e2e.py:212-232` covers `dismissed` and `expired` statuses, but does not cover an explicit thesis invalidation marker because no such marker exists.
- A repository-wide search found `invalidation` stored inside `verdict_json` as a textual condition and used in prompts/context, but no code path that evaluates a persisted `thesis_invalidated` flag to close a Serenity position.

Expected direction: choose one contract. If status-only is the intended MVP, update `docs/plans/serenity_execution_path.md` or the normative `serenity_contract.md` to remove the explicit-marker promise and explain that status is the single control. If explicit invalidation is intended, add a model/API field and a behavioral test proving a Serenity position exits when that marker is set while preserving status semantics.

### 105. Execution contract still documents deleted RuleSet auto-exit as an active exit trigger

Impact: the current SDD docs are internally inconsistent about who may close positions. `agent_contracts.md` and `spec_pipeline_position_manager.py` correctly say RuleSet auto-close was removed so exits are controlled by SL/TP/max-hold/market-aware/System Operator. But `execution_contract.md`, which is the execution-path gold-standard document, still lists `ruleset_exit` as priority 4 in the active exit trigger table. That is a high-risk documentation bug: an implementer following the execution contract could reintroduce a deterministic rule-based close path that the current tests explicitly forbid.

Evidence:

- `docs/design/execution_contract.md:163-171` says `_decide_exit()` uses a fixed priority order and lists `ruleset_exit` as priority 4, with a location pointing to old `position_manager.py:348-349`.
- `docs/design/agent_contracts.md:121-124` says RuleSet exit auto-close was removed with the rules engine and `paper_trader` must not close positions directly.
- `docs/design/agent_contracts.md:145-153` lists `position_manager` exit triggers as stop_loss, take_profit, max_hold_time, trailing_stop, and market_aware_exit, and explicitly says RuleSet exit auto-close is removed.
- `tests/specs/spec_pipeline_position_manager.py:431-472` encodes `PL-PM-08`: RuleSet exit signals must not auto-close positions, and neither `position_manager` nor `paper_trader` may wire RuleSet exits into `close_position`.
- `aletheia/portfolio/position_manager.py:323-328` has the correct implementation note that RuleSet exit signals no longer auto-close positions.
- `aletheia/portfolio/position_manager.py:310-314` still has a stale function docstring saying `evaluate_open_positions()` checks RuleSet exit signals, so both the contract doc and one local docstring need cleanup.
- Repository search confirms `scan_active_rulesets`, `_handle_ruleset_exit`, and `ruleset_exit_signal` no longer appear in active exit code, only in docs/tests/historical files.

Expected direction: update `docs/design/execution_contract.md §10.1` to remove `ruleset_exit` from the active priority table and renumber/describe `market_aware_exit` accordingly. Update the `evaluate_open_positions()` docstring to match the implementation. Keep `PL-PM-08` as the executable guard against reintroducing this bypass.

### 106. System Operator portfolio-review close path selects positions by symbol only

Impact: `review_portfolio_state()` lets the System Operator close or reduce exposure, but its structured output schema only asks the LLM for `target_symbol`. The handler then closes the first open position with that symbol. If there are multiple open positions for the same symbol, for example spot long plus perp short, different market types, or multiple entries with different causal IDs, the command can close the wrong position. This violates the causal-ID discipline around positions and is weaker than the anti-hallucination safeguards already present in `TradingDecision` close/reduce attribution.

Evidence:

- `docs/design/execution_contract.md:173` says System Operator exits run independently through `review_portfolio_state()` and then converge on the same `close_position_routed()` central route.
- `aletheia/agent_shell/commands.py:1614-1623` defines the portfolio-review schema with `action`, `target_symbol`, and `reasoning`; there is no `position_id`, `market_type`, `direction`, or quantity target.
- The prompt at `aletheia/agent_shell/commands.py:1635-1641` asks the model to specify `target_symbol` for `close_one` or `reduce_exposure`.
- The close implementation at `aletheia/agent_shell/commands.py:1662-1667` runs `matching = [p for p in list_positions(status="open") if p["symbol"] == sym]` and then iterates `for pos in matching[:1]`.
- `list_positions(status="open")` uses `_select_all(... order="rowid DESC")` by default (`aletheia/storage/repositories.py:19-27`, `509-512`), so the chosen position is whichever matching row is newest, not necessarily the one the model intended.
- `tests/test_anti_hallucination_e2e.py:128-184` has a stronger guard for the separate `TradingDecision` close/reduce path: it backfills from one unambiguous open position and refuses to guess when multiple open positions are ambiguous. No equivalent guard exists for `review_portfolio_state()`.
- Repository search found no tests for `review_portfolio_state()` ambiguity, `target_symbol` disambiguation, or `positions_closed` choosing the intended position.

Expected direction: change the portfolio-review schema and prompt to include a stable `position_id` (and ideally market_type/direction as context), and refuse to close when the target is ambiguous. Add a regression with two open positions sharing the same symbol and assert `review_portfolio_state()` does not close either unless the returned target identifies a unique position.

### 107. Agent Activity manual-run buttons expose stale agents and omit the current equity observer

Impact: the Agent Activity page is one of the operator-facing controls for manually starting agents. Its hardcoded button list is stale: it exposes `strategy_designer`, which the current agent contract says was deleted, and `stock_observer`, which the current contract says was removed/superseded by `equity_observer`. It also omits the current `equity_observer` entry. This creates a misleading control surface: the user can click a deleted or obsolete task, while the supported equity scanner is not available from the same UI.

Evidence:

- `frontend/src/pages/AgentActivity.tsx:33-37` hardcodes `AGENTS = ['market_observer', 'research_agent', 'strategy_designer', 'risk_critic', 'portfolio_agent', 'post_trade_reviewer', 'dreaming_agent', 'prediction_observer', 'stock_observer']`.
- `frontend/src/pages/AgentActivity.tsx:103-107` renders every `AGENTS` entry as a live `Run an agent now` button that calls `api.startAgentRun(a)`.
- `aletheia/server/routes/data.py:251-256` accepts the posted `agent_name` and passes it directly to `run_agent(req.agent_name, trigger_type="manual")`; there is no API-side allowlist aligned to the current contract.
- `aletheia/agent_runtime/orchestrator.py:29-31` resolves the name with `get_agent_fn(agent_name)` and runs the returned function. Unknown names are caught later as run errors and surfaced as `completed_with_errors`, not rejected before the UI reports the mutation success path.
- `aletheia/agents/__init__.py` imports `aletheia.agents.{name}` dynamically and raises `ValueError("Unknown agent: ...")` if no registered function exists.
- `aletheia/agents/strategy_designer.py` and `aletheia/agents/backtest_orchestrator.py` do not exist (`Test-Path` returned `False` for both).
- `docs/design/agent_contracts.md:157-164` says the rules engine (`strategy_designer` + `backtest_orchestrator`) was deleted and strategy generation/backtesting no longer exist.
- `docs/design/agent_contracts.md:61-70` says `equity_observer` replaces `stock_observer`, and `stock_observer` was removed/superseded.
- The codebase still has a registered `stock_observer` implementation (`aletheia/agents/stock_observer.py:288-290`) and a registered `equity_observer` implementation (`aletheia/agents/equity_observer.py:443-445`), so the frontend currently points users at the obsolete equity path while hiding the documented current path.

Expected direction: derive the manual-run list from a backend registry or an explicit contract-tested allowlist, not a stale frontend constant. Remove deleted/superseded entries from the active UI, add `equity_observer`, and make `POST /api/agent-runs/start` reject unknown/deleted names with a clear 4xx response instead of recording a failed run after the UI reports success. Add a regression that the UI/allowed manual-run list excludes deleted names and includes current documented manual entries.

### 108. Learning page still presents legacy strategy lifecycle as active paper-strategy progress

Impact: the current Position C contract says the old strategy engine was deleted and `strategy_records` are legacy read-only historical data. The Learning page still presents strategy lifecycle state as a current learning KPI: "Strategies in paper" and "Reports = performance and strategy progress". That can make the owner think there is still an active paper-strategy promotion system, even though the current decision path has moved to System Operator decisions, signal-pending queues, guards, and Serenity promotion into PI evaluation.

Evidence:

- `docs/design/agent_contracts.md:157-165` says Strategy Agents were removed, the rules engine (`strategy_designer` + `backtest_orchestrator`) was deleted, and `strategy_records` / `backtest_runs` / `backtest_trades` are kept read-only for historical attribution.
- `docs/design/data_model.md:60` marks `strategy_records` as **Legacy read-only** after rules-engine removal, kept for `strategy_id` attribution.
- `frontend/src/pages/Learning.tsx:16-27` still describes the page's Reports area as "Portfolio PnL + a strategy-promotion board showing each paper strategy's distance to its next lifecycle gate."
- `frontend/src/pages/Learning.tsx:36` tells the user the page includes "performance and strategy progress."
- `frontend/src/pages/Learning.tsx:56` queries `api.getStrategies()`.
- `frontend/src/pages/Learning.tsx:66-77` filters `StrategyRecord` rows with `state !== 'draft' && state !== 'retired'` and renders that count as `Strategies in paper`.
- `frontend/src/api/client.ts:536` still exposes `getStrategies: () => fetchJson<any[]>('/strategies')`.
- `aletheia/server/routes/data.py:766-769` exposes `GET /api/strategies` and returns `repo.list_strategies()`, which is a raw legacy table read.
- `aletheia/storage/repositories.py:547-548` implements `list_strategies()` as `_select_all("strategy_records", limit=limit)` with no legacy/historical labeling or filter.
- `frontend/src/types.ts:251-262` still models `StrategyRecord` with active lifecycle fields such as `state`, `execution_venue`, `direction_affinity`, and `applicable_regimes`.

Expected direction: change the Learning page to align with the current learning contract. Either remove the `Strategies in paper` KPI and stale strategy-promotion/progress copy, or relabel any `strategy_records` display as historical/legacy attribution only. Prefer current learning signals instead: lesson counts, applied guards, contradictions needing review, System Operator lesson visibility, Serenity active opportunities, and recent post-trade reviews. Add a frontend/contract test that Position C-deleted strategy lifecycle concepts do not appear as active learning progress.

### 109. Exchange entry partial fills are collapsed into completed local orders and executed proposals

Impact: for exchange demo/live entry orders, a real partial fill can be recorded as a fully completed local order and the parent proposal is marked `executed`. That overstates order completion, hides remaining unfilled quantity, and conflicts with the execution contract's partial-fill rule. The close-position path already treats `partially_filled` as `partial`, so entry and exit accounting are inconsistent.

Evidence:

- `docs/design/execution_contract.md:121` says partial fills should leave `Order status=submitted`, record only the filled quantity in `Fill`, and size the position by the actual filled quantity.
- `aletheia/execution/bitget_broker.py:120-142` maps `partially_filled` into the `filled` branch, inserts a fill, then immediately overwrites the local order to `status="filled"`.
- `aletheia/execution/binance_broker.py:103-120` has the same pattern.
- `aletheia/execution/okx_broker.py:183-200` has the same pattern.
- The three brokers return no order/exchange status in their entry result bodies (`bitget_broker.py:210-217`, `binance_broker.py:175-182`, `okx_broker.py:255-262`), so the gateway cannot distinguish full fills from partial fills.
- `aletheia/execution/gateway.py:324` and `gateway.py:409` mark demo/live proposals as `status="executed"` unconditionally after the broker returns, even when the exchange response was only partial.
- Entry brokers use the exchange/client spelling `partially_filled`, while close handling and reconciliation use local spelling `partial` (`reconciliation.py:26`, `bitget_broker.py:327`, `binance_broker.py:283`, `okx_broker.py:413`). If a post-submit local write fails after the entry order is backfilled but before it is overwritten to `filled`, the stranded `partially_filled` order is not in reconciliation's open-state set.
- `tests/test_client_partial_fill.py:1-14` protects client-level partial-fill detection, but not broker/gateway status semantics.
- `tests/test_exchange_close_confirmation.py:82-87` protects the close path by mapping `partially_filled` to `partial`, showing the intended safer behavior exists for exits but not entries.
- `tests/test_bitget_broker.py:39-41` comments that fills/positions are recorded for `filled/partially_filled`, but the test uses only `status="filled"` and does not assert partial order status or proposal status.

Expected direction: preserve exchange entry status through broker results, keep local orders `partial`/`submitted` instead of `filled` until the exchange confirms completion, and avoid marking the parent proposal as fully `executed` while quantity remains open. Add focused broker/gateway regressions for Bitget, Binance, and OKX where the exchange returns `partially_filled` with less than requested quantity.

### 110. NewsIntel manual analysis does not feed the analyzed-news research path

Impact: the NewsIntel page exposes an "Analyze Impact" action, but that action only returns the LLM text to the current browser drawer and marks the row `reviewed`. It does not persist the result into the fields and status used by the research pipeline. A user can analyze a news item in the UI and reasonably expect it to become available as analyzed market context, but `research_worker` will still ignore it.

Evidence:

- `frontend/src/pages/NewsIntel.tsx:204-214` renders "Analyze Impact" and calls `api.analyzeNews(selected.id)`.
- `aletheia/server/routes/news.py:91-115` calls `llm_complete(...)`, returns `analysis`, and updates only `status = "reviewed"`.
- `news.py:111-115` includes `analyzed_at` only in the response body; it does not write `impact_summary` or `analyzed_at` to `news_items`.
- `aletheia/storage/migrations.py:1523-1527` explicitly adds `impact_summary` and `analyzed_at` columns for news collector analysis.
- `aletheia/data/news_analyzer.py:76` and `news_analyzer.py:90` show the intended analyzed-news persistence shape: `status = 'analyzed'`, `analyzed_at`, and `impact_summary`.
- `aletheia/agents/research_worker.py:78-103` injects only rows where `status = 'analyzed'` and reads `impact_summary`; rows marked `reviewed` by the UI route are invisible to this path.
- `docs/design/data_utilization.md:51` says news/sentiment data in `news_items` is consumed by `market_observer` and `research_worker`, so a UI action labeled as analysis should not dead-end outside those consumers.
- Repository search found no test that `POST /api/news/{id}/analyze` persists `impact_summary`, `analyzed_at`, or `status='analyzed'`.

Expected direction: route manual UI analysis through the same persistence contract as `data/news_analyzer.py`, or label it as a temporary UI-only note. Prefer writing `impact_summary`, `analyzed_at`, and `status='analyzed'` on success; if the LLM call fails, keep the row `new` or store a clear failure state instead of silently moving it to a non-consumed `reviewed` state. Add a route-level regression asserting analyzed manual news appears in `_get_recent_news_for_research()`.

### 111. PredictionLens market drawer uses external Polymarket IDs against internal prediction-market endpoints

Impact: PredictionLens market cards can render, but opening a card can lose the local probability history, generated claims, and linked Serenity evidence. The card's `market_id` is the external Polymarket/Gamma market id from opportunity metadata, while `/prediction/markets/{market_id}/history`, `/claims`, and Serenity `source_ref` use the internal `prediction_markets.id`. The UI therefore asks internal-table endpoints with the wrong identifier.

Evidence:

- `aletheia/agents/prediction_observer.py:90-134` persists normalized `prediction_markets` and returns `market_id_map[external_market_id] = internal_market_id`.
- `prediction_observer.py:126-133` writes probability snapshots using the internal market id.
- `prediction_observer.py:137-186` and `prediction_observer.py:367-386` write prediction claims using `internal_market_id`.
- `prediction_observer.py:410-419` writes Serenity evidence with `source="prediction_market"` and `source_ref=internal_id`.
- But `prediction_observer.py:182-198` serializes opportunity metadata with `"market_id": market.market_id`, which is the external Polymarket id.
- `aletheia/server/routes/prediction.py:96-126` builds PredictionLens cards from prediction opportunities and returns `market_id: meta.get("market_id", "")`, preserving the external id.
- `frontend/src/pages/PredictionLens.tsx:363-371` calls `api.getPredictionMarketHistory(market.market_id)` and `api.getPredictionMarketClaims(market.market_id)`.
- `frontend/src/pages/PredictionLens.tsx:339-346` links Serenity evidence by `e.source_ref === marketId`, again comparing internal `source_ref` to the external id from the card.
- `aletheia/server/routes/prediction.py:178-203` looks up snapshots and fallback market detail by the supplied `market_id`; `repositories.py:305-316` use this value as `prediction_markets.id` / `prediction_probability_snapshots.market_id`.
- `repositories.py:326-335` filters prediction claims by internal `market_id`.
- Existing spec coverage in `tests/specs/spec_subsystem_observers.py:170-202` checks only that prediction opportunities are observation-only and `asset_class="prediction"`; it does not assert the ID bridge between opportunity metadata, normalized market rows, history, claims, and Serenity evidence.

Expected direction: store both IDs explicitly in opportunity metadata, for example `external_market_id` and `internal_market_id`, and have the `/prediction/markets` card API return the internal id for local history/claims plus the external id for provider calls/display. Update PredictionLens to use the internal id for local endpoints and Serenity joins. Add a regression that one observed market's card can retrieve its snapshots, claims, and linked Serenity evidence.

### 112. Proposal reject route can overwrite already executed proposals

Impact: a direct API call can change an already executed proposal back to `rejected` after an order and position have been created. That breaks the trading-state story: the proposal row says the human rejected it, while downstream records still show execution happened. The dashboard normally renders Reject only for pending rows, but the backend route is exposed and has no state guard.

Evidence:

- `docs/design/execution_contract.md:35` defines proposal status flow as `pending -> approved -> executed` or `pending -> rejected` / `error`; `execution_contract.md:42` says `executed` means the order was successfully created and `rejected` means a human or risk rejection.
- `aletheia/server/routes/data.py:653-664` implements `POST /proposals/{proposal_id}/reject` by fetching the row and unconditionally calling `repo.update_trade_proposal(proposal_id, status="rejected")`.
- The reject route does not inspect `p.status`, does not reject terminal/conflicting states, and does not preserve `before` / `after` status in the audit event.
- `tests/test_workflow_routes.py:84-90` covers only rejecting a newly inserted pending proposal.
- `tests/test_workflow_routes.py:73-80` separately proves an approved proposal can become `executed`, but there is no regression that `POST /reject` refuses an already executed proposal.
- `docs/design/api_reference.md:25` names approving an already executed proposal as a `409 State conflict` example; the same state-conflict rule is missing from reject behavior even though rejecting an executed proposal is more state-corrupting.

Expected direction: make reject idempotent only for already rejected proposals and return a state-conflict error for `approved`, `executed`, and `error` states unless a documented reversal workflow exists. Include `before` and `after` status in the rejection audit event. Add a route regression that executes a proposal, then proves `POST /reject` cannot change it to rejected or erase the executed state.

### 113. Approval Desk treats backend execution failures as successful UI mutations

Impact: the Approval Desk comment says the operator must see approve/reject failures, but the approval API can return HTTP 200 with `execution.status = "error"`, `rejected`, or `already_executed`. The frontend API wrapper only throws on non-2xx responses, and the approval mutation ignores the returned execution status. A failed paper execution can therefore clear the inline error state and follow the success path, leaving the operator to infer failure only after a list refresh.

Evidence:

- `frontend/src/pages/Proposals.tsx:72-74` explicitly documents the trust requirement: approval/rejection failure must be visible because silent failure means the button resets while no order landed.
- `frontend/src/pages/Proposals.tsx:78-81` calls `api.approveProposal(id)` and on success clears the proposal error and invalidates queries; it does not inspect `r.execution.status`.
- `frontend/src/api/client.ts:51-62` makes `fetchJson()` throw only when `res.ok` is false. A JSON body with a business failure status is treated as success.
- `aletheia/server/routes/data.py:629-632` returns HTTP 200 for already executed, error, and rejected proposal states.
- `data.py:641-648` catches execution exceptions, changes the proposal to `error`, and returns HTTP 200 with `{"execution": {"status": "error", "message": str(e)}}`.
- `tests/test_workflow_routes.py:78-80` currently codifies the `already_executed` 200 response. There is no frontend or API-client regression that an execution `error` body is surfaced as an approval failure.

Expected direction: choose one consistent contract. Prefer returning non-2xx for business failures that should stop the operator flow, matching `api_reference.md`'s state-conflict guidance, or make the frontend unwrap `execution.status` and render non-`executed` statuses as visible errors. Add a regression where `execute_existing_proposal()` fails and the UI/API client does not clear the inline error.

### 114. Approval confirmation can imply non-paper execution while backend always forces paper

Impact: the Approval Desk displays each proposal's `execution_mode` and confirms that mode before approval, but the only approval endpoint is `approve-paper` and the backend always calls `execute_existing_proposal(..., execution_mode="paper_internal")`. If a pending proposal carries `demo`, `live`, or another non-paper mode, the human confirmation can say that mode while the actual order path is forced into paper simulation. That is semantically dishonest even if paper-only approval is the intended safety posture.

Evidence:

- `docs/design/api_reference.md:66` documents `POST /proposals/{id}/approve-paper` as "Approve proposal for paper execution".
- `aletheia/server/routes/data.py:619-649` implements only `approve-paper`; it writes an audit message "Proposal approved for paper execution" and calls `execute_existing_proposal(proposal_id, execution_mode="paper_internal")`.
- `frontend/src/pages/Proposals.tsx:57-63` includes `execution_mode` as a visible table/card field.
- `Proposals.tsx:95-99` sets the confirmation mode label to `"Paper simulation - no real order."` only when `p.execution_mode === "paper_internal"`; otherwise it displays `p.execution_mode` directly.
- `frontend/src/api/client.ts:527` maps `approveProposal()` only to `/proposals/{id}/approve-paper`; there is no separate UI action for demo/live approval.
- Existing workflow tests in `tests/test_workflow_routes.py` cover paper approval only and do not cover the operator-facing confirmation text for non-paper proposal modes.

Expected direction: if the dashboard approval desk is intentionally paper-only, make the UI label and confirmation say that the action will execute as paper regardless of the proposal's requested mode, while separately showing the requested mode as metadata. If non-paper approval is intended later, add explicit routes, permission gates, and tests rather than overloading `approve-paper`.

### 115. Markets candlestick chart drops market type and defaults futures symbols to spot OHLCV

Impact: the Markets page can label a board or symbol as futures/perp while the actual candlestick panel requests spot candles. This can put spot price action under a perp/futures decision surface, exactly where users inspect entries, signals, and trade markers. The page already knows the board market type, but the chart component does not receive or forward it.

Evidence:

- `frontend/src/pages/Charts.tsx:367` reads `boardMarketType` from `universe.snapshot.market_type`.
- `Charts.tsx:453` and `Charts.tsx:464` pass that `marketType` to `SymbolStrip` and `ChartPanel`, so the page has the instrument context for display.
- Inside `ChartPanel`, `Charts.tsx:156` renders `<PriceChart symbol={symbol} timeframe={timeframe} markers={markers} ... />` without any market type prop.
- `frontend/src/charts/PriceChart.tsx:52` defines props only as `symbol`, `timeframe`, `markers`, and `onMarkerClick`.
- `PriceChart.tsx:106`, `PriceChart.tsx:133`, and `PriceChart.tsx:158` call `api.getOhlcv(symbol, timeframe, ...)` without the market-type argument for older pages, live tail, and initial load.
- `frontend/src/api/client.ts:619-624` defaults `getOhlcv(..., marketType = 'spot')` and only sends `market_type` when the value is not `spot`.
- `aletheia/server/routes/data.py:790-825` defaults backend `market_type` to `"spot"` and builds `InstrumentId.from_parts(effective_exchange, sym, market_type)`, so an omitted market type really selects the spot instrument path.
- Existing ledger entries cover symbol display formatting and Universe board market-type assumptions, but no prior entry covers the candlestick data request itself defaulting to spot.

Expected direction: add `marketType` to `PriceChart` props and include it in all three `api.getOhlcv()` calls and the effect dependency list. In `ChartPanel`, resolve the selected symbol's market type using the same `mtFor(symbol)` function already used for display. Add a frontend/API-client regression that a perp/futures market selection calls `/market/ohlcv?...&market_type=<perp value>` rather than relying on the spot default.

### 116. Trades & Positions order detail opens the default chart instead of the selected order's chart

Impact: a filled order's detail drawer offers "View on Chart", but the action navigates to the generic Markets chart route with no symbol, market type, order id, or marker context. The chart page initializes to `BTC/USDT`, so viewing an ETH/SOL/futures order can land the user on the wrong instrument and wrong market. This is a UI truthfulness bug: the button promises order-specific context but opens a default chart.

Evidence:

- `frontend/src/pages/TradesPositions.tsx:330` renders `View on Chart` for filled orders and handles it with `navigate('/charts')`.
- The order detail component has the current `order.symbol`, `order.market_type`, `order.id`, and fill timestamps available, but none of those values are passed to the chart route.
- `frontend/src/pages/Charts.tsx:345` initializes the chart symbol with `useState('BTC/USDT')`.
- `Charts.tsx:427`, `Charts.tsx:451`, and `Charts.tsx:460` update the symbol only from local chart UI callbacks. Repository search found no `useSearchParams`, `searchParams.get('symbol')`, or equivalent route-state reader in `Charts.tsx`.
- Finding #115 separately covers missing market-type forwarding inside the chart. This finding covers the cross-page navigation contract from an order detail into the chart.

Expected direction: make the chart route accept order context, for example `/charts?symbol=...&market_type=...&order_id=...`, and have `Charts.tsx` initialize/select that instrument and optionally highlight the order marker. Add a frontend regression where a non-BTC filled order's "View on Chart" lands on that order's symbol and market type, not the default BTC spot chart.

### 117. Position reconciliation hides stale or unavailable exchange sync when there are no drift rows

Impact: the reconciliation backend intentionally returns a `sync_status` so callers know whether the exchange cache is current, stale, unavailable, or unconfigured. The Trades & Positions page drops the entire reconciliation card whenever there are no `external` or `orphaned` rows, even if `sync_status` says the exchange view is stale or unavailable. That can make "no drift shown" look like a healthy reconciliation result when the backend is actually saying it cannot prove that state.

Evidence:

- `aletheia/portfolio/position_reconciler.py:135` documents the contract: when cached exchange data is unavailable, `sync_status` explains why and the code must not infer orphaned positions from missing cache.
- `position_reconciler.py:190-191` explicitly says locals are reported as managed and the caller sees `sync_status` to understand the degraded state.
- `position_reconciler.py:115-125` includes `sync_status` in `ReconcileResult.to_dict()`, so the API payload carries this state for UI use.
- `frontend/src/pages/TradesPositions.tsx:159-162` passes `reconciliation?.sync_status` into `ReconciliationCard`.
- `TradesPositions.tsx:235` immediately returns `null` when `external.length === 0 && orphaned.length === 0`, ignoring `syncStatus`.
- `TradesPositions.tsx:249` only displays `(exchange view is stale)` inside the external-position paragraph, so stale/error/unconfigured states with zero drift rows are invisible.
- `aletheia/server/routes/data.py` exposes `/positions/reconciliation` as the page source; there is no alternate UI surface on this page for the degraded sync status.

Expected direction: show a reconciliation status banner/card whenever `sync_status` is not a healthy/current state, even when drift row counts are zero. Treat stale, error, and unconfigured as "cannot prove no drift" states rather than silently hiding the card. Add a frontend test for a payload with `sync_status='stale'` or `error` and empty `external`/`orphaned` arrays.

### 118. Overview and Risk Controls can show Scheduler active while the real scheduler is stopped

Impact: the backend now reports actual in-process scheduler state (`scheduler_running`, `scheduler_state`, `scheduler_job_count`) specifically so the UI does not show "active" when the scheduler failed to start, is disabled, or is stopped. The Overview and Risk & Controls pages ignore those fields and derive the scheduler light from only `runtime.paused` and gateway reachability. That can show a green/active scheduler even when no agent jobs are actually scheduled or running.

Evidence:

- `aletheia/agent_runtime/scheduler.py:621-641` defines `status_snapshot()` and documents that it reports the actual APScheduler object state so the dashboard does not show "active" when init failed or the scheduler is paused.
- `aletheia/server/routes/health.py:199-247` includes `scheduler_running`, `scheduler_state`, and `scheduler_job_count` from `status_snapshot()` in `/api/status`.
- `tests/test_status_routes.py:104-120` proves `/api/status` returns `scheduler_running=False` and `scheduler_state='not_started'` when the actual scheduler snapshot says so.
- `frontend/src/types.ts:367-370` and `frontend/src/api/client.ts:456` already model these scheduler fields.
- `frontend/src/App.tsx:211-282` uses the actual scheduler fields for the topbar popover, showing the intended frontend pattern.
- `frontend/src/pages/Overview.tsx:171` renders `Scheduler` as yellow/paused only when `paused` is true, otherwise green/active. It never checks `s.scheduler_running`, `s.scheduler_state`, or `s.scheduler_enabled`.
- `frontend/src/pages/RiskControls.tsx:180-187` similarly renders the Scheduler lever as `PAUSED`, `ACTIVE`, or `OFFLINE` based on `paused` and `gatewayRunning`, not actual scheduler state.
- `frontend/src/lib/systemState.ts` has the same limitation at the master-state level: the priority ladder treats `paused` as scheduler state but accepts no actual scheduler-running input.

Expected direction: centralize scheduler display derivation around `/api/status.scheduler_running` and `scheduler_state`, with fallback only for older payloads. Overview and Risk Controls should show stopped/not_started/disabled as degraded or offline, not active. Add a frontend regression where `/api/status` returns `gateway_running=true`, `paused=false`, `scheduler_running=false`, `scheduler_state='not_started'`, and both pages avoid green `ACTIVE`.

### 119. Go-Live scorecard model-health criterion reads the wrong snapshot shape

Impact: the Go-Live scorecard's model-health criterion does not reflect real model availability. The fallback model-health API returns a list of per-model health rows, but the scorecard reads it as a dict with a `providers` array. That exception is swallowed, so the criterion always degrades to "cannot verify" with a 50 score even after a model records successful health. This weakens a live-readiness signal and can make the scorecard neither fail nor pass based on actual LLM availability.

Evidence:

- `aletheia/model_provider/fallback.py:146-162` implements `get_model_health_snapshot()` as `list[dict]`, returning rows with keys such as `provider`, `model`, `last_success_at`, `last_error`, `cooling_down`, and `cooldown_remaining_sec`.
- `aletheia/server/routes/health.py:44-49` and `frontend/src/pages/Overview.tsx:27-30` treat model health as a list, and the Overview page finds the default provider with `(modelHealth as any[]).find(...)`.
- `aletheia/analytics/go_live_scorecard.py:288-295` calls the same `get_model_health_snapshot()` but then executes `snap.get("providers", [])`, which is a dict-only access on a list.
- The broad `except Exception` in `go_live_scorecard.py:295-296` turns that shape mismatch into `_criterion(... score=50, detail="cannot verify")`.
- Temporary reproduction with `uv run python -` showed:
  - empty snapshot type is `list`, criterion returns `{'key': 'model_health', 'score': 50, 'detail': 'cannot verify', 'status': 'warning'}`;
  - after `fallback._record_model_success('openai_compatible', 'test-model')`, snapshot contains a successful row, but the criterion still returns the same 50-point `cannot verify` result.
- Existing scorecard tests in `tests/specs/spec_analytics_contracts.py` cover synthetic close exclusion but do not cover the model-health criterion shape. Live-unlock tests generally monkeypatch `compute_scorecard()` wholesale, so they cannot catch this criterion-level bug.

Expected direction: either make `get_model_health_snapshot()` expose a stable readiness summary consumed by both status and scorecard, or change `_check_model_health()` to evaluate the list rows directly, for example any row with `last_success_at` and not `cooling_down` and no recent failure. Add a unit test that a populated successful snapshot returns a passing model-health criterion and an all-cooling/down snapshot returns failing, not the neutral `cannot verify` fallback.

**Correction (verify pass 2026-07-06 — line drift):** confirmed present, but the cited `go_live_scorecard.py:288-295` is `_check_data_coverage`. The real defective code is `_check_model_health` at `go_live_scorecard.py:304-312` — `snap.get("providers", [])` on a list at `:308`, `except`→`score=50` at `:311-312`. (`get_model_health_snapshot()` returns `list[dict]`, `fallback.py:146-159`.)

### 120. Overview AgentFeed renders real agent failures as yellow non-failure statuses

Impact: the Overview Agent Activity feed is the first screen the owner sees for "what the AI is doing." Backend health treats `failed`, `completed_with_errors`, and `timed_out` agent runs as failures, and the orchestrator uses `completed_with_errors` for exceptions. AgentFeed only gives red/error styling to the literal status `error`, so common real failure statuses are rendered as yellow badges and non-red icons. That underplays agent failures on the main cockpit even though the system health layer knows they are failures.

Evidence:

- `aletheia/agent_runtime/orchestrator.py:26-45` catches agent exceptions and writes the run status as `completed_with_errors`, not `error`.
- `aletheia/agent_runtime/scheduler.py:304-314` marks timed-out scheduler runs as `timed_out`; `scheduler.py:1008-1010` records `failed` for position-reconcile job exceptions.
- `aletheia/agent_runtime/run_logger.py:62-138` can mark stuck `running` rows as `stale` with an error message.
- `aletheia/agent_runtime/health.py:18` defines `_FAILED_STATUSES = {"failed", "error", "completed_with_errors", "timed_out"}`, so backend health already classifies several non-`error` statuses as failures.
- `frontend/src/components/AgentFeed.tsx:143-147` renders run headlines directly from `r.status`, including `completed_with_errors`, `failed`, and `timed_out`.
- `AgentFeed.tsx:199` colors the feed icon red only when `it.status === 'error'`; all other statuses use the accent color.
- `AgentFeed.tsx:212-214` maps badges to green only for `completed`/`approved`, red only for `error`/`rejected`, and yellow for everything else. Therefore `completed_with_errors`, `failed`, `timed_out`, and `stale` are yellow rather than red.
- Existing Finding #70 covers raw traceback/error persistence. This finding is distinct: it covers the main Overview feed's severity mapping after the status has already been persisted.

Expected direction: share a status-to-severity helper for agent runs and classify at least `failed`, `error`, `completed_with_errors`, `timed_out`, and likely `stale` as red/problem states on Overview and Agent Activity. Add a frontend regression with mocked agent runs for those statuses and assert the feed does not render them as yellow neutral states.

### 121. SetupBanner still assumes API-key models and Bitget demo credentials are the only valid setup

Impact: the first-screen setup banner can tell a correctly configured user to "add a model endpoint and Bitget demo credentials" even when they are using supported non-API-key model auth or a non-Bitget primary exchange. This is not just cosmetic: it nudges the owner toward the wrong setup path and conflicts with the current multi-provider/multi-exchange settings surface.

Evidence:

- `frontend/src/components/SetupBanner.tsx:12` queries only `api.getBitgetCredStatus()`.
- `SetupBanner.tsx:16-18` defines setup completion as at least one model provider with `p.api_key.configured` and Bitget demo `api_key.configured`.
- `SetupBanner.tsx:24` hardcodes the instruction "add a model endpoint and Bitget demo credentials to start."
- `frontend/src/api/client.ts:342-359` exposes current credential status APIs for Bitget, Binance, and OKX.
- `frontend/src/pages/Settings.tsx:1587-1635` queries and displays Bitget demo/live, Binance testnet/live, and OKX demo/live credential profiles.
- `aletheia/server/routes/bitget.py:135-141` exposes Binance credential status; `bitget.py:270` exposes OKX credential status.
- `aletheia/server/routes/settings.py:176-217` supports `/settings/primary-exchange`, and docs list `/settings/primary-exchange`, `/binance/credentials-status`, and `/okx/credentials-status` in `docs/design/api_reference.md:180` and `219-230`.
- `frontend/src/pages/Settings.tsx:3572-3581` already treats OAuth-backed native providers as configured when `config.oauth.configured` is true, but `SetupBanner` ignores `p.oauth.configured`.
- `aletheia/server/routes/model.py:33-55` returns both `"api_key": _key_status(...)` and `"oauth": _key_status(...)` for each provider, plus `auth_type`.
- Tests in `tests/test_model_provider.py:783-955` cover OAuth/subscription model provider behavior, and `tests/test_config_routes.py:317-331` covers Codex CLI model setting/health. SetupBanner's "anyModelKey" predicate ignores those setup modes.

Expected direction: derive setup completeness from the same provider/exchange state the Settings page uses. A model should count as usable if its configured auth mode is usable (API key, OAuth token, or CLI/subscription provider health), and exchange setup should respect the selected primary exchange/profile rather than requiring Bitget demo. Add a frontend/component regression for `openai_native` OAuth configured + OKX demo credentials configured where the banner does not ask for Bitget/API-key setup.

### 122. CausalChain renders fills from order fields instead of the immutable fills table

Impact: the Trades & Positions drawer claims to show the immutable proposal -> risk -> order -> fill -> position chain, but the Fill node does not read the `fills` table at all. It uses `orders.filled_at` and `orders.price` as a stand-in for fill time and fill price. For market orders, partial fills, slippage, or exchange-confirmed fills where `fill_price` differs from the requested order price, the UI can show an incorrect fill or hide fill quantity/fees entirely. This is a trust/audit display bug: the user sees a "Fill" node that is not backed by the fill record.

Evidence:

- `docs/design/execution_contract.md:18-25` defines the causal chain as `orders.proposal_id + orders.risk_decision_id -> fills.order_id -> positions`.
- `aletheia/storage/models.py:194-206` defines `Fill(order_id, fill_price, fill_quantity, fee, fee_currency, fee_source, filled_at)`.
- `aletheia/storage/repositories.py:456-492` exposes `list_fills()` and strategy fill queries, proving fills are first-class records, not order decorations.
- `tests/test_demo_trading.py:205-207` asserts demo fills store the confirmed `fill_price` (`60123.5`) and explicitly comments that it is not the `60000` request price.
- `frontend/src/components/CausalChain.tsx:183-193` renders the order-anchor Fill node from `anchor.order.filled_at`, `anchor.order.price`, and `anchor.order.exchange_order_id`; it never fetches a fill row by `order_id`.
- `CausalChain.tsx:195-205` repeats the same pattern for position anchors through `resolvedOrder.filled_at` and `resolvedOrder.price`.
- Repository search found no frontend `getFills` helper and no FastAPI `GET /api/fills` or `GET /api/orders/{id}/fills` route. The only fills route references are exchange adapter tests and repository-level tests, so the UI currently has no honest fill data source.
- Existing Finding #65 covers CSV export omitting fills. This finding is distinct: it covers the interactive causal-chain inspector showing a fake/incomplete Fill node.

Expected direction: add a read-only fills endpoint scoped by `order_id` or include fill rows in order detail payloads, then render actual `fill_price`, `fill_quantity`, `fee`, `fee_currency`, `fee_source`, and `filled_at`. If fills are unavailable, the node should say no fill record found rather than deriving fill facts from the order row. Add a regression where order `price` differs from `fills.fill_price` and the CausalChain displays the fill price.

### 123. CausalChain order anchors always show the resulting Position as missing even when positions carry `order_id`

Impact: opening an order in Trades & Positions always ends the causal chain with `Position: not recorded · orders store no resulting-position link`. That is no longer true: current positions have `order_id`, `proposal_id`, and `opportunity_id`, and `/api/positions` already returns them to the same page. A filled order that created a position can therefore be shown as having no resulting position, making the audit chain look broken when the backend data exists.

Evidence:

- `frontend/src/components/CausalChain.tsx:14-15` correctly says a `Position` stores `proposal_id` and `order_id`.
- `aletheia/storage/models.py:209-230` defines `Position` with `proposal_id`, `order_id`, and `opportunity_id`.
- `frontend/src/types.ts:72-73` models `Position.proposal_id` and `Position.order_id`.
- `frontend/src/pages/TradesPositions.tsx:94` already queries `api.getPositions()` for the page.
- `frontend/src/components/CausalChain.tsx:53-56` fetches proposals and orders, but it never fetches or receives positions when the anchor is an order.
- `CausalChain.tsx:211-220` renders a present Position only when `anchor.kind === 'position'`; for every order anchor it unconditionally renders `state="missing"` with note `orders store no resulting-position link`.
- `tests/specs/spec_pipeline_execution_chain.py:103-104` checks that a position row carries the originating `order_id`.
- `docs/design/webui_backend_handoff.md:11` says the position causal backlink work is done, while older docs such as `webui_page_map.md` still contain stale language about the missing link. The component behavior follows the stale assumption, not the current schema.

Expected direction: have `CausalChain` resolve positions by `order_id` for order anchors, either by accepting the page's positions list as a prop or querying a scoped read endpoint. Render a missing Position only when no position row with that `order_id` exists. Add a regression for a filled order plus position row sharing `order_id` where the order drawer shows the Position node as present.

### 124. Equity-curve `limit` is applied before the date window and can hide recent PnL points

Impact: the Overview/Learning cumulative PnL chart can show an empty or stale recent window even when trades closed inside that window. In `full_cumulative` mode, `/analytics/equity-curve` orders all closed positions oldest-first, applies `LIMIT`, then filters displayed points by `days`. If the limited prefix is older than the requested date window, recent trades are never read from SQLite. With the default `limit=5000`, this appears once the system has enough historical closes; with smaller explicit limits it is immediately reproducible.

Evidence:

- `aletheia/server/routes/analytics.py:37-48` documents `days` as "only include positions closed within the last N days" and `limit` as max data points.
- `analytics.py:71-81` applies the SQL `ORDER BY closed_at ASC LIMIT ?` before any full-cumulative date cutoff is applied.
- `analytics.py:84-103` computes the `cutoff` for `days` only after fetching the limited rows, then drops rows older than the cutoff.
- `frontend/src/api/client.ts:818-822` exposes `/analytics/equity-curve` to the frontend, and `frontend/src/charts/PnLChart.tsx:16-31` renders whatever `points` the endpoint returns.
- `frontend/src/components/PnLZone.tsx:314-327` uses `PnLChart days={rangeDays}` for the Overview 7D/30D/90D/All chart; `frontend/src/pages/Learning.tsx:468` also uses `PnLChart`.
- Existing tests in `tests/test_pnl_pipeline.py:217-236` cover `days=7` in normal conditions, and `test_respects_limit` covers `limit=5` without `days`; there is no combined `days + limit` regression where old rows can consume the limit.
- Temporary reproduction with `uv run python -` used a temporary `DATA_DIR`, inserted one close 30 days ago and one close now, then called `get_equity_curve(days=7, limit=1)`. Result was `{'points': [], 'current_unrealized': 0.0, 'total_realized': 5.0}` even though the recent close should have produced a displayed point and the all-history total should have been 8.0.

Expected direction: in full-cumulative mode, compute cumulative history without letting the display `limit` exclude recent rows before the cutoff. Options include querying all rows needed for accumulation then applying `limit` after the cutoff, or splitting accumulation (`SUM` before cutoff) from a limited in-window query. Add a regression with old rows exceeding `limit` plus an in-window close and assert the in-window point is returned with cumulative PnL including pre-window history.

### 125. Command Palette is missing the implemented Universe Board navigation entry

Impact: the owner-approved sidebar IA includes Universe Board as a first-class Research surface, but the global `Ctrl+K` launcher cannot jump to it. This is a product navigation coverage bug, not a missing backend feature: the route and page exist, and the sidebar test already treats the entry as mandatory. Users relying on the keyboard launcher will not discover or reach a key universe-selection surface from the global command path.

Evidence:

- `frontend/src/App.tsx:41-75` defines the data-driven `NAV` list and includes `{ to: '/universe', label: 'Universe Board' }` under the Research group.
- `frontend/src/App.tsx:515` registers the implemented `/universe` route.
- `frontend/src/pages/Universe.tsx:148` renders the page title `Universe Board`.
- `tests/test_frontend_navigation_ia.py:34` asserts the sidebar Research group contains the Universe Board entry.
- `frontend/src/components/CommandPalette.tsx:40-52` hardcodes global navigation commands for Command Center, Overview, Markets, News Intel, Serenity Lens, Prediction Markets, Approval Desk, Agent Activity, Trades & Positions, Learning, Risk, and Settings, but has no `/universe` command.
- `tests/test_frontend_navigation_ia.py:42-59` scans `.tsx` files for stale strategy/old-serenity links but does not assert CommandPalette coverage equals the implemented `NAV` routes, so this drift is not tested.
- `docs/_ai/SIDEBAR_IA_DECISION_RECORD_2026-07-03.md:76` and `docs/_ai/DISCUSSION_DEVELOPMENT_LESSONS_2026-07-03.md:163` both list Research as including Universe Board, Markets, Prediction Markets, Serenity Lens, and News Intel.

Expected direction: derive CommandPalette navigation commands from the same `NAV` source used by the sidebar, or add a parity regression that every non-legacy sidebar route has a CommandPalette entry. Add the missing `/universe` command at minimum.

### 126. Command Palette Enter handling can prevent keyboard execution of filtered commands

Impact: the global command launcher is meant to be a quick launcher, but its root `onKeyDown` intercepts every Enter press when the input has any non-empty text and navigates to `/command-center?draft=...`. With cmdk, the normal keyboard workflow is type to filter, then press Enter to select the highlighted command. Here a user typing `risk`, `settings`, or `market` can be sent to Command Center with a draft instead of executing the highlighted navigation/system/agent command. Mouse selection still works, so this can slip past manual testing.

Evidence:

- `frontend/src/components/CommandPalette.tsx:72-82` attaches `onKeyDown` to the `<Command>` root. On Enter, it reads `(e.target as HTMLInputElement)?.value?.trim()` and, if non-empty, calls `e.preventDefault()`, closes the palette, and navigates to `/command-center?draft=...`.
- `CommandPalette.tsx:96-100` relies on `Command.Item onSelect={() => exec(cmd.action)}` for command execution.
- Because the Enter handler runs on the parent command surface and prevents default whenever the input is non-empty, typed filtering and keyboard selection are conflated with the "send natural language draft" path.
- `CommandPalette.tsx:86` says the input can be used to "Jump somewhere, or type a natural-language command (20+ chars) for AI...", but the code has no length threshold in the Enter handler. Any one-character filter such as `r` is treated as a draft if Enter is pressed.
- `docs/design/operator_layer.md:250` says not to treat `Ctrl+K` as the System Operator itself; `operator_layer.md:994` says the command palette may open or prefill the operator surface but should remain a secondary shortcut.
- `docs/design/specs/2026-06-10-pi-operator-agent-dual-process-design.md:1020-1027` similarly says `CommandPalette` is a popup-only surface and should stay a shortcut/page launcher.
- Existing `tests/test_frontend_navigation_ia.py` is static route/link scanning only; there is no interaction test for `Ctrl+K`, filtered command selection, or the NL-draft threshold. `docs/issues/FRONTEND_ISSUES.md:344` already records an old "NL minimum length no hint" issue, but current code goes further by not enforcing the hinted threshold at all and by intercepting keyboard selection.

Expected direction: let cmdk handle Enter for visible/selected command items, and only route to Command Center when the query is intentionally submitted as natural language, for example when no command item matches and the query meets the documented threshold. Add a Playwright or component-level keyboard regression: open palette, type `risk`, press Enter, assert navigation to `/risk` rather than `/command-center?draft=risk`; type a long unmatched natural-language command and assert it pre-fills Command Center.

### 127. Prediction probability chart cannot reliably render Polymarket fallback history timestamps

Impact: the Prediction Markets detail drawer may show `No probability history yet` or fail to render a usable probability chart even when the backend successfully fetched external Polymarket history. Local snapshots use ISO strings and render normally, but the external fallback path returns Polymarket `t` values as raw strings. The chart parses every `ts` with `new Date(p.ts)`, and JavaScript does not parse numeric epoch strings such as `"1700000000"` as valid dates. This breaks the exact fallback intended to provide history before local snapshots have accumulated.

Evidence:

- `aletheia/server/routes/prediction.py:178-193` falls back to `fetch_market_history(ext_id, interval=interval)` when there are no local `prediction_probability_snapshots`, then returns `{"source": "polymarket_api", "points": history}` directly.
- `aletheia/data/polymarket_provider.py:146-149` documents the fallback as returning `{ts, yes_probability}` history from Polymarket.
- `polymarket_provider.py:166-168` maps each external point to `{"ts": str(pt.get("t", "")), "yes_probability": float(pt.get("p", 0))}`. The raw `t` field from the CLOB price-history API is an epoch timestamp value, so this can be a numeric string rather than an ISO timestamp.
- `frontend/src/types.ts:227-232` only types `ProbabilitySnapshot.ts` as `string`; it does not distinguish ISO strings from epoch strings.
- `frontend/src/charts/ProbabilityChart.tsx:35-40` filters only for truthy `p.ts` and non-null `yes_probability`, then computes `time: (new Date(p.ts).getTime() / 1000) as Time`; it does not use `normalizeIso()`/`isoToEpochSeconds()` and does not filter out `NaN`.
- A direct verification command showed the browser-equivalent parsing problem: `node -e "console.log(Number.isNaN(new Date('1700000000').getTime()))"` prints `true`, while an ISO timestamp with `+00:00` parses.
- Local observer snapshots are less exposed because `aletheia/agents/prediction_observer.py:126-129` writes `ts: _utcnow()`, and `_utcnow()` in `storage/models.py:12-13` returns a timezone-aware ISO string. The bug is therefore specifically the external-history fallback path, which is the path used before local history exists.
- Existing subsystem observer specs check that prediction tables and writes exist, but they do not exercise the `/prediction/markets/{market_id}/history` fallback payload in the frontend chart.

Expected direction: normalize history timestamps at the backend boundary or in `ProbabilityChart`. If `ts` is numeric seconds or milliseconds, convert it to chart epoch seconds directly; if it is ISO, parse with the shared UTC-safe helper. Filter invalid points before `series.setData()`. Add a regression for a fallback point like `{ts: "1700000000", yes_probability: 0.62}` and assert the chart data conversion produces a finite `Time`.

### 128. Trades & Positions analytics still presents deleted RuleSet strategy source as an active trade attribution dimension

Impact: the active Trades & Positions analytics tab tells the user closed trades are split "By Strategy Source" into `RuleSet` and `Opportunity`. The current product architecture says the rules engine and RuleSet trading path were deleted, and `strategy_id` is only a nullable legacy attribution tag. Showing RuleSet as a first-class current source can make the owner think some trades were driven by the removed rules engine, or that RuleSet-vs-opportunity performance is still an actionable current control. This is semantic honesty drift in an active dashboard panel.

Evidence:

- `docs/design/execution_contract.md:152` says the strategy `entry_rule`/`exit_rule`/`RuleSet` path was eliminated by deleting the rules engine, and trading direction now comes from the System Operator's opportunity decision; `strategy_id` is only a nullable historical attribution tag.
- `docs/design/DOC_ATLAS_BY_DOMAIN.md:66-71` repeats that legacy `strategy_records` are historical, the RuleSet/backtest paths are deleted, and they should not be restored without a new owner decision.
- `frontend/src/pages/TradesPositions.tsx:203-207` mounts `TradeAnalyticsPanel` in the current Analytics tab.
- `frontend/src/charts/TradeAnalyticsPanel.tsx:78-90` renders a section titled `By Strategy Source` and maps backend `source_type="ruleset"` to the user-visible label `RuleSet`.
- `frontend/src/api/client.ts:857` types `TradeStatsSource.source_type` as the closed union `'ruleset' | 'opportunity'`, baking the stale dimension into the frontend API contract.
- `aletheia/analytics/trade_stats.py:112-120` computes `source_type` by left-joining `positions` to `strategy_records` and classifying a position as `ruleset` when `json_extract(s.parameters, '$.ruleset') IS NOT NULL`; otherwise it is `opportunity`.
- `trade_stats.py:154-165` always returns rows for both `"ruleset"` and `"opportunity"`, even when the current source is no longer an active system capability.
- The existing Finding #105 covers the execution contract still documenting deleted RuleSet auto-exit. This finding is distinct: it covers the current dashboard analytics API/UI still presenting RuleSet as an active attribution dimension.

Expected direction: rename or remove the stale source dimension. If old historical RuleSet records must remain visible, label them explicitly as `Legacy RuleSet` and keep them separated from current-source analytics; otherwise aggregate current trades by honest dimensions such as opportunity source/signal source/scanner family/execution mode. Add a regression that the active trade-stats API/UI does not present deleted RuleSet as a current strategy source unless a legacy row is clearly labeled as historical.

### 129. Knowledge Markdown filenames can collide within one second and overwrite earlier learning evidence

Impact: learning artifacts keep a DB row plus a Markdown evidence file. The file writer names every knowledge file as `<second timestamp>-<sanitized title>.md`. When the system writes multiple same-title artifacts within the same second, the later write overwrites the earlier Markdown file while both DB rows remain. This can corrupt the learning corpus: two different `knowledge_items` can point to the same file path, and the first item's body/frontmatter is silently replaced by the second item's evidence. Case studies are especially exposed because batch generation can create several rows with the same title such as `Case: BTC/USDT long (stop_loss)`.

Evidence:

- `aletheia/files/markdown.py:65-70` builds filenames with `datetime.utcnow().strftime("%Y%m%d-%H%M%S")` plus sanitized title only; there is no unique suffix such as `KnowledgeItem.id`, `position_id`, or nanoseconds.
- `markdown.py:78-80` writes to `path.with_suffix(".tmp")` and then `os.replace(str(tmp), str(path))`, so a same-path second write atomically replaces the earlier file.
- `aletheia/analytics/trade_case_builder.py:94-106` creates case study titles as `Case: {symbol} {direction} ({close_reason})`. Multiple closed positions with the same symbol/direction/reason in one batch can therefore share the exact title.
- `trade_case_builder.py:100-106` passes distinct `metadata` including `position_id`, but `write_trade_case_study()` does not use that metadata to make the filename unique.
- `aletheia/knowledge/manager.py:228-240` stores the returned `file_path` in each `KnowledgeItem`; if two writes collide, both DB rows can persist with the same file path.
- `aletheia/knowledge/retrieval.py:118-169` and maintenance routines later re-read Markdown files by `file_path`, so an overwritten file changes what old knowledge rows mean during reindex/maintenance.
- Clean temporary reproduction with `uv run python -` monkeypatched the Markdown writer clock to the same second and created two `trade_case_study` rows with the same title but different bodies/position metadata. Output:
  - `same_path: True`
  - `contains_first: False`
  - `contains_second: True`
  - `db_rows: 2`
  - both DB rows had the same `knowledge/case_studies/20260706-010203-case-btcusdt-long-stop_loss.md` path.
- Existing tests cover FTS and case-study creation, but they do not simulate same-title same-second writes. `tests/test_trade_case_builder.py` mocks `create_trade_case_study`, so it cannot catch file-path collisions in the real writer.

Expected direction: make knowledge file paths collision-proof. For generated learning artifacts, include a stable unique id in the filename, for example `position_id` for trade case studies and a generated `KnowledgeItem.id` or UUID for general knowledge writes. Add a regression that two same-title case studies written under the same clock tick produce two different files and each DB row's `file_path` preserves its own content/frontmatter.

### 130. Applied lessons are never re-reviewed, so consumed learning constraints cannot be overturned by later evidence

Impact: the Learning contract says lessons should be overturnable as newer evidence arrives, and the consumed stable state is `applied`. In the implementation, `lesson_reviewer` re-judges only `status="accepted"` rows. But accepted lessons are immediately marked `applied` in the same normal reviewer run, and downstream consumers read `applied` lessons. Therefore the actual lessons that influence the System Operator prompt and risk lesson checks are never re-reviewed or demoted, even when later settled evidence contradicts them. A bad `block_symbol`/`block_scanner` lesson can remain active until expiry instead of being overturned by the feedback loop.

Evidence:

- `docs/design/learning_contract.md:63-73` defines `accepted` as short-lived and `applied` as the stable consumed state; it explicitly says consumers must read `status="applied"`, not `accepted`.
- `learning_contract.md:101-103` lists `trading_context.py` and `risk/engine.py::_check_lesson_constraints` as consumers of `status="applied"` lessons.
- `aletheia/learning/lessons.py:102-123` implements `mark_applied()` by setting `applied_policy_version` and `status='applied'`.
- `aletheia/agents/lesson_reviewer.py:18-19` says later evidence can overturn an earlier acceptance.
- `lesson_reviewer.py:167-186` performs the overturn pass over `store.list_all(status="accepted", limit=100)` only.
- `lesson_reviewer.py:224-251` then applies newly accepted lessons by calling `store.mark_applied(lid, pv.policy_version_id)`, moving them out of the only status the overturn pass reads.
- `agent_shell/trading_context.py` and `risk/engine.py` were already reviewed earlier in this sweep for lesson consumption; both active consumption paths use applied lessons, not accepted ones.
- Temporary reproduction with `uv run python -` created a lesson, called `accept()` and `mark_applied()`, patched `_fetch_evidence()` to strong contradictory evidence and `_judge()` to return `("reject", "contradicted")`, then ran `run_lesson_reviewer()`. Output was `{'before': 'applied', 'after': 'applied', 'outputs': ['summary:accepted=0,rejected=0,deferred=0,demoted=0,remaining=0'], 'judge_calls': 0}`.
- `tests/specs/spec_learning_pipeline.py:414-436` only checks that the reviewer source mentions `status="accepted"` for overturnability. That test encodes the dead intermediate status rather than proving consumed `applied` lessons can be overturned.

Expected direction: re-review the lessons that actually influence the system: `status="applied"` and not expired, possibly with a separate policy-version supersession event when a lesson is demoted. If preserving the accepted-overturn path is still useful, include both accepted and applied. Add a regression where an applied lesson with contradictory settled evidence is demoted/rejected or archived and no longer appears in trading context/risk lesson checks.

### 131. TradingMemo time decay is visible in the prompt but not applied to ranker sorting

- **Domain**: learning feedback loop / paper opportunity ranking.
- **Impact**: old tactical memory can keep moving candidate order with full strength even after the system tells the operator that the memo has almost decayed away. A stale `strong`/`contra` regime rule can still add `+12` or subtract `-20` in `_memo_scoring()`.
- **Document expectation**:
  - `docs/design/learning_contract.md:7-10` says learning output is reused by future ranking and decision context.
  - `learning_contract.md:228-235` says later evidence/time decay can reduce older rules' influence.
  - `docs/design/knowledge_contract.md:349-351` says `trading_memo` regime guidance is downweighted by `memo_half_life_weight()`.
- **Code evidence**:
  - `aletheia/analytics/memo_effectiveness.py:38-56` implements the 7-day half-life.
  - `aletheia/analytics/insight_synthesizer.py:511-533` applies that weight only when rendering prompt text.
  - `aletheia/agents/paper_trader_ranker.py:432-471` reads the same memo for actual candidate scoring but never calls `memo_half_life_weight()` or uses `generated_at`.
  - `paper_trader_ranker.py:443-450` applies fixed adjustments: `strong => +12`, `weak => -12`, `contra => -20`.
- **Reproduction**:
  - Temporary `uv run python -` monkeypatch supplied the same memo rule with `generated_at=now` and `generated_at=30 days ago`.
  - Output:
    - fresh memo: `weight=1.0`, `memo_score=12.0`
    - 30-day memo: `weight=0.0513`, `memo_score=12.0`
- **Expected fix direction**: multiply ranker memo adjustments by `memo_half_life_weight(memo.get("generated_at"))`, or explicitly document that half-life only affects prompt display and not ranking. Add a focused test where a stale memo has materially smaller `_memo_scoring()` impact than a fresh memo.

## Backend Control / Lifecycle / Execution Findings (verified fan-out sweep, 2026-07-06)

> Added by a read-only 9-lane reviewer fan-out over under-covered backend areas (control routes, gateway lifecycle/edge, self-update, server app, position exit, exchange sync/brokers, low-level clients), each finding adversarially re-verified by an independent skeptic. Severities reflect the verifier's correction — several were downgraded from the reviewer's first rating. Two candidate findings were investigated and refuted (see "Investigated and refuted"). Anchors for #133 and #137 were additionally re-read by hand before recording. Two lanes came back clean: `risk-bitget-routes` (no route mutates hard risk limits; boundaries are GET-only; kill-switch surfaces read the same field so cannot diverge; broker status returns only `{configured, last4}`; no order path bypasses the gateway) and `gateway-lifecycle` (recovery FSM is honestly documented as unwired, guarded by the `PR-C01` self-correcting tripwire; SQLite write discipline correct even in the dead code).

### 132. Gateway restart audit hardcodes `actor="human"` and derives audit `source` from the caller-supplied `reason`

- **Domain**: backend control routes / audit integrity.
- **Impact**: both real callers of `POST /api/gateway/restart` are automated — the git post-merge/checkout hook and the dev file-watcher — yet the route records every restart as `actor="human"`, so automated restarts are logged as human actions. `source` is taken verbatim from the request body's `reason`, so it is caller-controllable (theoretical only: the endpoint is localhost-internal). Weakens the audit trail for gateway lifecycle state changes. Sibling of #83 (runtime-continuity caller-supplied actor), different route.
- **Document expectation**: AGENTS.md trust invariant — "Audit logs must describe the real actor, source, entity type, entity id, and state change."
- **Code evidence**: `aletheia/server/routes/gateway.py:79-88` (`reason=body.reason`; `source="dev_watcher" if reason=="file_change" else "dashboard"`; `write_audit({"actor":"human", "source":source, ...})`). Automated callers: `aletheia/gateway/git_hooks.py:117` (`reason="git_hook"`), `scripts/watch_backend.py:80` (`reason="file_change"`).
- **Reproduction/verification**: reviewer + skeptic read `gateway.py:61-101` and traced both callers; confirmed `reason` is body-supplied and both non-manual callers are automated processes.
- **Expected fix direction**: derive `actor` from the trigger (`git_hook`/`file_change` → `actor="system"`/`"dev_watcher"`; only manual/dashboard → `"human"`); don't let an unauthenticated body field alone set the recorded `source`. **Severity: low.**

### 133. Log-tail redaction filter misses the bare `secret` field and the `ACCESS-KEY`/`ACCESS-SIGN` exchange auth headers

- **Domain**: gateway log redaction / secret exposure (defense-in-depth).
- **Impact**: `redact_log_text` scrubs secrets before operational log tails are surfaced by `aletheia doctor` (a frontdoor-proxied, tunnel-reachable view whose content is also ingested into the System Operator's assessment/prompts). Its pattern set omits the bare `secret` field name — the exact CCXT credential attribute used to build every exchange client — and the `ACCESS-KEY`/`ACCESS-SIGN` auth headers. If a CCXT exception, config dump, or header dump lands in `gateway.log`/`errors.log`/`frontdoor-errors.log`, the API secret / HMAC signature passes through in plaintext to a tunnel-reachable view and potentially into agent prompts. (Verify correction: the `ACCESS-PASSPHRASE` *value* is already caught by the standalone `passphrase` token — only `ACCESS-KEY`/`ACCESS-SIGN` and bare `secret` leak; and no current code path is shown writing a bare-`secret` value into these logs, so this is a defense-in-depth gap, not an active leak — downgraded high→medium.)
- **Document expectation**: AGENTS.md — "API keys/secrets must NEVER be … written to logs … or passed into agent prompts"; `logs.py:8-9` docstring claims log tails are read "with redaction".
- **Code evidence**: `aletheia/gateway/logs.py:24-29` `_SECRET_PATTERNS` alternation (`:26`) is `api[_-]?key|api[_-]?secret|passphrase|token|authorization|x-api-key` — **no bare `secret`, no `access[_-]?(key|sign)`** (hand-confirmed). Reachable via `diagnostics/doctor.py:229-236` (`tail_file_redacted`) / `:305-306`. Leak-format sources exist: `exchanges/bitget/auth.py:48-49` (ACCESS-* headers); `exchanges/okx/client.py:116`, `binance/client.py:77`, `server/routes/bitget.py:234,353` (`"secret": <value>` ccxt config).
- **Reproduction/verification**: reviewer ran `redact_log_text` via `uv run` on exchange-format inputs — `secret=...`, `{"secret":"..."}`, `ACCESS-KEY:`, `ACCESS-SIGN:` pass through unredacted while `ACCESS-PASSPHRASE: <val>` is redacted; anchor `logs.py:24-29` re-read by hand.
- **Expected fix direction**: extend `_SECRET_PATTERNS` to cover the bare `secret` key/value and the `access[_-]?(key|sign|passphrase|token)` header family. Same defense-in-depth filter; distinct from #69 (proxy-URL logging). **Severity: medium.**

### 134. Frontdoor `/api/*` proxy does a synchronous route-file read + `json.loads` on every request

- **Domain**: gateway frontdoor / async hot path.
- **Impact**: `proxy_api` (the hottest path — every dashboard/SSE/API call) calls `_backend_url()`, which does a blocking `route_path.read_text()` + `json.loads()` on the uvicorn event loop with no TTL cache — the pattern the API-performance rule prohibits. (Verify correction: the route file is a tiny OS-page-cached JSON, so the per-call stall is microseconds, not the seconds seen in the `load_config` case; the link to past frontend disconnects is speculative — real disconnect causes were the market_observer thread leak and unguarded `aiter_raw`. Downgraded medium→low.)
- **Document expectation**: AGENTS.md API-performance rule — sync blocking I/O inside `async def` freezes the loop; hot-path reads need a TTL cache.
- **Code evidence**: `aletheia/gateway/frontdoor.py:287-303` `proxy_api` → `:288 _backend_url()` → `frontdoor_route.py:65-80 resolve_backend_base_url` → `:51-62 read_frontdoor_route` → `:54 route_path.read_text()` + `json.loads`. No cache wrapper. Production entry `app = create_app()` (`frontdoor.py:360`) takes the `use_route_file=True` branch. `_infra/*` handlers share the read plus `static_dir.exists()`/`index.html.exists()` stats.
- **Reproduction/verification**: reviewer + skeptic traced the chain; confirmed no TTL/lru cache and the production entrypoint branch.
- **Expected fix direction**: wrap the route lookup in a short (1–2 s) TTL / mtime-memoized cache in `resolve_backend_base_url`/`read_frontdoor_route`. **Severity: low.**

### 135. Self-update rollback reports success without checking `reset_hard()` result

- **Domain**: self-update rollback / UI honesty.
- **Impact**: when smoke fails, `applier.py:123` calls `git_ops.reset_hard(old)` but discards the return; it then unconditionally sets `cfg.update.current_commit=old`, writes an `update_rolled_back` audit (`after.commit=old`), and returns "Update rolled back … No restart needed." If `reset_hard` failed (index lock from a concurrent session, disk error → `{ok:False}`), on-disk HEAD is still the broken NEW commit while config/audit/API all claim a clean revert; a later restart boots the code the operator was told was reverted. Contradicts the module's own "a bad update can never brick the gateway" invariant.
- **Document expectation**: AGENTS.md UI-honesty rule + `applier.py:118-120` rollback invariant.
- **Code evidence**: `aletheia/update/applier.py:123` (return discarded), `:126-138` (set `current_commit=old` + audit + `rolled_back:True` regardless); `aletheia/update/git_ops.py:113-124` (`reset_hard` returns `{ok:False,reason:"reset_failed"}` on non-zero git, no raise).
- **Reproduction/verification**: reviewer + skeptic read both files; confirmed the compound smoke-fail + reset-fail path yields a false clean-revert report.
- **Expected fix direction**: check `reset_hard().ok`; on failure surface `reason="rollback_failed"`, keep/flag the true HEAD, and audit real state. **Severity: medium.**

### 136. `_safe_env` strips only `BITGET_` among exchange/API credentials for update subprocesses

- **Domain**: credential isolation / cross-broker parity.
- **Impact**: `_safe_env()` (in both `applier.py` and `smoke.py`) strips a 5-prefix blocklist (`BITGET_/OPENAI_/ANTHROPIC_/LLM_/TELEGRAM_BOT_TOKEN`) before spawning update/smoke/reinstall subprocesses, but not `OKX_`/`BINANCE_`/`COINALYZE_`/`FRED_`. (Verify correction: `OKX_`/`BINANCE_` keys are never read from `os.environ` by the app — like the already-stripped `BITGET_`, that half is parity-only/theoretical. But `COINALYZE_API_KEY` IS read from `os.environ` (`data/coinalyze.py:115`) and `FRED_API_KEY` IS read from `os.environ` (`data/macro.py:62`) — those are concrete environ-sourced secrets a hijacked build/test hook could read.) Real secrets otherwise live in keyring/enc file, so impact is low.
- **Document expectation**: cross-broker rule + secret-never-leaked invariant.
- **Code evidence**: `aletheia/update/applier.py:19-25` and `aletheia/update/smoke.py:30-36` (`_SECRET_PREFIXES`); `config/config_consistency.py:67-69` (documents BITGET_/OKX_/BINANCE_ env names); concrete environ readers `data/coinalyze.py:115`, `data/macro.py:62`.
- **Reproduction/verification**: skeptic confirmed the prefix list and that `COINALYZE_API_KEY`/`FRED_API_KEY` are read from `os.environ`.
- **Expected fix direction**: add `COINALYZE_`/`FRED_` (and, for documented parity, `OKX_`/`BINANCE_`) to the strip prefixes, or use an allowlist env for the subprocesses. **Severity: low.**

### 137. Tunnel auth guard omits `POST /api/settings/risk` and `/api/settings/exchange-mode` — the high-severity instance of #42

- **Domain**: server auth middleware / trust boundary. **Highest-priority item in this batch.**
- **Impact**: the tunnel `_tunnel_auth_guard` fails-closed on only 5 exact paths. `/api/risk/kill-switch` IS protected, but the SAME safety controls are mutable via the UNPROTECTED `POST /api/settings/risk`, which sets `kill_switch_enabled_live`/`kill_switch_enabled_demo` and every leverage/notional/exposure limit; `/api/settings/exchange-mode` (demo→live) is likewise unprotected. When `tunnel.enabled=True` (public exposure), an unauthenticated client can disable the kill switch and widen all risk limits — defeating the protection deliberately placed on `/api/risk/kill-switch`, and directly affecting demo. Live *order* placement still needs the protected `/api/settings/live-unlock`, which bounds the worst case. Same root cause as #42 (exact-match `_PROTECTED_PATHS` set), recorded separately because the fix target and risk are concrete and #42's enumerated paths did not include it. Toggles are live-consumed at `risk/engine.py:202-205`.
- **Document expectation**: AGENTS.md — `aletheia/risk/` is a change-controlled trust boundary; `app.py:445-452` docstring says protected paths must fail-closed to prevent "unauthenticated public access to trust-boundary endpoints."
- **Code evidence**: `aletheia/server/app.py:436-440` (`_PROTECTED_PATHS` = 5 paths), `:453` (exact-match `in _PROTECTED_PATHS`) — hand-confirmed; `aletheia/server/routes/settings.py:762` `@router.post("/risk")` (prefix `/api/settings`) mutates risk fields via `setattr` (`:776`) + `save_config` (`:778`) with **no auth dependency** (hand-confirmed); per reviewers it sets `kill_switch_enabled_*` (`:718-719`); consumed at `risk/engine.py:202-205`.
- **Reproduction/verification**: reviewer + skeptic read `app.py:436-475` and `settings.py:695-784`; I re-read `app.py:430-476` and `settings.py:755-784` by hand — protected set has 5 paths, `/api/settings/risk` handler has no token check.
- **Expected fix direction**: add `/api/settings/risk` and `/api/settings/exchange-mode` to `_PROTECTED_PATHS`, or switch the guard from an exact-path set to a prefix/predicate covering all risk/live-toggling mutations. Touches trust-boundary protection — add a focused regression test. **Severity: medium (top priority).**

### 138. `sync_all_exchanges()` runs synchronously pre-`yield` in the app lifespan, blocking the loop and delaying the port bind

- **Domain**: server startup lifespan / async blocking I/O.
- **Impact**: `sync_all_exchanges()` is called synchronously in the async lifespan BEFORE `yield` (before uvicorn binds the socket). It reaches `get_unified_state_sync()` → `uta/manager.py`, which — detecting the running loop — submits the async aggregation to a worker thread and blocks the event-loop thread on `Future.result(timeout=30)`. A slow/unreachable broker blocks the lifespan up to 30 s, delaying the port bind — the exact failure mode the LLM/exchange gates were refactored to avoid (spawn-verifier/OS watchdog seeing a dead port → false failure/respawn). (Verify correction: not redundant with `startup_exchange_gate` (a reachability probe) but with the scheduler's periodic `_run_exchange_sync` at `scheduler.py:390-391`; non-fatal (try/except) and fast under normal conditions.)
- **Document expectation**: AGENTS.md API-performance rule; `app.py`'s own lifespan comments (`:330-345`) establish that pre-yield work must not delay the port bind.
- **Code evidence**: `aletheia/server/app.py:322-327` (`sync_all_exchanges()` before `yield` at `:382`); `portfolio/exchange_sync.py:410,418` → `get_unified_state_sync()`; `portfolio/uta/manager.py:118-124` (`ThreadPoolExecutor(...).submit(asyncio.run, get_unified_state()).result(timeout=30)`). Contrast `app.py:330-338` deferring the LLM gate post-yield.
- **Reproduction/verification**: reviewer + skeptic read the lifespan and `uta/manager.py`; confirmed the blocking `.result(timeout=30)` path is always taken inside the running lifespan loop.
- **Expected fix direction**: move `sync_all_exchanges()` into a post-yield asyncio background task (same pattern as `_llm_startup_gate`), or `await get_unified_state()` directly, so the socket binds before broker round-trips. **Severity: medium.**

### 139. Exchange-side protective stop is placed for OKX but never updated when trailed/adjusted (bitget-only replacement)

- **Domain**: portfolio/execution — exit-rule / exchange-bracket sync.
- **Impact**: with `cfg.trading.exchange_tpsl_enabled` on, an exchange-side bracket is placed once for OKX, but every later stop tightening leaves the exchange bracket stale: `_maybe_update_trailing_stop` and `adjust_stop` only call `replace_exchange_stop` for bitget modes, and `replace_exchange_stop` exists only in `bitget_broker.py`. The market-aware `tighten_stop` path updates the DB stop but syncs no exchange bracket for ANY broker. So a tightened stop lives only in the DB while the exchange keeps the looser level — if gateway/polling is down the exchange enforces the wrong stop. (Verify correction: OKX placement is called only from the reconciler adoption path, so exposure is limited to ADOPTED OKX positions with the flag on; flag defaults off.)
- **Document expectation**: no contract says trailed stops need not reach the exchange; the bitget path intends exchange sync; `stops.py:1-8` states the shared helper exists so "the historical stop-dropped bug cannot re-appear". `spec_pipeline_stop_propagation.py` locks open-time propagation but not *replacement* propagation.
- **Code evidence**: `portfolio/position_manager.py:213` and `:422` (bitget-only gate before `replace_exchange_stop`), `:374-385` (market-aware tighten writes `update_position(stop_price=...)` with no `replace_exchange_stop`); `execution/bitget_broker.py:448` (`replace_exchange_stop` defined only here); `execution/okx_broker.py:269` (`place_okx_protective_orders` places, no okx replace); `execution/stops.py:63` (okx placement dispatch); called from `position_reconciler.py:515` (adoption).
- **Reproduction/verification**: reviewer + skeptic read all three files; grepped `execution/` confirming `replace_exchange_stop` is bitget-only and placement is okx-only; confirmed `exchange_tpsl_enabled` default `False` (`schema.py:555`).
- **Expected fix direction**: add an okx (and binance) `replace_exchange_stop` and broaden the gate to the active broker plus the market-aware tighten path, OR document the bracket as open-time-only and drop the bitget replace for symmetry. Smallest fix: route all three DB-stop-mutation sites through one broker-agnostic replace dispatcher (mirror `stops.place_exchange_protective_orders`). **Severity: low.**

### 140. Averaging into an open position doesn't accumulate `entry_fee` cost basis in okx/binance/bitget brokers (paper_broker does), overstating realized PnL

- **Domain**: execution / cross-broker consistency.
- **Impact**: on a second+ fill scaling into an open position, the three live brokers update only quantity/entry_price/current_price/stop; stored `positions.entry_fee` stays at the FIRST leg's fee while quantity grows. `pnl.close_position` uses stored `entry_fee` verbatim (re-derives only when NULL) and computes `exit_fee` on the full post-averaging quantity, so the add-on legs' entry fees are never deducted → realized_pnl is overstated on every multi-leg position, and this feeds the learning loop / scorecard. `paper_broker` explicitly guards against exactly this. (Verify correction: only `entry_fee` affects realized PnL — `slippage_cost` is recomputed independently in `close_position` and never subtracted, so the slippage divergence is analytics-only. Overstatement ≈ add-on-leg fees at ~4–6 bps — small in absolute terms but a systematic upward bias in a learning metric.)
- **Document expectation**: `position_enrichment.py` docstring + `paper_broker.py:92-94` — the standing Position must carry the full cost basis of every leg.
- **Code evidence**: `execution/bitget_broker.py:145-154`, `binance_broker.py:123-131`, `okx_broker.py:203-211` (existing-position branch omits `entry_fee`/`slippage_cost`); reference `execution/paper_broker.py:92-100` (accumulates); consumer `portfolio/pnl.py:133-137`.
- **Reproduction/verification**: reviewer + skeptic read all four broker files and `pnl.py:103-165`; confirmed `close_position` reads stored `entry_fee` and `exit_fee` uses full quantity; no spec/contract covers it (silent divergence, not false-green).
- **Expected fix direction**: in the existing-position branch of all three live brokers, add `new_entry_fee = fill_price*fill_qty*fee_rate` to stored `entry_fee` (NULL→0) via `update_kwargs`, mirroring `paper_broker`. Fix all three together (cross-broker rule). **Severity: low-medium.**

### 141. `liquidation_price` not recomputed when averaging into a leveraged position in okx/binance/bitget brokers

- **Domain**: execution / cross-broker consistency.
- **Impact**: averaging into an open leveraged position shifts the weighted `entry_price` but leaves stored `positions.liquidation_price` at the original single-leg estimate, so the persisted distance-to-liquidation goes stale. `paper_broker` recomputes it; the three live brokers don't. (Verify correction: `risk/engine.py:798-801` computes liquidation fresh and does NOT read the stored field, so this is not a hard risk-gate bug — it only affects the LLM near-liquidation warning fed via `trading_context.py:426-443` and UI display.)
- **Document expectation**: `paper_broker.py:82-85` recomputes on averaging-in; the standing Position should carry an accurate liquidation estimate.
- **Code evidence**: `execution/paper_broker.py:81-85` (recomputes, gated `existing_lev>1`); `execution/bitget_broker.py:145-153`, `binance_broker.py:123-131`, `okx_broker.py:203-211` (compute `new_entry`, merge stop, but never update `liquidation_price`; `liquidation_price_for` imported but used only on initial-open).
- **Reproduction/verification**: reviewer + skeptic read all four broker files; confirmed only paper recomputes; confirmed the stored field is display/LLM-only, not risk-gate.
- **Expected fix direction**: in the existing-position branch of all three live brokers, when `existing.leverage > 1`, set `update_kwargs["liquidation_price"] = liquidation_price_for(new_entry, direction, existing["leverage"], symbol)`, mirroring `paper_broker`. Fix all three together. **Severity: low.**

### 142. Binance client bypasses the system proxy (no proxy-aware session / `trust_env`), unlike OKX and Bitget

- **Domain**: exchanges/binance execution client / cross-broker parity.
- **⚠️ Likely already fixed on branch `iridite/system_proxy_support`** (project memory 2026-07-04 records "修 binance broker 绕代理真 bug" on that branch, incl. a `net.make_ccxt` enforcement not present on `iridite/bug_fix`). **Confirm against that branch before fixing here to avoid duplicate/conflicting work.** Recorded because it is real on the current `iridite/bug_fix` branch.
- **Impact**: on a network where outbound exchange access requires the configured proxy (the project's China / system-proxy environment), every Binance execution call (set_leverage/create_order/fetch_order/fetch_positions/fetch_balance/load_markets) goes direct and fails, while OKX and Bitget succeed via the proxy. Silent cross-broker inconsistency; only bites when a Binance mode is active and the environment needs the proxy.
- **Document expectation**: `net.py:1-4` docstring — all outbound HTTP must go through the shared helpers so proxy config is applied consistently; OKX/Bitget comply, Binance doesn't.
- **Code evidence**: `aletheia/exchanges/binance/client.py:70-83 _ensure_exchange()` builds `ccxt.binanceusdm({...})` with no `"session"` key, no `ccxt_timeout_options()`, no `aiohttp_trust_env`; contrast `exchanges/okx/client.py:111-121` (`"session": aiohttp_session()`) and `exchanges/bitget/client.py:118` (`net.httpx_client`). Dispatched live via `execution/binance_broker.py:68,233` for `binance_testnet`/`binance_live_limited` (`execution/gateway.py:281-282,372-373`).
- **Reproduction/verification**: reviewer + skeptic read all three clients + `net.py` + `binance_broker.py`; offline `ccxt.binanceusdm` introspection confirmed `aiohttp_trust_env=False`, `timeout=10000`.
- **Expected fix direction**: mirror OKXClient — pass `"session": aiohttp_session()` (+ `ccxt_timeout_options()`) into the `ccxt.binanceusdm(...)` constructor and close the owned session in `close()`. **First confirm the `iridite/system_proxy_support` fix isn't already merged/pending.** **Severity: medium.**

## Gap-Sweep Findings (unscanned subsystems, verified, 2026-07-06)

> Added by a read-only 16-lane reviewer fan-out over the subsystems the first sweep never opened (risk engine, execution chokepoint, permissions/tooling, runtime self-heal, config/secrets, storage integrity, agent_shell decision + operator, agents producers, data provider/collectors/sources, analytics pnl + learning, model_provider, universe/trading/sizing, operator-shell + misc). Each finding adversarially re-verified by an independent skeptic; 24 candidates → 20 kept, 4 refuted. Severities below reflect the verifier's correction. Two HIGH items: #145 (connectivity auto-resume) and #159 (guard promotion on fabricated data). Lanes that came back clean: execution-chokepoint (order chokepoint invariants all hold — risk-before-order, live-guard, causal-id, no direct broker path, paper/live honesty) and universe-trading-sizing (regime direction correct, persona/dynamic-sizing verified DEFAULT-DORMANT, family/regime quota + signal_emitter dedup correct).

### 143. Risk check #5 drawdown scaling reads position NOTIONAL as equity for `paper_internal` (un-migrated sibling of the 70a7b64 notional-vs-equity trap)

- **Domain**: risk engine (drawdown size cap).
- **Impact**: `_portfolio_drawdown_scale('paper_internal')` computes drawdown from `portfolio_snapshots.total_value_usdt`, which for paper_internal is position NOTIONAL (Σ qty×price), not equity. When positions close, open notional collapses → the formula reads a >20% "drawdown" → `drawdown_scale=0.0` → check #5 sets `effective_cap = max_demo_position_notional_usdt × 0 = 0` → every subsequent paper_internal proposal is rejected (`exceeds_demo_position_notional:{n}>0.0`). Identical mechanism to the 2026-06-25 trap (70a7b64) that was fixed for exchange-backed modes but left on the buggy notional path for paper_internal. **Reachability (verify): active only when `paper_execution_mode='paper_internal'` is explicitly configured; under the default `bitget_demo` the bitget_demo snapshot rows filter to bitget_demo positions and the peak≤0 guard keeps it dormant — so it bites the paper_internal pool specifically.** The `engine.py:486-489` comment claiming "paper_internal total_value_usdt IS its equity" is provably false.
- **Document expectation**: consistency with the 70a7b64 fix (drawdown must be equity-based); AGENTS.md UI/semantic honesty.
- **Code evidence**: `risk/engine.py:508-521` (groups paper_internal+bitget_demo, drawdown=(peak-current)/peak on notional series), `:135,143-145` (effective_cap = cap × scale); `portfolio/pnl.py:416-427` (writes `total_value_usdt=round(local_notional,4)`, pure notional). Fake-green: `spec_risk_engine.py` RE-B05 mocks equity-like values; RE-B05a only covers okx_demo→exchange_account — no spec exercises real paper_internal notional data.
- **Reproduction/verification**: reviewer + skeptic traced producer→storage→consumer; confirmed the notional series feeds the drawdown cap and no spec covers the real path.
- **Expected fix direction**: store true equity (cash + mark-to-market) for the paper_internal snapshot, or route paper_internal drawdown to an equity source as exchange modes do; also stop grouping bitget_demo notional into the paper_internal series. Fix the false comment. **Severity: medium.**

### 144. Reconciler run recorded `status="completed"` while carrying per-item errors — evades the health failure-ratio signal

- **Domain**: agent_runtime health honesty / run-status truthfulness.
- **Impact**: `health.compute_health()` derives `agent_failure_ratio` purely from the run status string, and `_FAILED_STATUSES` omits plain `"completed"`. The position_reconciler job catches per-item errors (failed adopt / failed orphan close) and appends them without raising, then logs the run with hardcoded `status="completed"`. A reconciler that fails every cycle (broken creds, exchange rejects close) is persistently "completed", never counts as failed, so the 10-min health/self-heal consumer is blind to it. Diverges from the orchestrator's correct convention (`completed_with_errors` when errors exist).
- **Document expectation**: UI/semantic honesty + health-signal-consumption; match `orchestrator.py:44` convention.
- **Code evidence**: `portfolio/position_reconciler.py:738-749` (append errors, no raise); `agent_runtime/scheduler.py:1008` (`end_run(... status="completed" ...)` hardcoded); `agent_runtime/health.py:18` (`_FAILED_STATUSES` no `completed`), `:133-138` (ratio keyed on status). Contrast `orchestrator.py:44`.
- **Reproduction/verification**: reviewer + skeptic read all cited lines; confirmed statically.
- **Expected fix direction**: in `scheduler.py:1008` pass `status="completed_with_errors" if summary.get("errors") else "completed"`, or have health/end_run treat a non-empty errors list as failed. **Severity: low.**

### 145. Exchange-connectivity auto-resume task is orphaned on the ephemeral per-job event loop — a mid-run connectivity loss pauses trading FOREVER

- **Domain**: self-heal / connectivity resilience. **HIGH — top of this batch.**
- **Impact**: the documented exchange-connectivity auto-pause/resume only works when the pause is triggered at STARTUP. When connectivity is lost during normal operation (the common case), the system pauses correctly but NEVER auto-resumes even after connectivity returns — the operator must resume by hand; on restart it stays paused (persisted `paused=True`, and `startup_exchange_gate` only pauses, never resumes on success).
- **Document expectation**: the connectivity design (mirrors the LLM gate) promises re-probe-and-resume; `connectivity.py` docstring says it starts an independent task that "probes until connectivity returns, then resumes."
- **Code evidence**: `agent_runtime/connectivity.py:170-173` `_pause_for_network` does `asyncio.get_running_loop().create_task(_exchange_auto_resume_loop(...))`; reached from the periodic `run_connectivity_check` (`connectivity.py:117`) which runs via `scheduler.py:671 _run_async_with_timeout` on a **dedicated daemon loop created at `scheduler.py:264` and closed at `:270-278`** — so the resume task is destroyed pending when that loop closes and its `await asyncio.sleep()` never fires. The startup path works only because `startup_exchange_gate` runs on the persistent uvicorn loop (`app.py:345`). Contrast the LLM `auto_resume` (started only from `app.py:250-251` on the uvicorn loop) which never hits this. Fake-green: `test_connectivity.py` monkeypatches `_exchange_auto_resume_loop` to an AsyncMock / awaits it directly, hiding the orphaned-task defect.
- **Reproduction/verification**: reviewer + skeptic traced the loop lifecycle in `scheduler.py:257-278` and confirmed the tests never exercise the create-task-on-ephemeral-loop path.
- **Expected fix direction**: schedule the resume loop on the persistent uvicorn loop (capture it at startup as `pi_daemon` does with `_uvicorn_loop`, then `run_coroutine_threadsafe`), or hand the pause+resume off to a task on the captured uvicorn loop. **Severity: high.**

### 146. Both auto-resume loops share one `runtime.paused` flag but each only validates its own dependency — first-to-recover resumes the whole system while the other outage persists

- **Domain**: self-heal / startup gates.
- **Impact**: when both LLM providers and the exchange are down at startup (common shared-network failure), both auto-resume loops run. If exchange connectivity returns first, `_resume_from_network` clears `runtime.paused` and the LLM `auto_resume` loop then exits on its next tick (`if not cfg.runtime.paused: return`) — leaving the system running unpaused with the LLM still down: the exact "looks alive, is dead" zombie the LLM startup gate exists to prevent. No periodic LLM auto-pause after boot re-catches it.
- **Document expectation**: `startup_gate.py` docstring — a gateway with no working LLM "must NOT run"; the two gates are meant to be independent guards.
- **Code evidence**: `agent_runtime/connectivity.py:214-223` `_resume_from_network` sets `paused=False` with no LLM check; `agent_runtime/auto_resume.py:33-36` the LLM loop exits whenever `not cfg.runtime.paused` regardless of which subsystem cleared it; `resume_gate.py:27-36` symmetric.
- **Reproduction/verification**: reviewer + skeptic read all cited lines.
- **Expected fix direction**: track pause CAUSES (a `paused_reasons` set `{'llm','exchange'}`) and resume only when all clear; or have each resume path re-verify the other dependency before clearing `paused`. **Severity: medium.**

### 147. `load_config()` TTL cache returns a shared mutable config — a rejected multi-field settings update leaks unsaved values for up to 5s

- **Domain**: config/loader + settings routes.
- **Impact**: `load_config()` returns the SAME `AletheiaConfig` instance to every caller for the 5s TTL (no `model_copy`). Update routes do `cfg = load_config(); setattr(...); save_config(cfg)` and validate bounds mid-loop, raising `HTTPException` before `save_config` on a bad field. Fields set before the raise are mutated in the shared cache but never persisted → for up to 5s `GET /settings` and every `load_config()` consumer reads the rejected values, then the cache expires and silently reverts. UI/semantic-honesty violation. (Verify: **requires a MULTI-field update where a valid field precedes an invalid one** — a single-field failing PATCH raises before its own setattr, so the common UI path is unaffected → downgraded medium→low.)
- **Document expectation**: AGENTS.md Config↔UI rule + UI/semantic honesty; the `loader.py:114-119` cache comment asserts mutations are invalidated only by `save_config`.
- **Code evidence**: `config/loader.py:130-133` (returns cached cfg, no copy); `server/routes/settings.py:503-512` (health) and `:993-1010` (market-data) setattr earlier fields then raise before `save_config`. Verified via temp-dir repro: two `load_config()` calls return the same object; mutating one without save leaks to a fresh `load_config()` while disk is unchanged.
- **Reproduction/verification**: reviewer temp-dir in-process repro; skeptic confirmed the multi-field precondition.
- **Expected fix direction**: return `cfg.model_copy(deep=True)` from `load_config()`, OR validate-all-then-apply in the routes, OR mutate a copy and commit only via `save_config`. **Severity: low.**

### 148. `config_consistency` demo-credential fix hints tell operators to set env vars that NO code ever reads

- **Domain**: diagnostics/config_consistency (misleading remediation).
- **Impact**: `_DEMO_CRED_MAP` fix hints say "Set `BITGET_API_KEY_DEMO` / `OKX_*` / `BINANCE_*`" to clear a missing-demo-credentials warning, but credentials are retrieved only via `get_secret('bitget.demo.api_key')` (keyring/encrypted file) — `get_secret` has no `os.environ` branch, and these env-var names appear ONLY in these fix hints. An operator who exports them neither supplies working creds nor clears the warning.
- **Document expectation**: fix hints should match `doctor.py:605`, which already gives the correct secrets-based remediation.
- **Code evidence**: `diagnostics/config_consistency.py:66-69` (env-var names in hints); `config/secrets.py:34-42` (`get_secret` = keyring→enc file, no environ); grep confirms these env names appear nowhere else. Correct hint reference: `diagnostics/doctor.py:605`.
- **Reproduction/verification**: skeptic confirmed via grep + reading `get_secret`.
- **Expected fix direction**: rewrite the `_DEMO_CRED_MAP` hints to reference Settings → Credentials / `aletheia secrets set bitget.demo.api_key` (matching `doctor.py:605`). **Severity: low.**

### 149. `onchain_snapshots` grows unbounded — `prune_onchain_snapshots()` defined but never wired into the cleanup job

- **Domain**: storage/retention (app.db bloat).
- **Impact**: the DefiLlama collector writes up to 3 rows/cycle into `onchain_snapshots`. A `prune_onchain_snapshots(days=30)` exists but has zero callers — it's absent from the `_run_cleanup` maintenance dict while its siblings (`macro_snapshots`, `coingecko_top_snapshots`) are present, so the table never shrinks. Same app.db-bloat class already fixed for heartbeats. (Verify: DefiLlama runs ~every 10 min via a `tick % 5 == 1` gate, not every 2 min → ~432 rows/day (~158k/yr), ~5× slower than the reviewer's "2,000+/day" → downgraded high→low-medium.)
- **Document expectation**: retention discipline parity with sibling snapshot tables; app.db-size memory (390M bloat root cause).
- **Code evidence**: `storage/repositories.py:1918` defines `prune_onchain_snapshots` (correct DELETE + was_in_tx); `data/collectors/defillama.py:83-85` inserts; `agent_runtime/scheduler.py:1160-1191` `_run_cleanup` counts dict omits it; grep for `prune_onchain_snapshots` returns only the definition.
- **Reproduction/verification**: reviewer + skeptic traced producer→schedule→prune; confirmed zero callers.
- **Expected fix direction**: add `"onchain_snapshots": repo.prune_onchain_snapshots(30)` to the `_run_cleanup` counts dict (idx already covers `observed_at`). **Severity: low-medium.**

### 150. `prediction_probability_snapshots` has an active producer but no prune function at all

- **Domain**: storage/retention.
- **Impact**: `prediction_observer` inserts one probability snapshot per observed market every 30 min; no `prune_*` exists for the table and it's absent from `_run_cleanup`, so it grows without bound. Lower volume than #149 and gated behind `prediction_enabled` (default False), so it only grows once the Prediction Lens feature is enabled.
- **Document expectation**: retention parity with sibling observation-only snapshot tables.
- **Code evidence**: producer `agents/prediction_observer.py:126`; schedule `agent_runtime/scheduler.py:472-474` (30 min); `_insert` `storage/repositories.py:277-281`; grep for `prune.*prediction`/`prune.*probability` returns nothing.
- **Reproduction/verification**: skeptic confirmed producer + cadence + absence of any prune; gated on `prediction_enabled` default False (`schema.py:139`).
- **Expected fix direction**: add `prune_prediction_probability_snapshots(days=90)` (follow `_delete_old_rows(ts_col='ts')`) and wire into `_run_cleanup`, or document the table as intentionally retained. **Severity: low.**

### 151. Explicit LLM `market_type='spot'` + `direction='short'` is not normalized — an impossible spot-short proposal reaches the broker

- **Domain**: decision-chain / symbol-market propagation.
- **Impact**: the decision engine's own comment states "spot cannot be shorted, so only a short forces perp", but that correction applies ONLY in the fallback branch where `market_type` is unset. When the LLM explicitly returns `market_type='spot'` AND `direction='short'` (both allowed by `_DECISION_SCHEMA`), `_parse_decision` keeps it (no cross-check) and `submit_decision_as_proposal` skips the fallback, building a spot+short proposal. The Risk Engine has no spot-short check. Result: a nonsensical / exchange-rejected "short spot" order or a fictional short-spot paper fill polluting learning data. (Verify: requires the LLM to violate an explicit prompt instruction; impact is a rejected order / bad learning row, not capital loss → downgraded medium→low; still worth recording given the Serenity-hallucination precedent that prompt-only constraints are insufficient at this boundary.)
- **Document expectation**: `decision_lifecycle_contract.md §2.2` + prompt (`trading_decision.py:1049`): "A short can only be expressed on usdt_futures"; the code comment at `:353-358` asserts the same.
- **Code evidence**: `agent_shell/trading_decision.py:356-358` (correction gated on `not market_type`), `:1172-1206` `_parse_decision` (only validates mt ∈ {spot,usdt_futures}, no direction cross-check), `_DECISION_SCHEMA:115-142` (direction + market_type independent); `risk/engine.py:176-180` (short check ignores market_type; no spot-short guard anywhere).
- **Reproduction/verification**: reviewer + skeptic read all four files + engine + schema defaults (spot enabled, allow_short_demo True); confirmed no spot-short guard on the path.
- **Expected fix direction**: force `market_type='usdt_futures'` whenever `direction=='short'` regardless of the LLM's explicit choice (apply the correction unconditionally), or reject spot+short in `_parse_decision` / the risk engine. **Severity: low.**

### 152. Attribution silently severed when the PI trades opposite to a same-symbol opportunity — ranker/serenity fallback is skipped (DL-INV-4)

- **Domain**: decision-chain / opportunity_id linkage.
- **Impact**: in `_attach_opportunity_context`, any same-symbol opportunity (even one whose `direction_bias` conflicts) sets `saw_same_symbol_opportunity=True`; the post-loop `if saw_same_symbol_opportunity: return` exits BEFORE the ranker-candidate and serenity fallbacks. So when the only Layer-1 opp for that symbol has the opposite bias but a ranker candidate (same market_observer provenance, matching direction, carrying a real `opportunity_id`) exists, `opportunity_id/strategy_id/scanner_family` stay None → the proposal and `pi_decisions` row get a null causal parent, breaking learning-loop attribution. Reachability: layer1 opps (recent-10) vs ranker candidates (top-8 by score) read the same table with different limits/ordering, so a matching-direction opp can be in ranker top-8 yet absent from layer1 while a conflicting one sits in layer1.
- **Document expectation**: `decision_lifecycle_contract.md §4 DL-INV-4` (backfill opportunity_id or the learning loop breaks); the ranker candidates are documented as a second id source to try before Serenity.
- **Code evidence**: `agent_shell/trading_decision.py:1240` (sets flag before direction check), `:1244-1245` (continue on conflict), `:1256-1257` (unconditional return before ranker loop `:1263` and serenity `:1284,:1303`).
- **Reproduction/verification**: reviewer + skeptic traced the full control flow (1233-1323); confirmed the early return fires once any same-symbol opp is seen regardless of direction.
- **Expected fix direction**: run the ranker-candidate loop before the `saw_same_symbol_opportunity` early return (same provenance), or drop the early return so ranker/serenity fallbacks still run. **Severity: low.**

### 153. L1/L2 self-heal actions are unreachable dead code and their "PI approval gate" is a no-op rubber stamp — specs pass green (fake-green) while `operator_layer.md §不变式1` is unenforced

- **Domain**: SDD fake-green / self-heal safety invariant (operator loop).
- **Impact**: two coupled defects: (1) `decide_actions()` never constructs any self-heal `OperatorAction` (clean_stale_heartbeats/restart_collector/rebuild_sqlite_index or the L2 clear_signal_pending/modify_prompt_fragment/modify_config_fragment), and `run_operator_loop → decide_actions` is the ONLY path to `_execute_level1_action`/`_create_proposal` — so the entire documented L1/L2 self-heal capability is unreachable at runtime. (2) Even if reached, `_request_pi_approval_for_selfheal` cannot deny — it writes a `recommend_l1_selfheal` row and returns; the handler executes regardless (its own docstring: "Execution is not blocked"), contradicting `§不变式1` ("PI 不批准则不执行"). Specs OL-C08/SH-C02 only assert a function whose name contains `pi_approval` is called; SH-S01/OL-S13/SH-B03 only assert set membership / a source substring — all pass `status="implemented"` while the real gate and capability don't exist. (No live auto-execution risk TODAY because the actions are unreachable — the defect is fake-green + dead code + an unenforced documented safety invariant.)
- **Document expectation**: `operator_layer.md §监管/自愈契约 不变式1` (L1/L2 self-heal must obtain PI approval before executing); `§自愈档位` presents L1/L2 as implemented. Note: `operator_layer.md:87/93/97` and the in-code comment `operator_loop.py:264` declare these specs xfail/missing — contradicting the spec files' `status="implemented"`.
- **Code evidence**: `agent_shell/operator_loop.py:590-598` (dispatch calls approval then `_action_*` unconditionally), `:967-993` (`_request_pi_approval_for_selfheal` no return/no block), `:1141-1290` (`decide_actions` emits only tighten/reduce/loosen/…/notify — no self-heal action); grep found zero emitters of the six self-heal action names outside operator_loop+specs; `spec_operator_loop.py:816-849` (SH-C02), `:508-543` (OL-C08), `:781-800` (SH-S01), `:582-590` (OL-S13) are structural/substring checks.
- **Reproduction/verification**: reviewer + skeptic confirmed the only callers of `run_operator_loop` (scheduler + operator route) run the full loop, the approval fn has no blocking logic, and the specs are substring-only.
- **Expected fix direction**: either wire `decide_actions` to emit self-heal actions when the relevant doctor/content probes fire (make the path reachable), or explicitly mark the self-heal action set + specs as deferred/not-implemented (remove the false-green); and make `_request_pi_approval_for_selfheal` (or the executor) actually gate — return a decision and skip the handler when approval is absent/negative. **Severity: medium.**

### 154. `divergence_scanner` infers trade direction from probability-change sign, ignoring question polarity — inverted execution-eligible signals for bearishly-framed prediction markets

- **Domain**: agents/divergence_scanner (opportunity generation).
- **Impact**: direction is derived purely from the sign of the 24h probability move (`long if prob_change>0 else short`), but `_map_question_to_symbol` extracts only the asset keyword and discards the question's semantic polarity. For a bearishly-framed Polymarket question ("Will BTC fall below $80k?"), rising YES is BEARISH, yet the scanner emits a LONG BTC opportunity with a hard-coded bullish thesis. These `execution_eligible=True` opps feed the decision chain with a wrong direction prior. (Verify: gated behind `prediction_enabled` (default False), and Polymarket crypto questions skew up-framed, so inverted cases are a minority not "half"; PI re-evaluates the candidate → downgraded medium→low.)
- **Document expectation**: `agent_contracts.md:80-86` — divergence alerts must carry a correct `direction_bias`, not one inverted for a class of question framings.
- **Code evidence**: `agents/divergence_scanner.py:170` (`direction = "long" if prob_change > 0 else "short"`), `:61-71` (`_map_question_to_symbol` maps only asset keywords), `:176-180` (thesis assumes prob-up=bullish), `:185-207` (`execution_eligible=True`); schema stores no parsed polarity (`migrations.py:1274-1282`); chained every 30 min at `scheduler.py:1138-1140` (gated on `prediction_enabled`).
- **Reproduction/verification**: reviewer + skeptic confirmed no polarity handling and no downstream correction; reachability config-gated.
- **Expected fix direction**: parse question framing (restrict to up-framed "reach/above/hit $X"; invert for "fall/below/drop/crash"), or gate to a curated up-only market set; until then emit these opps `execution_eligible=False` (observation-only). **Severity: low.**

### 155. OHLCV TTL cache key omits `market_type` — spot and perp candles collide for the same symbol

- **Domain**: market-data / cache correctness.
- **Impact**: the process-global `OHLCVCache` is keyed on `(canonical_symbol, timeframe, limit)` only; `market_type` is dropped even though the fetched candles differ (spot BTC/USDT vs perp BTC/USDT:USDT). Two consumers requesting the same symbol/tf/limit but different market_type within the TTL cross-contaminate (first writer wins): (a) the chart route can serve spot candles under a perp-labelled chart; (b) `market_context.py` (an LLM decision-context path) fetches perp candles via `to_ccxt_perp()` but stores them under the bare symbol. (Verify: latent, not currently reachable — the real frontend requests `limit=300` (cache requires `limit==100`) and only `market_type='spot'`, and the universe emits one market_type per symbol so the same symbol isn't scanned as both within the 10-min TTL → downgraded medium→low.)
- **Document expectation**: `symbols.py` docstring + `InstrumentId(exchange:symbol:market_type)` exist so the same symbol on different market types never collides; `spec_market_type_dedup.py OD-S02` fixed this class for opportunity lookups but no spec covers the OHLCV read cache.
- **Code evidence**: `data/cache.py:41-42` (`_key` = `(symbol,timeframe,limit)`, no market_type); `data/ccxt_provider.py:165,170,196` (only market-type-stripped `canon` keys the cache); `server/routes/data.py:840-841` (`cache_key = sym`, `use_cache` gated on `limit==100` not market_type); `trading/market_context.py:208,211,214` (perp fetch stored under bare symbol).
- **Reproduction/verification**: reviewer + skeptic read cache/provider/symbols/route/market_context and the specs; confirmed the key never receives market_type and the scenarios are currently latent.
- **Expected fix direction**: include `market_type` (and ideally exchange) in the cache key — thread the resolved market_type through `_resolve_ccxt_symbol` into `cache.get/set`; minimal alt for the chart route: gate `use_cache` on `market_type=='spot'`. **Severity: low.**

### 156. Coinalyze OI collector: OI parsed by guessed key names (may not match the live schema → dead feed) AND, if it writes, its aggregated OI pollutes the primary per-symbol latest-OI read

- **Domain**: data/collectors + storage (OI latest read). **Combined finding — resolve the schema question first.**
- **Impact**: two coupled risks, both gated on a `coinalyze_api_key` (default none → collector writes nothing):
  1. **Parser/dead-feed**: `_fetch_open_interest` reads `open_interest_usd`/`openInterestUsd`, then falls back to `open_interest`/`openInterest`, and on the coin fallback stores the coin count into `open_interest_usd` (`:159`, a coin-as-USD magnitude bug). A reviewer checked the published Coinalyze `/open-interest` OpenAPI and reports the actual field is `value` (with `convert_to_usd` query param) — **if correct, none of the four guessed keys exist → every row is skipped (`:157-158 continue`) → the OI collector silently writes 0 rows (a dead feed), and the coin-as-USD line is never reached.**
  2. **Pollution (only if it does write)**: coinalyze writes aggregated cross-venue OI (Binance+OKX+Bybit+Bitget summed) with `exchange="coinalyze"`, which is NOT in `_CROSS_VENUE_TAGS`. So a coinalyze row (later `observed_at` in the same tick → deterministically wins) fills the primary `latest_open_interest(sym)` LIMIT-1 slot consumed by `market_observer`/`context_builder`/`universe.features` → an aggregate magnitude fed to the LLM as the primary venue's OI, plus spurious `oi_change_1h_pct` → corrupts `normalizer.crowding_score`. Hyperliquid got a dedicated exchange-filtered read (DU-S02) precisely to avoid this; coinalyze (added the same batch) was not.
- **Document expectation**: `data_utilization.md §3` lists coinalyze OI (row 60) and okx OI (row 44) as SEPARATE feeds; coinalyze is a distinct cross-exchange aggregate, not a substitute for the primary venue series.
- **Code evidence**: parser `data/collectors/coinalyze.py:147-185` (`:153-159` key names + coin-as-USD fallback; `:177-185` writes `exchange="coinalyze"`); pollution `storage/repositories.py:1714` (`_CROSS_VENUE_TAGS=("hyperliquid","hyperliquid_predicted")` — no coinalyze), `:1717-1729` (`_latest_deriv_where`); consumers `market_observer.py:226`, `context_builder.py:242`, `universe/features.py:206`; deltas `derivatives.py:172-176`, `coinalyze.py:168-173`; crowding `normalizer.py:68-69`. Hand-confirmed the parser reads the four guessed keys, not `value`.
- **Reproduction/verification**: two agents (one refuting the parser finding against the live API doc, one confirming the pollution) plus my own read of `coinalyze.py:60-189`. The tension is resolved by the shared gate (no key → no rows) and the schema question.
- **Expected fix direction**: **first verify the live `/open-interest` field name.** If it is `value` (or `convert_to_usd`), fix the parser to read `value` and either request `convert_to_usd=true` (store into `open_interest_usd`) or store into `open_interest_coin` with `open_interest_usd=None` (never store a coin count as USD). THEN add `"coinalyze"` to `_CROSS_VENUE_TAGS` and give crowding a dedicated `latest_open_interest(sym, exchange="coinalyze")` read (mirror the hyperliquid pattern) so the primary venue series stays clean. **Severity: medium.**

### 157. `render_calibration_for_pi` crashes on every real report (broken f-string) — silently disabling conviction-calibration feedback to the System Operator LLM

- **Domain**: analytics / learning-loop feedback.
- **Impact**: `build_calibration_report` always stores `ece` as a float, so any persisted report has a numeric ece; `render_calibration_for_pi` then hits an invalid f-string format spec (`{ece:.3f if ece else '?'}`) and raises `ValueError` every time. The only caller wraps it in a bare `except` that sets `calibration_feedback=None`, so the crash is swallowed and the feedback (whether the operator is over/under-confident per conviction bucket) is silently dropped — a learning signal the codebase computes, persists, and intends to inject is dead in production. The AN-S* specs only exercise `build_calibration_report` shape, not the render → false-green.
- **Document expectation**: module docstring + `render_calibration_for_pi` — calibration is injected into the Layer-2 System Operator prompt; `go_live_scorecard` treats calibration as a live criterion.
- **Code evidence**: `analytics/confidence_calibration.py:161` (`f"...ECE={ece:.3f if ece else '?'} ..."` — the colon makes `.3f if ece else '?'` the format spec → invalid), `:91` (`ece = round(ece,4)`), `:122` (stored). Caller `agent_shell/trading_context.py:594-598` (try/except sets `calibration_feedback=None`). No spec calls `render_calibration_for_pi`.
- **Reproduction/verification**: reviewer reproduced twice via `uv run python` (`ece=0.123` and `0.0` both raise `ValueError Invalid format specifier`); skeptic confirmed the caller swallows it.
- **Expected fix direction**: precompute `ece_str = f"{ece:.3f}" if ece is not None else "?"` then interpolate `{ece_str}`; add a spec that actually calls `render_calibration_for_pi` on a numeric-ece report. **Severity: medium.**

### 158. `trade_insights` avg_pnl_pct for the `unknown` close_reason / symbol / direction buckets always reports ~0 (operator-precedence bug)

- **Domain**: analytics / PnL aggregation (Trade Insights panel).
- **Impact**: in `_by_exit_reason`, NULL-`close_reason` rows are keyed under `'unknown'`, but the avg_pnl_pct re-scan filter `(r["close_reason"] if "close_reason" in r.keys() else None or "unknown") == reason` binds `None or "unknown"` as the else-value and, since the column always exists, returns the raw value (None for NULL rows) → `None == "unknown"` is always False → the `unknown` bucket's avg is summed over 0 rows = 0.0 (while win_rate/total_pnl are correct). Same precedence pattern in `_symbol_direction_matrix`. Cosmetic/analytics-panel only (not fed to go-live/risk), but the displayed average is silently false for those buckets.
- **Document expectation**: the panel should show the actual per-bucket average PnL%.
- **Code evidence**: `analytics/trade_insights.py:517-519` (precedence bug; bucket keying at `:503-505` maps None→'unknown', re-scan disagrees); parallel at `:404-406` vs cell key `:390`.
- **Reproduction/verification**: reviewer + skeptic traced Python precedence; else branch is dead code.
- **Expected fix direction**: compute `_pnl_pct` once per row and accumulate into the same normalized bucket dict used for count/total (single pass), instead of re-scanning with a mismatched normalization expression. **Severity: low.**

### 159. Guard promotion & effectiveness compute loss-rate from raw attribution rows that INCLUDE synthetic/administrative closes (and skip the clean-window floor) — can promote/deprecate live guards on fabricated evidence

- **Domain**: analytics/learning — guard promotion (candidate→active) + effectiveness. **HIGH.**
- **Impact**: `run_promotion_check()` runs on a daily 04:00 cron with `guard.auto_promote_enabled` default True, so it MUTATES live guard state. It derives `loss_rate = losses/matched` from `build_trade_attribution_rows(limit=500)` applying NEITHER `is_synthetic_close()` NOR `clean_window_cutoff()`. So reconciler orphan sweeps (`reconciled_gone`), `demo_validation_cleanup`, `units_bug_cleanup`, `retry_after_fix` — closes that mark-to-last-price near entry and are explicitly NOT trade outcomes — are counted as wins/losses. A candidate guard can be promoted to an active HARD-BLOCKING guard (`score_penalty=10.0, block_candidate=True`) on fabricated reconciler-sweep data, permanently suppressing every future opportunity of that scanner_family+direction; or a protective guard deprecated when synthetic near-zero closes dilute its loss_rate below 0.3. `guard_effectiveness.compute_guard_stats` has the same omission (distorts CLI/doctor stats). `close_reasons.py:22` states the contract: "Every consumer that reports trade PERFORMANCE must exclude them" — honored by `factor_feedback`/`trade_case_builder`, VIOLATED here. (Synthetic closes are near-zero PnL so they add noise both ways; at `min_matching_trades=3` that noise still tips the 0.6/0.3 thresholds.)
- **Document expectation**: `close_reasons.py:22-23` (every performance consumer must exclude synthetic closes); clean-window floor quarantines pre-2026-06-25 dirty data (sibling learners all apply it).
- **Code evidence**: `analytics/guard_promotion.py:40` (no filter), `:49-52` (promote ≥0.6), `:57` (deprecate <0.3), `:100-105` (active hard-blocking guard); `analytics/guard_effectiveness.py:35,53-54,66` (same omission); `analytics/trade_attribution.py:19-33,101` (raw, no filter); contract `analytics/close_reasons.py:22-23`; contrast `factor_feedback.py:25-32,47` (`_genuine_rows`); `config/schema.py:720` (auto_promote_enabled True); `agent_runtime/scheduler.py:516-517` (daily 04:00 cron).
- **Reproduction/verification**: reviewer + skeptic read all modules; grepped `is_synthetic_close`/`clean_window_cutoff` usage (only siblings apply them); confirmed candidates carry predicates so the matcher fires and the job mutates guard files.
- **Expected fix direction**: wrap both reads in `_genuine_rows()`/`is_synthetic_close()` (mirror `factor_feedback`), and filter `closed_at` through `clean_window_cutoff`, in `guard_promotion.run_promotion_check` and `guard_effectiveness.compute_guard_stats`. **Severity: high.**

### 160. Trading-memo synthesizer and reasoning-feedback fit their models on positions that include synthetic/administrative closes — the memo steering the System Operator prompt is built on fabricated exits

- **Domain**: analytics/learning — batch memo synthesis + reasoning feedback (both feed the System Operator prompt).
- **Impact**: `insight_synthesizer._gather_statistics` selects closed positions filtered ONLY by `clean_window_cutoff`, never by `close_reason`; every downstream compute (regime playbook edge, anti-patterns `loss_rate≥70→avoid`, symbol affinity, direction bias) treats `reconciled_gone`/`demo_validation_cleanup` rows as real outcomes. The resulting `TradingMemo` is rendered into the market_observer/System Operator prompt. `reasoning_feedback.build_reasoning_feedback` has the same gap (classifies executed_wins/losses by realized_pnl with no synthetic filter, injects recommended_avoid/pursue into the Layer-2 prompt). The clean-window floor does NOT help — the reconciler false-orphan thrash (~41% `reconciled_gone`) ran until the 2026-07-01 broker-safety fix, inside the clean window. Same `close_reasons.py` contract violation as #159, in the two consumers with the widest reach (the PI prompt).
- **Document expectation**: `close_reasons.py:22-23`; contrast the already-compliant `factor_feedback.py:47` / `trade_case_builder.py:71-75`.
- **Code evidence**: `analytics/insight_synthesizer.py:176-183` (clean_window only, no close_reason filter), `:378-402` (anti_patterns), `:405-451` (direction_summary), `:509-533` (render into prompt); `analytics/reasoning_feedback.py:35-55,71-77` (win/loss by realized_pnl, no filter); neither imports `close_reasons`/`is_synthetic_close` (grep).
- **Reproduction/verification**: reviewer + skeptic read both modules fully; confirmed the clean-window floor predates the 2026-07-01 reconciler fix.
- **Expected fix direction**: add a `close_reason NOT IN (SYNTHETIC_CLOSE_REASONS)` exclusion in `_gather_statistics` and `build_reasoning_feedback`, reusing `close_reasons.is_synthetic_close` as the single source of truth. **Severity: medium.**

### 161. `complete_with_fallback` resolves fallback config + health tracking against `default_provider`, not the tier-selected provider actually used

- **Domain**: model_provider / deep-fast routing + fallback.
- **Impact**: when the operator configures a distinct `deep_provider`/`fast_provider` (the whole point of the MR-* two-tier routing, UI-settable), agent LLM calls apply the WRONG provider's fallback config and record health/cooldown against the wrong provider name. For a deep-tier call (e.g. the core trade-decision path): if the default provider has `model_fallback_enabled=False`, the deep provider gets NO fallback; if the default's `fallback_models=[X,Y]`, those default-only slugs are cloned onto the deep instance and POSTed to the deep endpoint on a transient error. All `llm_model_cooldown`/`fallback_used`/`recovered` events and health snapshots are labeled with the default provider name for models that belong to the deep/fast provider. Primary-model calls still work; the defect bites the fallback/health path whenever a tier provider differs from default (and, per verify, mislabels health on the success path too since `_record_model_success` uses the wrong name).
- **Document expectation**: `agent_contracts.md` MR-* claims — deep/fast agents use the tier provider end-to-end; per-provider `model_fallback_enabled`/`fallback_models` (`schema.py:235-236`) are the fallback source for THAT provider.
- **Code evidence**: `agents/helpers.py:22-27,130-136` pass `provider=<instance>` but never `provider_name`; `model_provider/fallback.py:233-236` `resolved_provider_name = provider_name or cfg.models.default_provider` → always default when None; `:169-172` reads pcfg from the wrong provider; `:242-256,266,276-284,299` (cooldown keys / event ids). Contrast `startup_gate.py:73-76` which correctly passes `provider_name=name`. `trading_decision.py:452,476` route via `get_provider_for_tier('deep')`.
- **Reproduction/verification**: reviewer + skeptic static-traced helpers→fallback→schema→startup_gate + the tier call sites + `routes/model.py` (tier providers are real user-configurable state).
- **Expected fix direction**: thread the resolved tier provider name string into `complete_with_fallback` as `provider_name=` (preserve it before `get_provider()` reassigns to an instance), or resolve `provider_name` from the passed instance's config key instead of defaulting. **Severity: medium.**

### 162. `proposal-pack` export ignores `proposal_id` and dumps the ENTIRE proposals + serenity research tables

- **Domain**: sync/export.
- **Impact**: `ExportService.export_bundle(mode="proposal-pack", proposal_id=X)` is documented as "Export a single proposal bundle" and validates `proposal_id` as required, but the id is never used to scope the query — `_export_proposal` calls `_export_db_tables([...], where_clause=None)` and `_export_db_tables` never references `where_clause` (only optionally applies `since`). So the bundle contains every row of `trade_proposals`, `serenity_themes`, `serenity_evidence`, `serenity_claims` — the full proposal + research history — plus all proposal artifacts. A user building a "single proposal pack" to hand off unknowingly ships the entire trading-intent and research corpus. (Distinct from #62, which flagged the same `proposal-pack` scoping at the CLI level — this pins the `where_clause`-is-dead root cause in the export service.)
- **Document expectation**: the method docstring "Export a single proposal bundle" + the required-`proposal_id` guard.
- **Code evidence**: `sync/exporter.py:98-105` (`_export_proposal` requires `proposal_id` then calls `_export_db_tables(..., where_clause=None)`, id never passed), `:129-149` (`_export_db_tables` accepts `where_clause` but the body only builds `SELECT * FROM {table}` + optional `WHERE created_at > ?`; `where_clause` is dead — grep confirms it appears only at the call and the unused param).
- **Reproduction/verification**: reviewer + skeptic read `exporter.py` end-to-end; confirmed `where_clause` has no read site.
- **Expected fix direction**: have `_export_proposal` pass a real per-proposal filter and make `_export_db_tables` apply `where_clause` with bound params — scope `trade_proposals` to the id and the `serenity_*` tables to rows linked to it. **Severity: medium.**

## Final Coverage Findings (frontend + backend zeros, verified, 2026-07-06)

> Added by a read-only 12-lane final sweep over the last unscanned areas: the frontend page-by-page (Settings, CommandCenter, Serenity+Learning, Prediction+News+Universe, Trades+Charts+Proposals, Risk+Overview+AgentActivity, the uncovered components, and shared App/client/types/lib+charts) plus the backend zeros (`knowledge/`, `runtime_continuity/`+`portfolio/uta`, `diagnostics/doctor.py`, `cli/`). Reviewers applied the house UI rules (UI honesty, `fmtSymbol`, the `+00:00`-vs-`Z` ISO trap, loading lanes, `onError`) and only reported issues beyond the ~30 frontend findings already recorded. 21 candidates → 18 kept, 3 refuted. All adversarially re-verified. Several frontend lanes came back largely clean (fe-prediction-news-universe, fe-components) — the primitives are well-maintained.

### 163. Settings "Config Coverage Matrix" mislabels ~18 editable fields as "read-only / config.yaml" (honesty inversion)

- **Domain**: frontend / Settings config-coverage. (Specific open residual of the #35–#37 class — the matrix's own hardcoded `controlled` set drifted.)
- **Impact**: the Advanced-tab matrix exists to honestly show which config keys have a UI control vs config.yaml-only. Its hardcoded `controlled` set omits 18 keys that DO have working controls above, so it renders "read-only / config.yaml" for them — including 11 **safety-critical** per-tier leverage + margin keys (max_leverage_*_live/demo, futures_margin_warning/force_close_ratio, min_liquidation_buffer_pct) and 7 market_data toggles. Telling the user these must be hand-edited in config.yaml is false AND risky (hand-editing bypasses the UI's range validation).
- **Document expectation**: UI-honesty top rule + the matrix's own header promise (it flags keys with "no dedicated control above").
- **Code evidence**: `frontend/src/pages/Settings.tsx:2817-2872` (`controlled` set omits RISK_FUTURES_LIVE/RISK_FUTURES_DEMO/RISK_MARGIN + 7 market_data keys), render badge at `:2903-2907`; the controls are proven editable at `:2600-2611` (risk) and `:1321-1338`/`:1245-1250` (market_data), accepted by routes `settings.py:692-759,961-987`.
- **Reproduction/verification**: reviewer + skeptic verified each key is absent from `controlled` yet rendered editable and accepted by the POST route.
- **Expected fix direction**: derive `controlled` from the same field arrays the cards render (so it can't drift), or add the missing arrays. **Severity: medium.**

### 164. Multiple Settings save mutations have no `onError`/feedback — saves fail silently

- **Domain**: frontend / Settings save error-handling.
- **Impact**: nine Intelligence-tab mutations (permission mode, bypass distress gate, guard auto-promote, operator/pi_daemon/serenity/regime toggles), the AppRows toggles, and the NetworkCard proxy save are `onSuccess`-only with no `onError` and no error message state; there's no global MutationCache onError either. A rejected save (bounds 400 / network failure) gives zero feedback and the server-bound control just never changes — a silent no-op (no false "saved" shown, so low). Contrast the number-grid cards which correctly set an error `SaveMsg`.
- **Document expectation**: UX house rule — fetches/saves need `onError` so operations never fail silently.
- **Code evidence**: `frontend/src/pages/Settings.tsx:321-374` (nine blocks, onSuccess-only), AppRows `:2732-2739`, NetworkCard `:3427-3435`; `main.tsx:18-34` has no global MutationCache onError. Good pattern at `TradingParamsCard:1879`/`RiskCard:2555`.
- **Reproduction/verification**: reviewer + skeptic read each block; confirmed no onError/error-state.
- **Expected fix direction**: add `onError` handlers (shared toast/inline error) mirroring the number-grid cards. **Severity: low.**

### 165. CommandCenter Pause/Resume/Wake + runtime lifecycle mutations swallow backend 409/404 — operator silently believes the action succeeded

- **Domain**: frontend / CommandCenter.
- **Impact**: the header Pause/Resume/Wake buttons and the 5 runtime lifecycle mutations have no `onError` and the UI reads only `isPending`. `resume`/`wake` route through `resume_gate.resume_if_llm_available`, which raises **409 when the LLM is unavailable** (a documented, common auto-pause scenario); runtime transitions raise 409/404. `client.ts` throws on non-2xx, and the rejection is silently discarded → the operator clicks Resume, nothing surfaces, the button re-enables, and the system **stays paused while the operator believes it resumed**. The SAME action in the global StatusBar (`App.tsx:194`) correctly surfaces the error via `extractResumeError` — the pattern exists but wasn't applied here.
- **Document expectation**: UX house rule (onError) + UI-honesty (never imply "resumed" not backed by the real result).
- **Code evidence**: `frontend/src/pages/CommandCenter.tsx:762-831` (pause/resume/wake + 5 runtime mutations, no onError); `server/routes/operator.py:155-161` (resume 409); `server/routes/runtime.py:34-38,116,144,160` (409/404). Contrast `App.tsx:191-195`.
- **Reproduction/verification**: reviewer + skeptic read all mutation blocks and traced the 409 path; confirmed no global MutationCache onError.
- **Expected fix direction**: add `onError` to these mutations surfacing the extracted API error (mirror `App.tsx` `extractResumeError`) so a 409 tells the operator the system is still paused. **Severity: medium.**

### 166. Runtime "Guidance" status color is dead — a runtime-state colorizer is fed guidance action strings, so promote-ready and rollback render identically yellow

- **Domain**: frontend / CommandCenter (cosmetic).
- **Impact**: the Guidance tone uses `runtimeBadge(guidance?.action)`, but `runtimeBadge` only recognizes runtime STATE strings while `guidance.action` is always one of observe/activate_restart/promote/start_shadow/drain/rollback/prepare_candidate — none match, so it always returns `badge-blue` → tone is unconditionally yellow. A positive "promote" and a negative "rollback" render the same; the color conveys nothing (text label stays correct). Dead color logic.
- **Document expectation**: color should map guidance-action severity to distinct tones.
- **Code evidence**: `frontend/src/pages/CommandCenter.tsx:378` (tone via `runtimeBadge(guidance?.action)`), `:219-234` (runtimeBadge keys off states); guidance actions at `runtime_continuity/service.py:406-446`.
- **Reproduction/verification**: reviewer + skeptic enumerated all 7 action strings; none match a state case → always yellow.
- **Expected fix direction**: a guidance-action→tone map (promote/start_shadow→green, rollback/activate_restart→red, else yellow), or drop the color. **Severity: low.**

### 167. Serenity **Evidence tab** displays the advisory proposed `tier`, not the authoritative `assessed_tier`

- **Domain**: frontend / SerenityLens. (Distinct render site from #101, which was the opportunity detail UI.)
- **Impact**: the standalone Evidence tab shows the generator's self-graded proposal as "Tier", not the tier that actually gates scoring/promotion. The deterministic evaluator can downgrade evidence, but the UI still shows the inflated proposal — inconsistent with the sibling `ThemeDetailPanel` which renders it correctly. Display-only (secondary tab), so low.
- **Document expectation**: `serenity_contract` D2 — `assessed_tier` is authoritative; `tier` is advisory (`storage/models.py:377,392-395`). UI-honesty: shown field must trace to the acted-upon value.
- **Code evidence**: `frontend/src/pages/SerenityLens.tsx:772` (`<td>{e.tier}</td>` in EvidenceTab); contrast `:657` (`humanize(e.assessed_tier || e.tier)` in ThemeDetailPanel). Route `data.py:357-363` returns `_select_all` rows so `assessed_tier` is present.
- **Reproduction/verification**: reviewer + skeptic read both render sites + the model comments + route.
- **Expected fix direction**: render `humanize(e.assessed_tier || e.tier)` at `:772` (match `:657`). **Severity: low.**

### 168. NewsIntel status filter has no bucket for pipeline-analyzed items (`status='analyzed'`)

- **Domain**: frontend / NewsIntel. (Distinct from #110, which is the manual-analyze persistence path.)
- **Impact**: the news pipeline produces `new` / `analyzed` (auto analyzer) / `reviewed` (manual UI), but the filter offers only all/new/reviewed. Auto-`analyzed` rows (which genuinely exist — the analyzer is wired as a tail step of the scheduled news_fetch job) match neither `new` nor `reviewed`, so they appear only under "All"; a user triaging via "New" misses them, and there's no way to list analyzed items. The badge also renders `analyzed` with the generic muted tone.
- **Document expectation**: UI filters should cover the real status vocabulary the backend produces.
- **Code evidence**: `frontend/src/pages/NewsIntel.tsx:73-78,88,154,177`; backend `data/news_analyzer.py:76,90` (sets `analyzed`), `server/routes/news.py:106` (manual→`reviewed`), `:145` (insert `new`); wired at `scheduler.py:457` → `news_collector.py:153-154`.
- **Reproduction/verification**: reviewer + skeptic confirmed the three-value vocabulary vs two-value filter and that `analyzed` rows are produced.
- **Expected fix direction**: add an `analyzed` filter bucket + badge tone (or fold analyzed+reviewed). **Severity: low.**

### 169. Charts PredictionsTab reads non-existent fields `m.question` / `m.volume_usd` — card question renders blank, volume silently dropped

- **Domain**: frontend / Charts (Markets page).
- **Impact**: every prediction-market card renders with an EMPTY question line and no volume. The backend `/api/prediction/markets` returns `event_title` + `volume_24h` (and the `PredictionMarket` type declares exactly those), but Charts reads `m.question` and `m.volume_usd`, which don't exist → React renders undefined as nothing and the `volume_usd != null` guard is always false. The sibling `PredictionLens` uses the correct fields, confirming Charts is wrong; `markets` is typed `any[]` so the compiler missed it.
- **Document expectation**: UI field must trace to a real backend field; the question is the card's primary label.
- **Code evidence**: `frontend/src/pages/Charts.tsx:323` (`m.question`), `:331-332` (`m.volume_usd`); backend `server/routes/prediction.py:110,113` (`event_title`/`volume_24h`); `frontend/src/types.ts:159,162`.
- **Reproduction/verification**: reviewer + skeptic read Charts + route + type; no legacy `question` alias exists.
- **Expected fix direction**: read `m.event_title` / `m.volume_24h`; restore `PredictionMarket` typing on `markets` so the compiler catches such drift. **Severity: medium.**

### 170. Charts feeds the universe board asset-class `"crypto"` into `fmtSymbol` as instrument market_type — perps render with a slash (symbol-display rule violated) and the per-snapshot fallback is dead

- **Domain**: frontend / Charts (Markets page). (Distinct from #115, which is the candlestick OHLCV market_type.)
- **Impact**: on the Markets page the symbol strip, chart dropdown, and Open-Opportunities header render EVERY symbol in spot (slashed) form even for perps, violating the immutable perp⇒no-slash rule. `boardMarketType` comes from `universe.snapshot.market_type`, which holds the asset-class `cfg.universe.market_type` (default `"crypto"`, and it's the very filter the snapshot is queried by), never an instrument type. `isPerpMarket("crypto")` is false so the slash is kept; worse, the intended per-instrument fallback `marketType ?? snap?.market_type` is dead because `"crypto"` is truthy and shadows the real `snap.market_type`. (Dead only while an active snapshot exists — the normal state.)
- **Document expectation**: `fmtSymbol` must receive the row's instrument market_type, not the board asset class (immutable owner rule).
- **Code evidence**: `frontend/src/pages/Charts.tsx:367` (`boardMarketType = snapshot.market_type`), passed to SymbolStrip `:453`/`:52` and ChartPanel `:464`/`:80,146,161`; board value forced to `cfg.universe.market_type` at `universe.py:21`→`repositories.py:1408` (default `schema.py:609`); `isPerpMarket` excludes `"crypto"` (`format.ts:228-237`); real per-instrument value at `market_data.py:97-99`.
- **Reproduction/verification**: reviewer + skeptic traced the full data flow; confirmed the snapshot market_type is always the asset class and the fallback is shadowed.
- **Expected fix direction**: use each snapshot's per-instrument `snap.market_type` first (`snap?.market_type ?? boardMarketType`); treat the board value as a hint only when it's an actual instrument type. **Severity: medium.**

### 171. Overview "Exchange" connectivity light always probes Bitget, ignoring the configured exchange (OKX/Binance shown wrong)

- **Domain**: frontend / Overview (exchange-agnostic + UI honesty).
- **Impact**: `cfg.exchange.name` selects the venue (bitget/okx/binance; OKX is heavily used) while `cfg.exchange.mode` is only demo/live. Overview unconditionally calls `api.testBitget(mode)` → `POST /bitget/test-connection`, probing Bitget with Bitget creds. On an OKX deployment: absent Bitget creds → a healthy OKX shows a RED "down" light (false alarm); present Bitget demo creds → GREEN based on Bitget while the real OKX venue could be down (false reassurance). Dedicated `/okx/test-connection` and `/binance/test-connection` routes exist but are unused here. Also re-probes the wrong venue every 15s.
- **Document expectation**: UI honesty (indicator must trace to the configured exchange's real state) + exchange-agnostic principle.
- **Code evidence**: `frontend/src/pages/Overview.tsx:29-35,172-176` (`api.testBitget`), `client.ts:346-347`→`routes/bitget.py:74-80` (BitgetClient); `config/schema.py:64-66` (name=venue, mode=demo/live); dedicated routes `bitget.py:312` (/okx), `:190` (/binance).
- **Reproduction/verification**: reviewer + skeptic traced Overview→client→route→BitgetClient; confirmed the okx/binance test routes exist and are unused.
- **Expected fix direction**: select the test endpoint by `cfg.exchange.name` (or add a venue-agnostic `/exchange/test-connection` that dispatches on the configured exchange). **Severity: medium.**

### 172. RiskControls Active Risk shows green "operating within all limits" when risk status is missing/unknown

- **Domain**: frontend / RiskControls (UI honesty).
- **Impact**: when `/api/risk/status` hasn't resolved or errors (no onError; a fresh system returns `status="not_evaluated"` with `violations=[]`), `riskState` defaults to `unknown`/`not_evaluated` but the card still renders the green clear-state "No active violations — operating within all limits" (gated only on `violations.length===0`). The header badge honestly shows "Unknown"/"Not evaluated", so the surfaces contradict — the green affirmative body is the honesty gap. (Overview's equivalent avoids the affirmative clause.)
- **Document expectation**: UI-honesty top rule — no reassuring "within limits" not backed by a real value.
- **Code evidence**: `frontend/src/pages/RiskControls.tsx:82-83,233-238`; backend `risk.py:28-71` (status `not_evaluated` unless a latest_decision exists). Contrast `Overview.tsx:151-155`.
- **Reproduction/verification**: reviewer + skeptic confirmed the clear-state text is unconditional on empty violations, independent of `riskState`.
- **Expected fix direction**: gate the affirmative clause on `riskState==='healthy'`; show "Risk not evaluated / not available" otherwise; add `onError`. **Severity: low.**

### 173. ActivityTimeline renders time-only timestamps (HH:MM) with no date, so a stale feed looks current

- **Domain**: frontend / ActivityTimeline component.
- **Impact**: `formatTime()` emits only hour:minute. The feed mixes agent runs + audit events sorted desc; if the system has been idle, the newest items are hours/days old but render identically to recent ones — no date, no relative age, so a stale feed is visually indistinguishable from a live one. (Sorting uses the full timestamp correctly; only display drops the date. The ISO parse itself is correct.)
- **Document expectation**: UI honesty — a time field shouldn't imply recency it can't back; `lib/format.ts fmtTimestamp` already produces "MMM D, HH:mm".
- **Code evidence**: `frontend/src/components/ActivityTimeline.tsx:53-58` (time-only `formatTime`), rendered `:122`; sort `:90` uses full ts.
- **Reproduction/verification**: reviewer + skeptic confirmed display-only (toUtc guard correct); compared to `fmtTimestamp`.
- **Expected fix direction**: use `fmtTimestamp()` (or add the date when not today); drop the duplicate local formatter for the shared helper. **Severity: low.**

### 174. Topbar master system-state can never reflect the risk engine — `riskState` is always empty, so CAUTION/OPERATIONAL are dead branches

- **Domain**: frontend / App topbar (UI honesty). (Distinct from #47 RiskControls exposure map and #118 scheduler.)
- **Impact**: the topbar `deriveMasterState({riskState,...})` reads `riskState = String(data?.risk_status || data?.risk?.status || '')` where `data` is the `/api/status` payload — but that handler returns NO risk field and `SystemStatus` declares none, and StatusBar never queries risk-status (contradicting its own comment claiming it does). So `riskState` is ALWAYS `''`: the `warning`(CAUTION/yellow) and `healthy`(OPERATIONAL/green) branches are dead, and in normal healthy operation the ladder falls through to STANDBY/blue "Risk state has not been evaluated yet" — misleading, because risk IS evaluated elsewhere. The RiskControls console feeds the same `deriveMasterState` with the real status, so the topbar diverges from the risk console (breaking the "same signal, same color" single-source contract). (Verify correction: the `blocked`→BLOCKED branch is harmlessly shadowed by the higher-priority kill-switch→HALTED, since risk `status='blocked'` only when kill_switch_active — so the real gaps are dead CAUTION/OPERATIONAL + the false "not evaluated" + the false comment, not BLOCKED.)
- **Document expectation**: `systemState.ts` header documents the risk→state ladder; UI-honesty forbids a neutral state not backed by a real value.
- **Code evidence**: `frontend/src/App.tsx:241` (riskState from `/status`), `:116-121` (no risk query), `:236-238` (false comment), `:248` (stateWord only uses operatorMode when green); `server/routes/health.py:227-248` (no risk field); `types.ts:360-381`; `lib/systemState.ts:49-67`. Contrast `RiskControls.tsx:83-86` feeding the real status.
- **Reproduction/verification**: reviewer + skeptic confirmed `/status` has no risk field and StatusBar has no risk query; corrected the BLOCKED-branch overstatement.
- **Expected fix direction**: add a `getRiskStatus` query to StatusBar and pass `.status` into `deriveMasterState` (mirror `RiskControls.tsx:83-86`), or add `risk.status` to `/api/status`. **Severity: medium.**

### 175. `SymbolDirectionMatrix` renders symbols via `.replace('/USDT','')` instead of `fmtSymbol` — drops the spot/perp distinction

- **Domain**: frontend / TradeInsightsPanel. (Same component family as #96, distinct symbol-display defect.)
- **Impact**: the Symbol×Direction matrix renders `row.symbol.replace('/USDT','')` (bare base, e.g. "BTC"), bypassing the immutable `fmtSymbol` rule — neither spot (BTC/USDT) nor perp (BTCUSDT) form, quote currency lost. The backing `SymbolDirectionEntry` type and the backend `_symbol_direction_matrix` also drop `market_type`, so full compliance needs the backend to include it.
- **Document expectation**: owner immutable rule — every symbol render must go through `fmtSymbol(symbol, marketType)`; analytics tables are not exempt.
- **Code evidence**: `frontend/src/charts/TradeInsightsPanel.tsx:449` (`.replace('/USDT','')`); contrast `:312` (correct `fmtSymbol(d.symbol, d.market_type)`); type `api/client.ts:980-986` (no market_type); backend `analytics/trade_insights.py:384-411` (keys by symbol+direction, drops market_type though the query selects it).
- **Reproduction/verification**: reviewer + skeptic read the render site + type + backend matrix.
- **Expected fix direction**: thread `market_type` into the matrix rows and render `fmtSymbol(row.symbol, row.market_type)`; stopgap `fmtSymbol(row.symbol)` instead of `.replace`. **Severity: low.**

### 176. `knowledge_contract.md` §3.4 + §7.2 still document `post_trade_review` as a retrieved lesson type — contradicting the anti-hallucination invariant the code enforces

- **Domain**: backend / knowledge contract drift. (Safety-relevant doc drift.)
- **Impact**: the L2 normative contract misrepresents retrieval item_types in two places, re-introducing the exact hallucination source that was removed. §7.2 says the ranker retrieves `["trade_case_study","post_trade_review"]` and §3.4 lists `post_trade_review` in three role item_types — but `paper_trader_ranker` retrieves ONLY `["trade_case_study"]` (post_trade_review deliberately removed to stop the ~1900-row templated-review flood), and `router.py` excludes `post_trade_review` from EVERY role (its comment calls re-adding it a regression, test-locked). A maintainer following the contract to re-wire retrieval would reinstate the flood the fix eliminated.
- **Document expectation**: §7.2/§3.4 should reflect that `post_trade_review` is excluded from all router roles and from the ranker's lesson retrieval (`trade_case_study` is the sole trustworthy lesson source).
- **Code evidence**: `docs/design/knowledge_contract.md:291` + `:140-142`; vs `agents/paper_trader_ranker.py:398-405` (trade_case_study only, comment `:393-397`); `knowledge/router.py:31-38` (no post_trade_review), comment `:22-30` + `test_no_role_retrieves_post_trade_review`.
- **Reproduction/verification**: reviewer + skeptic read all four anchors; confirmed the doc contradicts the code-enforced, test-locked invariant.
- **Expected fix direction**: update §7.2:291 to `["trade_case_study"]` and strike `post_trade_review` from §3.4:140-142. **Severity: medium.**

### 177. Knowledge router `_ITEM_TYPES_BY_ROLE` has 3 unreachable role entries; `equity_observer` runs but retrieves no knowledge

- **Domain**: backend / knowledge router reachability.
- **Impact**: `router.py` maps 4 roles (strategy_designer, market_observer, equity_observer, paper_trader), but `build_advisory_context`/`retrieve_for_role` has exactly ONE live caller (`market_observer.py:572`). `strategy_designer` is legacy (rules engine removed), `paper_trader` consumes via the ranker's direct `retrieve_lessons` (not the router), and `equity_observer` has zero retrieval calls despite being a live scheduled agent and despite §3.4 documenting its role mapping — so the scheduled equity_observer always runs un-augmented. Dead config + a documented-but-unwired consumer path.
- **Document expectation**: §3.4 implies each listed role's mapping drives live retrieval; only market_observer does.
- **Code evidence**: `knowledge/router.py:31-38`; grep shows sole caller `market_observer.py:572`; `equity_observer.py` has no retrieval; scheduled at `scheduler.py:467-469`; `knowledge_contract.md §3.4:140-142`.
- **Reproduction/verification**: reviewer + skeptic grepped callers and read equity_observer + scheduler.
- **Expected fix direction**: either wire `equity_observer` to `build_advisory_context`, or prune the dead role entries and correct §3.4 (also drop the stale post_trade_review refs — see #176). **Severity: low.**

### 178. `config_consistency` `info`-severity finding is escalated to a doctor BLOCKER (false-critical verdict)

- **Domain**: backend / doctor probe honesty. (Distinct from #148, which is the hint text — this is the severity-mapping bug.)
- **Impact**: `_probe_config_consistency` maps severity with `"warn" if f.severity=="warning" else "blocker"`, but `config_consistency` emits three severities — the benign/intentional "data source differs from exchange identity" case returns `severity="info"`. `"info" != "warning"` → it falls to the else and is reported as a doctor **BLOCKER**, flipping `healthy` to False, adding to `summary.blockers`, and driving `aletheia doctor` exit code to 1 — surfacing an explicitly info-level note as a critical blocker to both the CLI and the Operator (`GET /_infra/doctor`). Latent (needs a non-internal execution mode + mismatched data/exchange identity) but reachable with valid config.
- **Document expectation**: probe-honesty convention — info states report INFO, blocker only for real current defects.
- **Code evidence**: `diagnostics/doctor.py:1821-1823` (two-way map); `diagnostics/config_consistency.py:100-105` (returns `severity="info"`); Severity Literal includes `"info"` (`doctor.py:37`) so a three-way fix is valid.
- **Reproduction/verification**: reviewer + skeptic read both files + the blocker/healthy/exit-code chain.
- **Expected fix direction**: map severity explicitly three-way (`info`→info, `warning`→warn, `blocker`→blocker). **Severity: medium.**

### 179. `audit_chain`: chain breaks with no parseable timestamp are always classified as historical INFO, hiding possible post-fix corruption

- **Domain**: backend / doctor probe honesty (trust boundary).
- **Impact**: `_probe_audit_chain` splits chain-break errors into historical vs post-fix by timestamp vs `EFFECTIVE_CHAIN_FIX_TS`, but `verify_audit_chain` appends `error_timestamps=""` for two break classes (invalid-JSON lines, and `_prev_hash` mismatches whose record lacks created_at/timestamp). `_parse_iso_datetime("")` returns None → those breaks are excluded from the post-fix set → if the only breaks are undateable (e.g. a corrupt/tampered invalid-JSON record written today), the probe reports INFO "N historical chain break(s) … zero post-lock breaks" while `verify`'s own `valid` is already False and `healthy` stays True. (Verify: the classic field-edit tamper yields a dated mismatch and is correctly warned; only invalid-JSON/corruption artifacts are masked → low, but still a probe-honesty gap on a trust boundary.)
- **Document expectation**: `audit_chain_contract §6.2` — a broken chain (valid=False) is a tamper/corruption signal; probe-honesty forbids INFO masking a real current defect.
- **Code evidence**: `diagnostics/doctor.py:1793-1805` (post_fix filter excludes empty-ts), `:326-327` (`_parse_iso_datetime("")`→None), `:74` (INFO leaves healthy True); `files/jsonl.py:328` (`""` for invalid JSON), `:358-359` (rec_ts defaults `""`), `:365` (valid=False).
- **Reproduction/verification**: reviewer + skeptic confirmed errors/error_timestamps are parallel and empty-ts breaks are excluded; steady state already sits in the INFO branch.
- **Expected fix direction**: treat empty/unparseable-timestamp breaks conservatively as post-fix (WARN); downgrade to INFO only when provably older than `EFFECTIVE_CHAIN_FIX_TS`. **Severity: low.**

### 180. `aletheia inspect config` reports the trading agent's `permission_mode` from `cfg.operator.permission_mode` (wrong field/namespace)

- **Domain**: cli / inspect_cmd read-only diagnostic honesty.
- **Impact**: `inspect config` (used by AI agents + humans via `--json` as a state-of-truth probe) emits `cfg.operator.permission_mode` (operator-shell namespace: ReadOnly/PaperAuto/Auto) under the `"agent"` section, while the actual trading-agent gate is `cfg.agent.permission_mode` (Observe/Paper/Full) — what `orchestrator.py:20` reads to gate every trading agent. On a default config it prints `agent.permission_mode="ReadOnly"` while trading agents are actually in "Paper" (false-restrictive); after a live unlock (`live.py` sets `operator.permission_mode="Auto"`, leaving `agent.*` untouched) it prints `agent.permission_mode="Auto"`, which isn't even a valid trading-agent mode. The two fields are written independently and never synced.
- **Document expectation**: a shown field must trace to the real backend field; the enforced trading permission is `cfg.agent.permission_mode`.
- **Code evidence**: `cli/inspect_cmd.py:366-370` (permission_mode reads `cfg.operator.*` while sibling fields correctly read `cfg.agent.*`); source of truth `agent_runtime/orchestrator.py:20`; defaults `config/schema.py:183` (agent=Paper) vs `:196` (operator=ReadOnly); independent writers `permission.py:32`/`settings.py:662` (agent) vs `live.py:54/79` (operator).
- **Reproduction/verification**: reviewer + skeptic read all readers/writers + ran `uv run python -c` confirming defaults diverge (Paper vs ReadOnly).
- **Expected fix direction**: change `inspect_cmd.py:367` to `cfg.agent.permission_mode`; surface the operator-shell mode as a separate `operator.permission_mode` key if useful. **Severity: medium.**

## Investigated and refuted (NOT recorded as defects — do not re-report)

- **Self-update: "post-merge auto-restart hook restarts the gateway onto unverified code before the smoke-gate/rollback can run."** Refuted. `server/routes/update.py:32-42 update_apply` is `async def` calling `applier.apply_update()` directly on the single uvicorn event loop (no threadpool; single worker). All of `apply_update`'s blocking work (git merge, ≤180 s pytest smoke, `reset_hard`) blocks that loop, so the hook's backgrounded 3 s curl to `/api/gateway/restart` cannot be serviced during the smoke window — the restart never fires mid-smoke, and if it fired it would be after `apply_update` already smoke-passed or rolled back. The "never bricks / never auto-restarts" invariant HOLDS on the Web-UI/API path. Only vulnerable path is the explicitly-deprecated `aletheia update apply` CLI (separate process, idle loop). **Latent-risk note**: if `apply_update` is ever wrapped in `run_in_executor` to unblock the loop, the API path would become vulnerable to this race — re-check then.
- **Low-level clients: "OKX `contractSize` lookup fails open to 1.0, oversizing orders."** Refuted. The `except → 1.0` handler (`okx/client.py:143-145`) fires only when `load_markets()` throws (markets empty); control returns to `create_order`, whose `self.market(symbol)` then raises `BadSymbol` (markets not loaded) → caught at `:209` and re-raised as `RuntimeError`, so the order ABORTS before any `amount=Q` is sent. The distinct branch at `:142` (markets loaded but `contractSize` falsy) was not the cited path and, for a loaded OKX swap market, ccxt populates `contractSize`. Not a reachable oversizing path.
- **Decision chain: "`flip` action opens the opposite-direction position but never closes the existing one" (double-exposure).** Refuted. Risk rule 6b (`risk/engine.py:162-173`) appends `symbol_already_open` for ANY open position on the same symbol in the same demo/live pool regardless of direction, and a flip by definition targets an already-open symbol → the proposal is always rejected before any fill; the paper_broker merge path never runs. `flip` is a rejected no-op, not double exposure. Residual (low, noted not recorded): `flip` is advertised in the prompt (`trading_decision.py:1062`) yet is structurally always rejected — a non-functional advertised action.
- **Operator: "`build_recommendations` can never flag a dead/stale operator shell."** Refuted. A Python supervisor `OperatorShellMonitor` (`gateway/operator_shell.py:553`, started in `_gateway_runner.py:345-347`) polls the child every 1s; on exit it writes a FRESH heartbeat with `status='offline'`, so `build_recommendations` (`operator_recommendations.py:894`) DOES emit the operator-offline recommendation. The hang case deliberately keeps `status='online'` with a "supervisor keeping status fresh" message (documented design, `App.tsx:359`). Only accurate sub-claim: the `elif heartbeat.get('stale')` branch (`:903`) is dead on the store read path — harmless redundant code, not a defect.
- **Agents: "`risk_critic` promotes every risk-lesson note to high_confidence whenever any proposal/decision exists."** Real mechanically (`risk_critic.py:59` promotes on `proposals or decisions`) but INTENDED: the in-code comment states the "real activity to critique" intent, impact is self-limited (one note per 6h run, no flooding analog to the post_trade_reviewer 118:1 case), content-hash dedup bounds accumulation, and no contract mandates `risk_lesson` stay medium_confidence. Design opinion, not a defect. (The `if rejected` refinement is a nice-to-have, not recorded.)
- **Data collectors: "Coinalyze OI USD field parsed by guessed key names with a coin-count fallback stored into the `*_usd` field."** Refuted AS WRITTEN, but the real adjacent concern is **folded into #156** rather than dropped: the verifier checked the live Coinalyze `/open-interest` OpenAPI and reports the actual field is `value` (not the guessed `open_interest_usd`/`open_interest` keys), so every row is skipped (`:157-158`) → the OI collector is a dead feed and the coin-as-USD line (`:159`) is never reached (no magnitude corruption). Because that flips the impact (dead feed, not corruption) and interacts with the kept pollution finding, #156 records both risks with "verify the live schema first" as the action.
- **Frontend: "Serenity evidence symbol renders pass a non-existent market_type, forcing spot/slash rendering."** Refuted (real mechanically, intended). `SerenityEvidence` has no `market_type` field so `fmtSymbol(sym, undefined)` is byte-identical to `fmtSymbol(sym)`, and the slashed canonical form is the correct display for serenity research/news tickers, which are explicitly EXEMPT from the perp/spot slash convention. Harmless dead-argument style nit, no rendered difference.
- **Runtime continuity: "`enabled` flag does not guard WRITE transitions — a disabled feature can still swap the production gateway's boot code via `/promote`."** Refuted (real facts, intended). `PI_RUNTIME_CONTINUITY_SOP.md:27` documents the flag's scope precisely as "enables real shadow gateway launch from API/CLI" — the code matches; the flag is never documented as gating the whole write control plane. Impact is bounded (localhost operator API; `/continuity` materializes only from the configured git remote — no arbitrary-code injection; the live-traffic frontdoor switch is inert when disabled). A defense-in-depth suggestion at most (low), not a violation of the documented flag contract.
- **Runtime continuity: "`validate_shadow` reports passed=True / emits `shadow_validated` when NO shadow gateway was launched."** Refuted (real in disabled mode, intended). In both production entry points the launcher is wired iff `runtime_continuity_enabled` is True, and `spawn_shadow_gateway` always returns `attempted=True`, so whenever the feature is enabled an unready gateway makes validation FAIL (test-locked). The `passed=True`-with-no-attempt outcome is reachable only in disabled mode (launcher=None) — graceful structural degradation, and the report is self-honest (`detail="not_attempted"`, `status` surfaces `enabled`). Not a production fake-green.

## Verification Commands Run During Sweep

- `uv run pytest tests/specs/ -q -r xX --tb=short`
- `uv run pytest tests/specs/spec_live_unlock.py tests/specs/spec_api_reference.py tests/specs/spec_kill_conditions.py -q --tb=short`
- `uv run pytest tests/specs/spec_execution.py tests/specs/spec_pipeline_execution_chain.py tests/specs/spec_pipeline_entry_enrichment.py -q --tb=short`
- `uv run pytest tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/specs/spec_operator_loop.py tests/specs/spec_operator_contract_parity.py tests/specs/spec_operator_single_source.py tests/specs/spec_pipeline_operator_loop.py -q --tb=short`
- `uv run pytest tests/specs/spec_serenity_xstock_execution.py tests/specs/spec_equity_observer.py tests/test_serenity_p0_p3_e2e.py tests/test_xstocks_observer.py -q --tb=short`
- `uv run pytest tests/test_frontend_navigation_ia.py tests/test_event_triggers.py tests/specs/spec_pipeline_operator_loop.py -q --tb=short -r xX`
- `uv run pytest tests/test_secrets.py tests/specs/spec_api_reference.py -q --tb=short -r xX`
- `uv run pytest tests/test_config_routes.py -q --tb=short -r xX`
- `uv run python -` temporary reproduction: backup archive omits `logs/audit.jsonl`.
- `uv run python -` temporary reproduction: full export includes nested `artifacts/evidence/nested/.env`.
- `uv run python -` temporary reproduction: import dry-run accepts a bundle whose checksum references a missing file.
- `uv run python -` temporary reproduction: `proposal-pack` for `prop_a` exports both `prop_a` and `prop_b`.
- `uv run python -` temporary reproduction: `pause_agents` executes without a confirmation token while returning `needs_confirmation=True`.
- `uv run python -` SDD audit: compared `tests/specs/spec_*.py` with `tests/test_spec_conformance.py` imports; found two missing files.
- `uv run pytest tests/test_spec_conformance.py -q --tb=short` passed while registry still reported missing/partial/superseded claims.
- `uv run python -` API route/doc audit: extracted FastAPI route decorators, normalized `/api` prefixes and path parameters, and compared them with `docs/design/api_reference.md`; found 21 actual routes not individually documented and 5 documented routes without actual backend handlers.
- `rg -n "self\\.enabled|enabled" aletheia/runtime_continuity/service.py aletheia/server/routes/runtime.py tests/test_runtime_continuity.py -S` showed runtime continuity's enabled flag is surfaced but not enforced on write transitions.
- Manual read of `aletheia/model_provider/openai_oauth.py`, `claude_oauth.py`, and `oauth.py` found no token exposure in status payloads, but found unescaped OAuth callback error HTML.
- `uv run python -` temporary reproduction: `news.analyze_news()` returns a tuple in `analysis` when `llm_complete()` returns `(content, error)`.
- `rg -n "prediction/preferences|updatePredictionPreferences" ...` confirmed frontend POST vs backend/doc PATCH mismatch for prediction preferences.
- `rg -n "run_migrations\\(\\)" aletheia/server/routes -S` confirmed `capital.py` still runs migrations per request while nearby route modules avoid it.
- `uv run python -` scheduler audit: AST-extracted 33 registered `add_job(... id=...)` entries and compared them to `agent_contracts.md` / `SYSTEM_MAP.md`.
- Manual read of `scheduler.py::_run_tpsl_fill_check` confirmed it uses plain `asyncio.run()` around Bitget calls and has no scheduler timeout key.
- `rg -n "CCXTProvider\\(|get_market_data_provider|Deliberate exception|only direct" ...` confirmed Universe registry/features directly construct `CCXTProvider`, while the documented direct-provider exception is limited to xStocks.
- Manual read of `agent_shell.commands.set_symbol_observe_only`, `risk.engine`, `settings.py`, and `trading.instruments` found the symbol-filter list is normalized by consumers, so `BTCUSDT` storage in that command is not recorded as a bug.
- Manual read of `macro.py`, `scheduler.py`, `yfinance_provider.py`, and macro tests found `MacroCollector.collect()` directly calls synchronous yfinance fallback inside the async data-collector tick.
- Manual read of `learning/lessons.py`, `agents/lesson_reviewer.py`, `risk/engine.py::_check_lesson_constraints`, `trading_context.py`, `trading_decision.py`, and `spec_lesson_guards.py` found applied ps_lessons are consumed, but hard-block lesson checks do not enforce the documented risk-category boundary.
- `rg -n "promote_strategy\\(|create_strategy\\(|check_promotion_gates\\(|check_live_limited_gates\\(" aletheia tests frontend/src -S` found the old strategy FSM functions only inside `strategy_lifecycle.py`, while docs mark strategy tables read-only; `operator_recommendations.py` still references deleted `strategy_designer`.
- Manual read of `frontend/src/api/client.ts`, `aletheia/server/routes/data.py`, and `tests/test_frontend_navigation_ia.py` found stale `setStrategyVenue()` frontend API client code for a deleted backend endpoint, while the IA regression test only scans `.tsx` files.
- Manual read of `aletheia/analytics/trade_insights.py`, `frontend/src/api/client.ts`, and `frontend/src/charts/TradeInsightsPanel.tsx` found the symbol-direction analytics matrix groups by symbol+direction without `market_type`, returns no `market_type`, and renders the row by stripping `/USDT`.
- Manual read of `discussion.md`, `frontend/src/pages/Settings.tsx`, `frontend/src/api/client.ts`, `aletheia/server/routes/settings.py`, and `aletheia/config/schema.py` found the System Operator visible label has partly moved, but active config/API/audit contracts still use `pi_daemon`/`PI daemon`.
- Manual read of `discussion.md`, `storage/models.py`, `risk/engine.py`, `agent_shell/trading_decision.py`, and `spec_pi_decision_chain.py`, plus a temporary `uv run python -` reproduction with risk-decision persistence monkeypatched out, found wrong-side stop/TP geometry passes the native limit entry-plan gate.
- Manual read of `docs/design/learning_contract.md`, `agents/dreaming_agent.py`, `agents/lesson_reviewer.py`, `storage/migrations.py`, `storage/repositories.py`, and `tests/specs/spec_learning_pipeline.py` found `block_scanner` lessons can be promoted using only global/symbol evidence, even though scanner-family outcome data exists.
- Manual read of `docs/design/serenity_contract.md`, `aletheia/trading/serenity_evidence.py`, `aletheia/agents/research_worker.py`, `aletheia/server/routes/data.py`, `tests/test_serenity_routes.py`, and `tests/specs/spec_serenity_evidence.py`, plus a temporary `uv run python -` scoring reproduction, found manual Serenity opportunity creation can turn a submitted self/proposed `evidence_tier` into authoritative `assessed_tier` without evidence-derived assessment.
- Manual read of `frontend/src/api/client.ts`, `frontend/src/pages/SerenityLens.tsx`, and `aletheia/server/routes/data.py` found the Serenity opportunity detail UI types and renders proposed `evidence_tier` as "Tier" instead of the authoritative `assessed_tier`.
- Manual read of `docs/design/serenity_contract.md`, `spec_serenity_xstock_execution.py`, `storage/models.py`, `research_worker.py`, `trading_context.py`, `trading_decision.py`, and Serenity promote code, plus two temporary `uv run python -` reproductions, found Serenity `market_type` is not copied into `TradingDecision` for either Layer 3 fallback or promoted pending-signal direct matches.
- Manual read of `portfolio/pnl.py`, `tests/test_serenity_p0_p3_e2e.py`, and `spec_serenity_holding.py`, plus a temporary `uv run python -` reproduction with a 48-hour-old Serenity position, found automatic Serenity PostMortems compute `hold_h` from the pre-close row and record `hold_h=0`.
- Manual read of `docs/plans/serenity_execution_path.md`, `docs/design/serenity_contract.md`, `storage/models.py`, `storage/migrations.py`, `portfolio/position_manager.py`, and Serenity thesis-invalidation tests found the implemented thesis exit only supports `status != active`, while the reconciled plan also promises an explicit `thesis_invalidated` marker.
- Manual read of `docs/design/execution_contract.md`, `docs/design/agent_contracts.md`, `portfolio/position_manager.py`, and `tests/specs/spec_pipeline_position_manager.py`, plus `rg` for RuleSet exit symbols, found the execution contract still lists deleted RuleSet auto-exit as an active trigger while the current spec forbids it.
- Manual read of `agent_shell/commands.py`, `agent_shell/trading_decision.py`, `storage/repositories.py`, and anti-hallucination tests found `review_portfolio_state()` closes the first open position matching only `target_symbol`, with no position-id/market-type/direction disambiguation.
- Manual read of `frontend/src/pages/AgentActivity.tsx`, `frontend/src/api/client.ts`, `aletheia/server/routes/data.py`, `agent_runtime/orchestrator.py`, `agents/__init__.py`, `agents/stock_observer.py`, `agents/equity_observer.py`, and `docs/design/agent_contracts.md`, plus `Test-Path` checks for deleted strategy-agent modules, found Agent Activity exposes stale manual-run buttons and omits the current `equity_observer`.
- Manual read of `frontend/src/pages/Learning.tsx`, `frontend/src/api/client.ts`, `frontend/src/types.ts`, `aletheia/server/routes/data.py`, `storage/repositories.py`, `docs/design/agent_contracts.md`, and `docs/design/data_model.md` found the Learning page still surfaces legacy `strategy_records` lifecycle state as active paper-strategy progress.
- Manual read of `docs/design/execution_contract.md`, `aletheia/execution/gateway.py`, `aletheia/execution/bitget_broker.py`, `aletheia/execution/binance_broker.py`, `aletheia/execution/okx_broker.py`, `tests/test_client_partial_fill.py`, `tests/test_exchange_close_confirmation.py`, and `tests/test_bitget_broker.py` found exchange entry partial fills are recorded as completed orders/proposals, while close partial fills are held as partial.
- Manual read of `frontend/src/pages/NewsIntel.tsx`, `aletheia/server/routes/news.py`, `aletheia/data/news_analyzer.py`, `aletheia/agents/research_worker.py`, `storage/migrations.py`, and `docs/design/data_utilization.md`, plus repository search for news-analysis tests, found the NewsIntel manual analysis path does not persist `impact_summary`/`analyzed_at` or `status='analyzed'`, so research context ignores it.
- Manual read of `frontend/src/pages/PredictionLens.tsx`, `frontend/src/types.ts`, `aletheia/server/routes/prediction.py`, `aletheia/agents/prediction_observer.py`, `storage/repositories.py`, `docs/design/PREDICTION_MARKET_DESIGN.md`, and `tests/specs/spec_subsystem_observers.py` found PredictionLens market cards expose external Polymarket IDs while local history/claims/Serenity joins expect internal `prediction_markets.id`.
- Manual read of `frontend/src/pages/Proposals.tsx`, `frontend/src/api/client.ts`, `aletheia/server/routes/data.py`, `tests/test_workflow_routes.py`, `docs/design/api_reference.md`, and `docs/design/execution_contract.md` found proposal rejection lacks executed-state guards, approval business failures can be treated as frontend success, and approval confirmation can imply non-paper execution while the backend forces paper.
- `uv run python -` frontend/API route inventory: extracted 186 path literals from `frontend/src/api/client.ts` and 282 FastAPI route decorators from `aletheia/server/routes/*.py`; used it to avoid duplicating already-recorded stale strategy venue and prediction-preferences findings.
- Manual read of `frontend/src/pages/CommandCenter.tsx`, `frontend/src/api/client.ts`, `aletheia/server/routes/operator.py`, `aletheia/agent_shell/operator_store.py`, and operator route tests found Command Center confirmation/session issues were already covered by earlier findings; no new Command Center issue recorded in this pass.
- Manual read of `frontend/src/pages/Charts.tsx`, `frontend/src/charts/PriceChart.tsx`, `frontend/src/api/client.ts`, and `aletheia/server/routes/data.py` found the Markets candlestick chart drops known market type and therefore defaults futures/perp OHLCV requests to spot.
- Manual read of `frontend/src/pages/TradesPositions.tsx`, `frontend/src/pages/Charts.tsx`, `aletheia/server/routes/data.py`, and `aletheia/portfolio/position_reconciler.py` found the filled-order chart jump loses order context and the reconciliation card hides stale/unavailable sync status when there are no drift rows.
- Manual read of `frontend/src/pages/Overview.tsx`, `frontend/src/pages/RiskControls.tsx`, `frontend/src/App.tsx`, `frontend/src/lib/systemState.ts`, `aletheia/server/routes/health.py`, `aletheia/agent_runtime/scheduler.py`, and `tests/test_status_routes.py` found Overview/RiskControls ignore the actual scheduler-running fields that the backend exposes.
- Manual read of `aletheia/analytics/go_live_scorecard.py`, `aletheia/model_provider/fallback.py`, `frontend/src/pages/Overview.tsx`, and scorecard/live-unlock tests, plus a temporary `uv run python -` reproduction, found the Go-Live scorecard model-health criterion treats a list snapshot as a dict and always falls back to `cannot verify`.
- Manual read of `frontend/src/components/AgentFeed.tsx`, `frontend/src/types.ts`, `aletheia/agent_runtime/orchestrator.py`, `aletheia/agent_runtime/scheduler.py`, `aletheia/agent_runtime/run_logger.py`, and `aletheia/agent_runtime/health.py` found the Overview activity feed renders backend failure statuses other than literal `error` as yellow/non-error UI states.
- Manual read of `frontend/src/components/SetupBanner.tsx`, `frontend/src/api/client.ts`, `frontend/src/pages/Settings.tsx`, `aletheia/server/routes/model.py`, `aletheia/server/routes/bitget.py`, `aletheia/server/routes/settings.py`, API docs, and OAuth/Codex tests found the first-screen setup banner still assumes API-key model providers and Bitget demo credentials are the only valid starting setup.
- Manual read of `frontend/src/components/CausalChain.tsx`, `frontend/src/pages/TradesPositions.tsx`, `frontend/src/api/client.ts`, `frontend/src/types.ts`, `aletheia/storage/models.py`, `aletheia/storage/repositories.py`, `aletheia/server/routes/data.py`, execution/web UI docs, and causal-chain tests found the interactive causal-chain inspector derives Fill facts from order fields and always marks order-anchor Position nodes missing despite existing position `order_id` backlinks.
- Manual read of `frontend/src/components/PnLZone.tsx`, `frontend/src/components/AccountSnapshot.tsx`, `frontend/src/charts/PnLChart.tsx`, `frontend/src/api/client.ts`, `aletheia/server/routes/analytics.py`, and `tests/test_pnl_pipeline.py`, plus a temporary `uv run python -` reproduction with a temp `DATA_DIR`, found `/analytics/equity-curve` applies `limit` before the date-window cutoff and can drop recent chart points.
- Manual read of `frontend/src/components/CommandPalette.tsx`, `frontend/src/App.tsx`, `frontend/src/pages/Universe.tsx`, `tests/test_frontend_navigation_ia.py`, and operator-layer design docs found the global command launcher lacks the implemented Universe Board route and its Enter handler can convert normal filtered keyboard command selection into a Command Center draft.
- Manual read of `frontend/src/charts/ProbabilityChart.tsx`, `frontend/src/pages/PredictionLens.tsx`, `frontend/src/types.ts`, `aletheia/server/routes/prediction.py`, `aletheia/data/polymarket_provider.py`, and `aletheia/agents/prediction_observer.py`, plus `node -e` timestamp parsing verification, found Polymarket fallback history can return numeric timestamp strings that the chart parses as `NaN`.
- Manual read of `frontend/src/charts/TradeAnalyticsPanel.tsx`, `frontend/src/pages/TradesPositions.tsx`, `frontend/src/api/client.ts`, `aletheia/analytics/trade_stats.py`, and strategy-deletion docs found the active trade analytics tab still presents deleted RuleSet as a current source dimension through `strategy_records.parameters.ruleset`.
- Manual read of `aletheia/files/markdown.py`, `aletheia/knowledge/manager.py`, `aletheia/analytics/trade_case_builder.py`, retrieval/maintenance readers, and trade-case tests, plus a temporary `uv run python -` reproduction with a fixed clock, found same-title same-second knowledge writes overwrite the earlier Markdown file while keeping both DB rows.
- Manual read of `docs/design/learning_contract.md`, `aletheia/learning/lessons.py`, `aletheia/agents/lesson_reviewer.py`, lesson consumption paths, and `tests/specs/spec_learning_pipeline.py`, plus a temporary `uv run python -` reproduction, found applied lessons are never re-reviewed even though applied is the consumed stable state.
- Manual read of `docs/design/learning_contract.md`, `docs/design/knowledge_contract.md`, `aletheia/analytics/memo_effectiveness.py`, `aletheia/analytics/insight_synthesizer.py`, and `aletheia/agents/paper_trader_ranker.py`, plus a temporary `uv run python -` monkeypatch reproduction, found TradingMemo half-life affects prompt rendering but not actual ranker scoring.

- **2026-07-06 verified fan-out sweep** (read-only 9-lane reviewer workflow, each finding adversarially re-verified by an independent skeptic; 13 candidates → 11 kept as #132–#142, 2 refuted). Lanes and files: control-routes (`server/routes/{telegram,update,gateway,dev_reload,command}.py`); risk-bitget-routes (`server/routes/{risk,bitget}.py`); gateway-lifecycle (`gateway/{lifecycle,state_machine,recovery,state_store,session,preflight}.py`); gateway-edge (`gateway/{frontdoor,frontdoor_route,tunnel,autostart,pidfile,git_hooks,logs,_win32_proc}.py`); update-selfheal (`update/{applier,git_ops,smoke}.py` + `gateway/git_hooks.py`); server-app (`server/app.py`, `server/dev_reload.py`); position-exit-tpsl (`portfolio/{position_manager,exit_rules}.py`, `execution/stops.py`); exchange-sync-brokers (`portfolio/exchange_sync.py` + `execution/{binance_broker,okx_broker,bitget_broker,paper_broker,broker_utils,position_enrichment}.py`); low-level-clients (`exchanges/{okx,binance,bitget}/client.py`, `exchanges/bitget/auth.py`). Each lane compared code vs its matching `docs/design/*.md` contract + `tests/specs/spec_*.py`.
- Reviewer reproductions during the sweep: ran `redact_log_text` via `uv run` on exchange-format inputs (bare `secret`/`ACCESS-KEY`/`ACCESS-SIGN` leak, `ACCESS-PASSPHRASE` value redacted → #133); offline `ccxt.binanceusdm(...)` introspection (`aiohttp_trust_env=False`, `timeout=10000` → #142). Hand re-reads before recording the two secret/trust-boundary headline findings: `server/app.py:430-476` + `server/routes/settings.py:755-784` (#137), `gateway/logs.py:24-29` (#133).

- **2026-07-06 gap-sweep** (read-only 16-lane reviewer workflow over the previously-unscanned subsystems, each finding adversarially re-verified; 24 candidates → 20 kept as #143–#162, 4 refuted). Lanes: risk-engine (`risk/engine.py` full ~20-check chain + `kill_switch.py`); execution-chokepoint (`execution/gateway.py` + reconciliation/broker_utils/paper_tools/errors/position_enrichment/stops); permissions-tooling (`agent_runtime/permissions,tool_registry,orchestrator,run_logger,health`); runtime-selfheal (`agent_runtime/scheduler` job bodies + `pi_daemon,connectivity,stability_monitor,auto_resume,resume_gate,startup_gate`); config-secrets (`config/secrets,loader,schema,exchanges` + `diagnostics/config_consistency`); storage-integrity (`storage/repositories,models,migrations,sqlite,event_store,artifact_store,hashing`); agent-shell-decision (`agent_shell/trading_decision,trading_context,intent,commands`); agent-shell-operator (`agent_shell/operator_loop,operator_store,operator_recommendations,sessions,executor,synthesis,reflection,…`); agents-producers (`agents/risk_critic,post_trade_reviewer,portfolio_agent,research_agent,research_worker,divergence_scanner,checkpoints,stock_observer`); data-provider-symbols (`data/ccxt_provider,symbols,providers,price_feed,cache,news_*,polymarket_provider,yfinance_provider,…`); data-collectors-sources (`data/collectors/*` + `data/sources/*`); analytics-pnl-attribution + analytics-learning-feedback (`analytics/*` 28 files split across two lanes); model-provider (`model_provider/*` 14 files); universe-trading-sizing (`universe/*` + `trading/*` + `regime/*` + `policies/*`); operator-shell-misc (`gateway/operator_shell,sync/*,trust/guard_chain,portfolio/uta/*,telegram/bot,files/*,diagnostics/doctor`). `cli/*` (23 files) deliberately deferred (Web-UI-only project). Each lane compared code vs its matching `docs/design/*.md` + `tests/specs/spec_*.py`.
- Reviewer reproductions during the gap sweep: temp-dir in-process repro that `load_config()` returns a shared mutable object leaking unsaved values (#147); `uv run python` repro that `render_calibration_for_pi` raises `ValueError` on `ece=0.123`/`0.0` (#157). Hand re-read to reconcile the coinalyze OI parser vs pollution contradiction: `data/collectors/coinalyze.py:60-189` (#156).

- **2026-07-06 final coverage sweep** (read-only 12-lane workflow over the frontend page-by-page + backend zeros, each finding adversarially re-verified; 21 candidates → 18 kept as #163–#180, 3 refuted). Lanes: fe-settings (`Settings.tsx` 4280); fe-commandcenter (`CommandCenter.tsx` 1548); fe-serenity-learning (`SerenityLens`+`Learning`); fe-prediction-news-universe (`PredictionLens`+`NewsIntel`+`Universe`); fe-trades-charts-proposals; fe-risk-overview-agent; fe-components (`MarketTicker`/`DataTable`/`ConnectionGuard`/… + `components/ui/*`); fe-shared-charts (`App.tsx`/`api/client.ts`/`types.ts`/`lib/format.ts`+charts); be-knowledge (`knowledge/*`); be-continuity-uta (`runtime_continuity/*`+`portfolio/uta/*`); be-doctor (`diagnostics/doctor.py`); be-cli (`cli/*`). Reviewers applied the frontend house rules (UI honesty, `fmtSymbol`, `+00:00`-vs-`Z` ISO trap, loading lanes, `onError`, computed-style) and the SDD fake-green/trust-boundary checks. `cli/` covered here (deferral lifted for this pass). Reproduction: `uv run python -c` confirming `AgentConfig`/`OperatorConfig` permission_mode defaults diverge (Paper vs ReadOnly, #180).

Known caveat: several commands were run as narrow review evidence. Passing tests do not prove the full system is clean because several tests were found to miss the exact problematic behavior.

## Continuation Notes

Next review should continue from implementation domains outside runtime-continuity, model/OAuth controls, news/prediction/capital route findings, scheduler/job-map findings, Universe provider construction, macro collector yfinance blocking, lesson hard-block category boundaries, applied-lesson overturnability, TradingMemo ranker time-decay, strategy-engine deletion residue, frontend strategy API-client residue, trade-insights symbol-direction aggregation, TradeAnalytics RuleSet source residue, knowledge Markdown filename collisions, System Operator naming migration, entry-plan stop/TP geometry, scanner-specific lesson evidence, Serenity manual opportunity assessment, Serenity opportunity tier UI, Serenity market_type propagation, Serenity PostMortem close-time accuracy, Serenity thesis-invalidation marker semantics, execution-contract RuleSet-exit drift, portfolio-review close disambiguation, Agent Activity manual-run controls, Learning legacy strategy-progress UI, exchange entry partial-fill status semantics, NewsIntel manual-analysis persistence, PredictionLens market-ID bridging, Prediction probability chart fallback timestamps, Proposals approval/rejection state handling, Markets candlestick market-type forwarding, Trades & Positions chart navigation, position-reconciliation sync-status display, Overview scheduler status, Go-Live scorecard model-health scoring, Overview AgentFeed failure severity mapping, first-screen SetupBanner setup-state detection, CausalChain fill/position rendering, PnL equity-curve windowing, and CommandPalette route/keyboard behavior, then continue through remaining frontend pages, API client methods, remaining learning loop producers/consumers, and remaining non-Serenity execution/holding data flow. SDD/spec quality, API-reference coverage, runtime-continuity route controls, model/OAuth routes, news/prediction/capital data-entry routes, scheduler/job-map coverage, Universe market-data provider boundaries, macro collector blocking risk, structured lesson enforcement boundaries, applied-lesson overturnability, TradingMemo ranker time-decay, strategy deletion residue, stale frontend strategy endpoint coverage, trade-insights matrix instrument identity, TradeAnalytics RuleSet source residue, knowledge Markdown filename collisions, active PI/System-Operator naming drift, entry-plan semantic geometry, scanner-specific lesson promotion evidence, Serenity manual opportunity assessment, Serenity opportunity tier UI, Serenity market_type propagation, Serenity PostMortem close-time accuracy, Serenity thesis-invalidation marker semantics, execution-contract RuleSet-exit drift, portfolio-review close disambiguation, Agent Activity manual-run controls, Learning legacy strategy-progress UI, exchange entry partial-fill status semantics, NewsIntel manual-analysis persistence, PredictionLens market-ID bridging, Prediction probability chart fallback timestamps, Proposals approval/rejection state handling, Markets candlestick market-type forwarding, Trades & Positions chart navigation, position-reconciliation sync-status display, Overview scheduler status, Go-Live scorecard model-health scoring, Overview AgentFeed failure severity mapping, first-screen SetupBanner setup-state detection, CausalChain fill/position rendering, PnL equity-curve windowing, and CommandPalette route/keyboard behavior now have initial passes. Do not re-open Settings, API performance, PnL, persistence, confirmation metadata, conformance-matrix mechanics, API endpoint-index drift, runtime-continuity route controls, model/OAuth controls, news/prediction/capital route findings, scheduler/job-map findings, Universe direct-provider construction, macro collector yfinance fallback, lesson category enforcement, applied-lesson overturnability, TradingMemo ranker time-decay, strategy_lifecycle FSM residue, frontend `setStrategyVenue()` residue, trade-insights symbol-direction matrix identity, TradeAnalytics RuleSet source residue, knowledge Markdown filename collisions, System Operator `pi_daemon` naming drift, native entry-plan stop/TP geometry, scanner-specific lesson reviewer evidence, Serenity manual opportunity assessment, Serenity opportunity tier UI, Serenity market_type propagation, Serenity PostMortem close-time accuracy, Serenity thesis-invalidation marker semantics, execution-contract RuleSet-exit drift, portfolio-review close disambiguation, Agent Activity reasoning-step stale UI, Agent Activity manual-run controls, Learning legacy strategy-progress UI, exchange entry partial-fill status semantics, NewsIntel manual-analysis persistence, PredictionLens market-ID bridging, Prediction probability chart fallback timestamps, Proposals approval/rejection state handling, Markets candlestick market-type forwarding, Trades & Positions chart navigation, position-reconciliation sync-status display, Overview scheduler status, Go-Live scorecard model-health scoring, Overview AgentFeed failure severity mapping, first-screen SetupBanner setup-state detection, CausalChain fill/position rendering, PnL equity-curve windowing, CommandPalette route/keyboard behavior, or `set_symbol_observe_only` symbol-format storage unless new evidence requires it.
