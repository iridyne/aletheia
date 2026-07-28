# Bug Review Handoff - 2026-07-06

## Current State

- Review task is paused, not complete.
- Findings are recorded in `docs/_ai/audits/FULL_BUG_REVIEW_2026-07-06.md`.
- Current ledger range: `#1` through `#180`. **Full-project coverage pass is complete** (see the ledger's "Coverage status: full-project pass complete").
- `#163`–`#180` were added by a read-only 12-lane final sweep over the frontend page-by-page + backend zeros (`knowledge/`, `runtime_continuity/`+`uta`, `doctor.py`, `cli/`); 21 candidates → 18 kept, 3 refuted. All adversarially re-verified.
- **2026-07-06 tidy/verify pass**: the ledger now opens with a **Finding Index** (legend + HIGH fix-first list + a navigable 180-row table: number, severity, primary `file:line`, one-line, status). `#1`–`#131` (the earlier manual batch) were re-verified against current code: **126 confirmed, 2 line-drift, 3 overstated, 0 stale-fixed, 0 duplicate**. The 5 non-confirmed ones (#18, #23, #38, #84, #119) carry an appended `**Correction (verify pass 2026-07-06)**` note and are flagged `corrected` in the index — read the note before acting (notably **#84 is NOT an XSS bug** — `_ERROR_HTML.format()` raises KeyError on CSS braces first, so the real defect is a broken/500 error page; downgrade to low/cosmetic).
- `#132`–`#142` were added by a read-only 9-lane reviewer fan-out over the previously under-covered backend (control routes, gateway lifecycle/edge, self-update, server app, position exit, exchange sync/brokers, low-level clients); 13 candidates → 11 kept, 2 refuted. Secret/trust-boundary headline findings (#133, #137) re-read by hand.
- `#143`–`#162` were added by a read-only 16-lane gap sweep over the remaining unscanned subsystems (risk engine, execution chokepoint, permissions/tooling, runtime self-heal, config/secrets, storage integrity, agent_shell decision + operator, agents producers, data provider/collectors/sources, analytics pnl + learning, model_provider, universe/trading/sizing, operator-shell + misc); 24 candidates → 20 kept, 4 refuted. All findings adversarially re-verified by an independent skeptic. Two lanes verified clean (execution-chokepoint order-flow invariants; universe-trading-sizing incl. persona/dynamic-sizing confirmed dormant). Refuted items are under "Investigated and refuted" — do not re-report.
- No business code was intentionally changed in this review session.
- Current untracked review artifact: `docs/_ai/audits/FULL_BUG_REVIEW_2026-07-06.md`.
- This handoff file is the short operational guide for the next AI/Claude Code session.

## Owner Requirements

- Record every finding locally. Do not keep important discoveries only in chat.
- Move toward full project coverage. Do not repeatedly re-check already covered areas.
- Use [Product Direction](../../product/definition/DIRECTION.md) as the high-value reference for durable product intent; use the relevant L2 contract for implementation detail.
- New bug records should be short, Markdown-structured, and fix-oriented:
  - title
  - domain
  - impact
  - document expectation
  - code evidence
  - reproduction/verification
  - expected fix direction

## Boundary For Next Session

- If continuing review: stay read-only except for updating review docs.
- If fixing bugs: pick a bounded finding, state the touched boundary first, add focused tests for trust/risk/execution/audit/secrets/lifecycle changes, then implement.
- Do not enable live trading, place orders, modify credentials, loosen risk limits, or restart the gateway unless the owner explicitly asks.
- Use `uv run` for all Python commands.

## Files To Read First

- `AGENTS.md`
- `docs/product/definition/DIRECTION.md`
- `docs/_ai/audits/FULL_BUG_REVIEW_2026-07-06.md`
- `docs/_ai/session/BUG_REVIEW_HANDOFF_2026-07-06.md`
- Relevant SDD contract docs under `docs/design/`

## Most Recent Findings (gap sweep #143–#162 + final sweep #163–#180)

Final-sweep (frontend + backend zeros) notable:
- `#171` Overview "Exchange" light always probes Bitget, ignoring the configured exchange → OKX/Binance deployments get a false red (or a false green from a stale Bitget probe).
- `#174` Topbar master state can never reflect the risk engine (`riskState` always empty) → CAUTION/OPERATIONAL are dead branches; healthy operation shows a misleading "not evaluated" STANDBY.
- `#165` CommandCenter Pause/Resume/Wake swallow the backend 409 (LLM-unavailable) → operator believes it resumed while the system stays paused.
- `#169`/`#170` Charts prediction cards read non-existent fields (blank question); Charts feeds board "crypto" into `fmtSymbol` → perps render with a slash.
- `#176` `knowledge_contract.md` still documents `post_trade_review` as a retrieved lesson type (contradicts the code-enforced, test-locked anti-hallucination invariant — a re-wire following the doc reinstates the ~1900-row flood).
- `#178` `config_consistency` info-severity note is escalated to a doctor BLOCKER (drives `doctor` exit 1); `#180` `inspect config` prints the wrong permission_mode namespace.
- `#163` Settings coverage matrix mislabels 18 editable fields (incl. safety-critical leverage/margin) as config.yaml-only.
- Refuted (3): serenity-evidence market_type no-op, runtime-continuity enabled-flag write-guard (intended per SOP), validate_shadow disabled-mode passed=True (graceful degradation).

Two HIGH:
- `#145` Exchange-connectivity auto-resume task is orphaned on the ephemeral per-job event loop — a **mid-run connectivity loss pauses trading forever** (only a startup-triggered pause auto-resumes). Self-heal resilience is effectively broken for the common case.
- `#159` Guard promotion (daily cron, auto_promote default ON, mutates live guard state) computes loss-rate from raw rows **including synthetic/reconciler closes** and skips the clean-window floor → can promote a hard-blocking guard or deprecate a protective one on fabricated evidence.

Notable medium:
- `#153` L1/L2 self-heal actions are unreachable dead code + the "PI approval gate" is a no-op rubber stamp; specs pass green (fake-green) while the documented safety invariant is unenforced.
- `#157` `render_calibration_for_pi` crashes on every real report (broken f-string) → conviction-calibration feedback to the System Operator LLM is silently dead.
- `#161` Deep/fast tier LLM calls resolve fallback config + health against `default_provider`, not the tier provider actually used.
- `#160` Memo synthesizer + reasoning-feedback (both feed the PI prompt) fit on positions incl. synthetic closes.
- `#162` `proposal-pack` export ignores `proposal_id` and dumps the entire proposals + serenity tables.
- `#143` risk check #5 drawdown reads paper_internal position notional as equity (can zero the size cap; dormant under default `bitget_demo`).
- `#156` Coinalyze OI collector — verify the live `/open-interest` schema first (guessed keys may make it a dead feed; if it writes, aggregated OI pollutes the primary read).

(Full batch `#143`–`#162`; 4 refuted candidates in the ledger's "Investigated and refuted" — incl. the `flip` double-exposure and coinalyze coin-as-USD claims.)

## Last Active Review Area

**Full-project coverage pass is complete.** Three verified fan-out sweeps (9 + 16 + 12 lanes) plus the pre-existing #1–#131 manual sweep now cover the frontend end-to-end and every backend subsystem. The 2026-07-06 final sweep closed the last gaps: frontend page-by-page (all 13 pages, 5 charts, all components + shared App/client/types/lib), `knowledge/` internals, `diagnostics/doctor.py` line-by-line, `cli/`, and `runtime_continuity/`+`portfolio/uta` (the latter came back clean). See the ledger's "Coverage status: full-project pass complete" for the authoritative statement. There is no remaining unscanned territory — next work is either fixing recorded findings or a targeted re-scan when a subsystem/data-source/page materially changes.

Clean lanes (do not reopen without new evidence): execution-chokepoint, universe-trading-sizing, risk-bitget-routes, gateway-lifecycle, runtime-continuity-uta, and the largely-clean fe-prediction-news-universe / fe-components.

## Areas Already Covered Enough For Now

Do not reopen these unless there is new evidence or the owner asks:

- Settings/config UI drift and async settings performance.
- Persistence export/import/backup issues already recorded.
- Confirmation metadata and command confirmation issues already recorded.
- Spec/conformance-matrix mechanics.
- API endpoint index drift.
- Runtime-continuity route controls.
- Model/OAuth route controls.
- News, prediction, capital route findings already recorded.
- Scheduler/job-map findings.
- Universe direct-provider construction.
- Macro yfinance fallback blocking.
- Lesson hard-block category enforcement.
- Applied-lesson overturnability.
- TradingMemo ranker time-decay.
- Strategy lifecycle deletion residue.
- Frontend stale strategy endpoint.
- Trade Insights market-type identity.
- TradeAnalytics RuleSet source residue.
- Knowledge Markdown filename collisions.
- System Operator `pi_daemon` naming drift.
- Native entry-plan stop/TP geometry.
- Scanner-specific lesson evidence.
- Serenity findings already recorded.
- Portfolio-review close disambiguation.
- Agent Activity stale controls.
- Learning legacy strategy-progress UI.
- Exchange entry partial-fill semantics.
- NewsIntel manual-analysis persistence.
- PredictionLens market-ID bridging and fallback timestamp parsing.
- Proposal approval/rejection UI/backend state issues.
- Markets candlestick market-type forwarding.
- Trades & Positions chart navigation and reconciliation status.
- Overview scheduler status and AgentFeed failure severity.
- Go-Live scorecard model-health scoring.
- SetupBanner setup-state assumptions.
- CausalChain fill/position rendering.
- PnL equity-curve windowing.
- CommandPalette route/keyboard behavior.

## Suggested Next Review Coverage

**Coverage is complete — there is no remaining unscanned area.** Three verified sweeps + the #1–#131 manual pass cover the frontend end-to-end and every backend subsystem. Do NOT re-run a broad sweep; instead:

- **Fix recorded findings** (see Fix Triage Hints below), starting with the HIGH items. Each fix adds its own focused regression per AGENTS.md change discipline.
- **Targeted re-scan only on material change**: a NEW subsystem, a NEW data source (register in `data_utilization.md` first), a NEW learning/performance consumer (apply the synthetic-close + clean-window filter check — see #159/#160), or a NEW frontend page must get its own pass. Do not assume the ledger stays complete.
- **One live check the review couldn't do**: confirm the Coinalyze `/open-interest` field name against the live API before touching #156.
- For any new area, compare against the matching `docs/design/*.md` + `tests/specs/spec_*.py`, and check reachability (fake-green trap), async sync-I/O on hot paths, secret/trust-boundary exposure, and the frontend house rules (UI honesty / `fmtSymbol` / `+00:00`-vs-`Z` ISO / loading lanes / `onError`).

## Fix Triage Hints

High-value fix candidates with narrow blast radius:

- `#137`: add `/api/settings/risk` + `/api/settings/exchange-mode` to `_PROTECTED_PATHS` in `server/app.py` (or switch to a prefix predicate). **Top priority — trust boundary; add a focused auth regression.**
- `#133`: extend `_SECRET_PATTERNS` in `gateway/logs.py` to cover bare `secret` and `access[_-]?(key|sign)`; add a redaction unit test with exchange-format inputs.
- `#135`: check `reset_hard().ok` in `update/applier.py` before reporting rollback success.
- `#132`: derive gateway-restart audit `actor`/`source` from the trigger, not a hardcode + body field.
- `#136`: add `COINALYZE_`/`FRED_` (and `OKX_`/`BINANCE_`) to `_safe_env` strip prefixes.
- `#131`: apply `memo_half_life_weight()` inside `paper_trader_ranker._memo_scoring()` and add a direct unit test.
- `#129`: make generated knowledge Markdown filenames collision-proof and add same-title same-second regression.
- `#127`/`#124`/`#125`/`#126`: Polymarket timestamp normalize / equity-curve window-before-limit / CommandPalette route+keyboard.

Cross-broker (fix all three brokers together, plus a shared regression):

- `#140`: accumulate `entry_fee` on averaging-in in okx/binance/bitget brokers (mirror `paper_broker`); overstates realized PnL fed to the learning loop.
- `#141`: recompute `liquidation_price` on averaging-in in the three live brokers (display/LLM only, not risk-gate).
- `#139`: exchange-side stop replacement is bitget-only; okx/binance trailed stops never reach the exchange (gated behind default-off `exchange_tpsl_enabled`).
- `#142`: Binance client proxy bypass — **first confirm it isn't already fixed on `iridite/system_proxy_support`.**

Perf (async blocking on hot/startup paths):

- `#138`: move `sync_all_exchanges()` post-`yield` in the lifespan.
- `#134`: TTL/mtime-cache the frontdoor route-file read (low severity).

Gap-sweep (#143–#162) high-value candidates:

- `#159` (HIGH, learning integrity): wrap `guard_promotion.run_promotion_check` + `guard_effectiveness.compute_guard_stats` reads in `is_synthetic_close()`/`_genuine_rows()` + `clean_window_cutoff` (mirror `factor_feedback`). Mutates live guard state — add a regression that a synthetic-close-only family does not promote/deprecate.
- `#145` (HIGH, resilience): schedule the connectivity auto-resume loop on the persistent uvicorn loop (capture it like `pi_daemon._uvicorn_loop`), not the ephemeral per-job loop. Fix the false-green `test_connectivity` mocks too.
- `#157` (medium, quick win): precompute `ece_str` before the f-string in `render_calibration_for_pi`; add a spec that actually calls it.
- `#160` (medium): add `close_reason NOT IN (SYNTHETIC_CLOSE_REASONS)` to `insight_synthesizer._gather_statistics` + `reasoning_feedback` (same class as #159, feeds the PI prompt).
- `#161` (medium): thread the resolved tier provider name as `provider_name=` into `complete_with_fallback`.
- `#162` (medium): apply `where_clause`/`proposal_id` scoping in `sync/exporter._export_db_tables`.
- `#153` (medium, SDD): either wire `decide_actions` to emit self-heal actions or mark the L1/L2 self-heal specs deferred; make `_request_pi_approval_for_selfheal` actually gate.
- `#143` (medium, risk): make the paper_internal snapshot store true equity, or route its drawdown to an equity source; fix the false comment.
- `#144`/`#149`/`#150` (low, mechanical): reconciler status `completed_with_errors`; wire `prune_onchain_snapshots`; add a prediction-snapshot prune.
- `#147`/`#148`/`#158` (low): defensive `model_copy` in `load_config` (or validate-all-then-apply); fix `config_consistency` env-var hints; single-pass `unknown`-bucket avg in `trade_insights`.
- `#156` (medium, verify-first): confirm the live Coinalyze `/open-interest` field name, then fix the parser AND add `coinalyze` to `_CROSS_VENUE_TAGS`.
- `#151`/`#152`/`#154`/`#155` (low): force perp on `direction=='short'`; run ranker fallback before the same-symbol early return; gate divergence signals to up-framed markets / observation-only; add `market_type` to the OHLCV cache key.

Final-sweep (#163–#180) candidates:

- `#171` (medium): select the Overview exchange-test endpoint by `cfg.exchange.name` (okx/binance/bitget), or add a venue-agnostic `/exchange/test-connection`.
- `#174` (medium): add a `getRiskStatus` query to the topbar StatusBar and feed `.status` into `deriveMasterState` (mirror `RiskControls.tsx:83-86`), or add `risk.status` to `/api/status`.
- `#165` (medium): add `onError` to CommandCenter pause/resume/wake + runtime mutations (mirror `App.tsx` `extractResumeError`).
- `#169`/`#170` (medium, Charts): read `event_title`/`volume_24h` (restore `PredictionMarket` typing); use per-snapshot `market_type` before the board asset-class in `fmtSymbol`.
- `#176` (medium, doc): fix `knowledge_contract.md` §7.2/§3.4 to drop `post_trade_review` from retrieval (align with `router.py` + ranker).
- `#178` (medium): three-way severity map in `doctor._probe_config_consistency` (`info`→info); `#180` (medium): `inspect_cmd.py:367` → `cfg.agent.permission_mode`.
- `#163` (medium): derive the Settings coverage-matrix `controlled` set from the field arrays the cards render (stop the drift).
- Low/mechanical: `#164` add `onError` to the silent Settings mutations; `#166` guidance-action→tone map; `#167` render `assessed_tier` in the Serenity Evidence tab; `#168` add an `analyzed` NewsIntel filter bucket; `#172` gate RiskControls "within limits" on `riskState==='healthy'`; `#173` show date in ActivityTimeline; `#175` `fmtSymbol` in SymbolDirectionMatrix; `#177` wire/prune the knowledge router roles; `#179` treat undateable audit-chain breaks as WARN.

Higher-risk candidates needing extra care/tests:

- `#130`: applied lesson re-review changes learning policy lifecycle.
- `#109`: exchange entry partial-fill semantics touches execution state.
- `#98`: native entry-plan stop/TP geometry touches risk decision acceptance.
- Any finding touching `aletheia/risk/`, `aletheia/execution/`, secrets, live unlock, or gateway lifecycle.

Refuted (do NOT re-report — reasoning in the ledger): "post-merge hook restarts onto unverified code" and "OKX contractSize fails-open oversizing".

## Verification Notes

The review ledger contains exact commands and reproduction notes under `## Verification Commands Run During Sweep`.

Common narrow checks:

```powershell
uv run pytest tests/specs/ -q --tb=short
uv run pytest tests/test_architecture_fitness.py -q
uv run pytest tests/test_frontend_navigation_ia.py -q
```

For any focused fix, run the new focused regression first, then the closest existing test file. Do not claim a trust/risk/execution/secrets/live fix is complete without test evidence.
