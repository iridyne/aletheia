# Changelog

## [Unreleased] - 2026-08-08 — Versioned incremental technical analysis

- Added a batch reference analyzer and append-fast incremental engine for
  closed-bar SMA/EMA, RSI, ATR, Bollinger, momentum, volume, VWAP, Donchian, and
  OBV facts. Revisions and gaps are explicit degraded-data events.
- Market Observer and Universe feature collection now reuse a process-wide,
  exact-instrument technical snapshot service. Persisted feature rows carry the
  algorithm version, input fingerprints, warm-up state, and data-quality facts;
  the Operator material fingerprint includes technical provenance.
- Added a format-neutral hot/cold OHLCV tier manager with complete-chunk
  planning, durable receipt validation, and read-back verification. SQLite
  remains the hot authority; ZSTD and the BLOB-vs-file choice remain deferred.
- Added a repeatable micro-benchmark and focused contract/integration coverage.

## [Unreleased] - 2026-07-17 — Cascade opportunity expiry when parent snapshot is superseded

Fixes an unbounded-growth backlog exposed by the change-suppression valid_until
slide + soft-TTL filter fixes: after the pre-restart baseline of 52 open
opportunities, the pool grew to 72 in the first hour post-restart, with
observed 63/72 opps belonging to *superseded* universe snapshots. Root cause
was a design gap: `expire_universe_snapshots` marks snapshots superseded but
never cascaded to their child opportunities. Before the soft-TTL fix those
would age out naturally at the family-TTL cliff; with soft-TTL they linger
indefinitely at `status='open'`.

- `repositories.expire_opportunities_from_superseded_snapshots(market_type)`:
  single UPDATE that transitions `status='open'` opportunities linked to
  any `universe_snapshots.status='superseded'` row into `status='expired'`.
  Idempotent — no-op once pool is clean. Terminal states preserved.
- `service.py` calls it in *both* branches of `UniverseService.refresh`:
  after each new-snapshot publication AND after each change-suppressed
  reuse. Self-healing at 10-min cadence regardless of fingerprint stability.
- One-shot repair of the 63 orphans observed at diagnosis time: run
  `python -c "from aletheia.storage.repositories import
  expire_opportunities_from_superseded_snapshots as f; print(f('crypto'))"`.
  Post-restart the running gateway will keep the pool tidy on its own.

## [Unreleased] - 2026-07-17 — Gateway CLI: no-browser as default

Flips the `gateway start` / `gateway restart` default: the browser no longer
pops open on every restart. Owner has to explicitly opt in with `--browser`,
or use the already-existing `aletheia gateway open-dashboard` helper any time.

- `--browser/--no-browser` is a two-way typer flag; default is `--no-browser`.
- Backward compatible: existing scripts and docs passing `--no-browser`
  continue to work (the flag becomes redundant but not an error).
- `cfg.app.auto_open_browser` config option is untouched; the CLI default
  short-circuits it. To restore auto-open on start, pass `--browser`
  explicitly or invoke `aletheia gateway open-dashboard` after boot.

## [Unreleased] - 2026-07-17 — Approval Desk records final-stage rejections

Closes the loop on "why didn't this reach me?" once a candidate crosses into
the operator stage — the Funnel card covers everything BEFORE that boundary,
the Approval Desk now records everything AFTER.

### feat(overview): Approval Desk Recent decisions section + trace drawer

- Adds a collapsible "Recent" section beneath the pending approval list.
  Shows outcome counts (trade / operator_skip / user_reject / auto_reject /
  operator_watch) with distinct icons and colored left borders.
- Each row shows symbol / direction / outcome label / relative timestamp +
  the operator's own one-line reason (from pi_decisions.reasoning or the
  reject audit event message).
- Clicking a row opens a DetailDrawer that fetches the full lifecycle trace
  (emit → filter_trace → pi_decision → risk_decision → proposal → order →
  fill → position → close) via a new /opportunity-trace endpoint.
- `user_reject` vs `auto_reject` distinguished at the endpoint layer by
  joining trade_proposals with audit_events (actor='human' → user, else
  auto). Users see clearly whether *they* pressed reject or the system did.

### feat(api): two new analytics endpoints

- `GET /api/analytics/operator-decisions?hours=24&limit=50` — merges
  pi_decisions (action IN 'trade'/'skip'/'watch'/'flip'/'scale_in') with
  rejected trade_proposals into a unified outcome timeline. Actor split
  computed from audit_events.
- `GET /api/analytics/opportunity-trace/{opportunity_id}` — thin route over
  `analytics.opportunity_flow.trace_opportunity_flow`. Reuses the existing
  CLI trace builder (`aletheia opportunity trace --id`) with no logic
  duplication.

### docs

- `api_reference.md`: two new endpoint rows.

## [Unreleased] - 2026-07-17 — Inline paper_trader nudge on universe events

Cuts new-opportunity → ranker-review latency from up to 3 minutes to ~1
second, without introducing a daemon. Debounced (3s) and gated by
APScheduler's `max_instances=1` so a burst of universe emits collapses into
one wake and never races an in-flight run.

- `agent_runtime/scheduler.py::notify_paper_trader(source)`: pulls the
  paper_trader job's `next_run_time` forward to `now + 0.5s` via
  `_scheduler.modify_job`. Fire-and-forget, thread-safe, non-raising.
  Skips silently when scheduler is down / paused / job absent.
- `universe/service.py`: calls it after a new snapshot is published, and
  from the change-suppression branch when `refresh_active_snapshot_ttls`
  actually slid opportunities forward (previously-expired now eligible).

Docs: `agent_contracts.md` describes the on-demand trigger alongside the
scheduled 3min cadence. Follow-up upgrade paths (Tier 2 — wider notify
surface; Tier 3 — full event-driven daemon) filed as a plan at
`docs/archive/legacy/plans/trading-pipeline/ranker_on_demand_upgrade_plan_2026-07-17.md`
with explicit observation gates to prevent premature escalation.

## [Unreleased] - 2026-07-17 — Opportunity funnel Tier-2: change-suppression TTL slide, ranker cadence, soft TTL, dashboard redesign

Follows the initial funnel card (commit 25a886d8) with a redesigned UI and three
root-cause fixes for the "everything expires before review" pattern the funnel
made visible.

### fix(universe): change-suppression must refresh valid_until (service.py, repositories.py)

- `repositories.refresh_active_snapshot_ttls(snapshot_id, now)` batches
  `UPDATE universe_members.expires_at` and `UPDATE opportunities.valid_until`
  forward-only (`WHERE expires_at < new_expires`). Idempotent, transactional
  against the shared writer slot.
- `UniverseService.refresh` calls it from the `reuse_previous_snapshot` branch.
  Previously, when the allocation fingerprint stayed stable for 30–60min, every
  carrier of the reused snapshot got hard-rejected as `ttl_expired` on the next
  ranker scan — even though the same working set had just been reconfirmed.
  Reuse is now a genuine no-op, not a wall-clock cliff.

### chore(scheduler): paper_trader cadence 5min → 3min (scheduler.py)

- Momentum (30min TTL) now gets ~10 review passes (was 6); mean_reversion (60min
  TTL) gets ~20 (was 12). Well below the `agents` executor budget.

### feat(ranker): soft-TTL filter with score decay (paper_trader_ranker.py)

- New `_ttl_soft_penalty_factor` returns `0.5 ** (expired_sec / family_ttl_sec)`
  — half-life = family TTL. Applied multiplicatively to `trade_score` at end of
  `_evaluate_candidate` (after guard penalty).
- Only past `_TTL_SOFT_HARD_CAP_MULT × ttl_sec` (3× family TTL) does the ranker
  hard-reject with `skip_reason=ttl_expired`. `paper_trader._settle_permanent_skips`
  continues to clean these up.
- `filter_trace` records new reason codes: `opportunity_ttl_soft_penalty` /
  `universe_member_ttl_soft_penalty` under `passed_with_penalty` decision.
- Symptom this fixes: signals aging just past their family window would vanish
  entirely; now they degrade gracefully so a late-arriving ranker can still
  consider them when there's nothing fresher.

### feat(overview): funnel card redesign per ASCII spec (Overview.tsx, index.css)

- Three sections: (1) top diagnosis headline ("Idle: all N open signals expired
  before review" / "Confidence gate rejected all N candidates" / "Healthy"); (2)
  24h cumulative bars — Discovered / Reviewed / Passed gates / Proposed + accept
  rate; (3) working set now — ✓ accepted / ✗ rejected + per-reason breakdown
  with dot (●) for count>0 and hollow dot (○) for count=0.
- TTL example rows now render "expired 42 min ago · ttl was 60 min" — no more
  `score -999` mystery numbers.
- Footer surfaces `min_confidence_to_trade` (clickable → Settings) and
  `universe_snapshot_age` so the change-suppression interaction with TTL is
  visible to the operator.

### feat(api): /analytics/opportunity-funnel payload enrichments (analytics.py, trading_context.py, paper_trader_ranker.py)

- Adds `reviewed_count` / `still_open_count` / `universe_snapshot_age_sec` /
  `family_ttl_sec` (map).
- `excluded_by_reason` examples now carry `opportunity_id`, `valid_until`, and
  `expired_seconds` — required for the redesigned UI's "expired X min ago" line.
- `paper_trader_ranker._make_candidate` and `trading_context.ranker_candidates`
  propagate `valid_until` end-to-end.

### docs

- `scheduler_contract.md`, `agent_contracts.md`, `SYSTEM_MAP.md`: paper_trader
  cadence 5min → 3min.
- `agent_contracts.md` (paper_trader_ranker): documents the TTL soft-filter
  contract (half-life formula, hard cap, filter_trace reason codes).
- `filter_evidence_contract.md`: two new paragraphs — soft-filter semantics and
  the change-suppression TTL slide invariant.
- `api_reference.md`: new `/analytics/opportunity-funnel` endpoint entry.

---

## [Unreleased] - 2026-07-17 — Universe scanner signal improvements + filter closed-loop feedback

### Universe Scanner — Signal Quality (scanners.py)

Five targeted fixes to scanner scoring quality and correctness:

1. **`oi_price_divergence` into derivatives scanner**: The +1 (OI↑/price↓ accumulation) and
   -1 (OI↓/price↑ weak-rally) divergence signal was collected but never consumed. Each
   non-zero reading now contributes ±15 pts to `score_derivatives`, raising the family's
   max achievable score from ~85 to ~100.

2. **`short_bias` score ceiling raised**: Was capped at ~78 pts vs ~100 for other families,
   causing consistent under-selection. Added `oi_delta_1h` (+10–12 pts for liquidation or
   new-shorts flow) and `rsi_4h` gate/bonus (+10 pts room-to-fall, early exit if already
   oversold on 4h). Max now ~100.

3. **`mean_reversion` gains `rsi_4h`**: 4h RSI ≥75 (overbought) or ≤25 (oversold) adds 15
   pts and sets direction, complementing the existing 1h RSI extremes.

4. **Volume penalty ordering bug fixed** (volatility + event scanners): `score *= 0.5`
   penalty was applied mid-accumulation (after ATR but before BB/taker/4h signals). The
   penalty was then effectively cancelled by subsequent additions. Moved to end of signal
   accumulation in both scanners; added `score *= 0.7` for unknown volume.

### Universe Filter — Two-Pass Architecture + Depth Gate (filters.py, service.py)

- **`apply_coarse_tradability_filters`** (pre-scan, spread threshold 3× max_spread_bps):
  runs before scanners assign families. Borderline spread assets (2×–3×) survive.
- **`apply_fine_tradability_filters`** (post-scan, per-family tolerance × 2×):
  applies after scanners run, using per-family spread tolerance multipliers from
  `filter_advisor`. Records rejections with `scanner_family` populated.
- **Depth hard gate**: rejects when `depth_10bps_usd < 10%` of configured minimum (only
  when depth data is available; missing data does not block).
- **Depth scoring in `_compute_tradability_score`**: +10 for deep (≥3× min), +5 for
  adequate (≥min), 0 for marginal (≥25% min), −10 for critically shallow (<25% min).

### Filter Feedback Loop — filter_advisor (filter_advisor.py, repositories.py)

Closes the feedback gap where filter-rejected candidates had no path back to scanner
calibration. Fine-filter rejections now populate `filter_candidate_outcomes` with
`scanner_family` and `direction`, enabling:

- **`repositories.filter_pressure_stats()`**: aggregates missed-alpha by family from
  settled fine-filter rejections.
- **`repositories.family_spread_tolerance_stats()`**: per-family spread sensitivity from
  `universe_forward_outcomes` (split by entry_spread_bps ≤15 / >15).
- **`filter_advisor.get_family_spread_tolerances()`**: computes per-family tolerance
  multipliers with empirical Bayes shrinkage. Initially all 1.0 (no behavioral change);
  calibrates as data accumulates over ~30 settled outcomes per family.
- **`filter_advisor.get_family_filter_pressure_summary()`**: surfaced in
  `operator_loop.SystemAssessment.filter_pressure` and logged by `outcome_recorder`.

### Dashboard — Opportunity Funnel Card (Overview.tsx, analytics.py)

New `OpportunityFunnelCard` in Overview surfaces why opportunities don't reach the
operator: emitted → proposed cumulative counts + point-in-time ranker snapshot (excluded
by reason, confidence floor, main_reason). Backed by new `/api/analytics/opportunity-funnel`
route reading `candidate_supply_snapshot` events.

## [0.5.0] - 2026-06-05 — Codex subscription provider (Web UI login + model listing)

Delivers guide §12.1.3 properly: the "ChatGPT Codex subscription / local
authenticated CLI mode" provider. Previously `codex_cli.py` was a bare
`codex exec <prompt>` wrapper with no login detection or model listing. Real
OAuth + token stay owned by the codex CLI (`~/.codex`); we store **no token**.
Zero change-controlled files touched. 272 tests passing (was 253).

### Provider (`model_provider/codex_cli.py`)
- `complete()` runs `codex exec --json` with the prompt on **stdin** and
  `-m <model>`, hardened with `--sandbox read-only --ask-for-approval never` (a
  text completion must never gain shell/write/approval surface). Parses the JSONL
  event stream, tolerantly skipping interleaved non-JSON log lines.
- `structured()` extracts JSON even from prose-wrapped output.
- `healthcheck()` is now 3-state: `not_installed` (binary absent) /
  `not_logged_in` (`codex login status` ≠ 0) / `available` (+ model list).
- `list_models()` parses `codex debug models --bundled` (visibility=="list").
- `ModelHealth` gained `models`; `ProviderConfig` gained `codex_model`.

### Web-triggered subscription login (`model_provider/codex_auth.py`, new)
- In-memory device-auth session: `start_login()` spawns
  `codex login --device-auth`, defensively parses the verification URL + user code
  from stdout to show in the dashboard; `poll_status()` asks the authoritative
  `codex login status`; `cancel()` aborts. No token is ever handled or returned.
  If the (undocumented) device-auth output can't be parsed, it fails with
  `device_auth_parse_failed` and the UI tells the user to run `codex login` in a
  terminal — honest, no fake success.

### API + UI + CLI
- Routes: `POST /api/model/codex/login-start|login-cancel`,
  `GET /api/model/codex/login-status|models`; `/api/settings/model` accepts
  `codex_model`; `/api/model/health` surfaces the model list. Audited; never
  returns a token.
- Settings → Model Provider shows a Codex panel: honest `not configured` /
  `not logged in` (+ Connect Codex device-auth flow) / `connected` (+ model
  dropdown). The green `connected` badge is backed only by a real
  `codex login status` exit 0.
- `model test` reflects the 3-state and shows the model count when available.

### Notes
- `config/secrets.py` deliberately untouched — codex self-manages its token, so
  there is nothing for us to store. No change-controlled module changed.
- Known pre-existing CLI quirk (out of scope, web path unaffected):
  `aletheia model set --enabled true` doesn't toggle the flag because Typer treats
  `Optional[bool]` as a `--enabled/--no-enabled` switch. Use the dashboard/API.

## [0.4.0] - 2026-06-04 — Go-live MVP: autonomous paper loop closes + long-run survival

Makes the system safe to run unattended for long stretches, accumulating real
paper PnL and strategy lessons. Strictly in-guide; only 3 change-controlled files
touched (all in execution/). 253 tests passing (was 217).

### C1 — Position management + automatic close (the keystone)
- New `portfolio/position_manager.py` closes open PAPER positions by stop-loss /
  take-profit / max-hold. This was the missing producer of closed positions — it
  activates realized PnL, the daily-loss breaker (risk §8), and the strategy
  promotion gates, which were all previously dead.
- positions table gains `stop_price` / `take_profit_price` / `close_reason`
  (additive migration). `close_position` now records the reason and writes a
  `position_closed` audit event (trust §1.4).
- **CHANGE-CONTROLLED** `execution/paper_broker.py`: carries the proposal's stop +
  a config-derived take-profit onto a newly opened position (averaging-in keeps
  the original stop). `execution/gateway.py`: wires `stop_price` into the paper
  call. Causal chain unchanged; no closing order is fabricated (paper close is a
  projection update + audit only).
- LIVE positions are NEVER auto-closed; the kill switch does NOT block closing
  (closing reduces risk); no latest price → skip (never fabricate an exit).
- New `TradingConfig` (stop% / TP% / max-hold / min-confidence).

### C2 — Entry logic upgrade (trades stop being noise)
- `market_observer` now uses the model provider's structured() to produce a real
  `{direction_bias, confidence}` (graceful fallback to neutral/speculative). The
  opportunity is ALWAYS recorded, even when not traded (kept for analysis).
- `paper_trader` skips neutral and below-`min_confidence_to_trade` signals (marks
  them `reviewed`, never discards) and sets a protective stop from config.
- **CHANGE-CONTROLLED** `execution/paper_tools.py`: optional `stop_price`
  passthrough; the paper-only hard-reject is unchanged.

### C3 — Data retention / cleanup (long-run survival)
- Whitelist-based pruning of high-volume telemetry only (agent_runs, run_steps,
  tool_calls, market/portfolio snapshots). Trust-bearing tables (proposals, risk
  decisions, orders, fills, positions), audit, and knowledge are NEVER pruned.
- JSONL size-cap rotation to a single `.1` generation (content never deleted).
- Daily `cleanup` scheduler job + `RetentionConfig`.

### C4 — Self-upgrade safety (a bad pull can't brick the box)
- `update/smoke.py`: post-pull import gate (fresh subprocess) + fast pytest subset.
- `apply_update` now verifies the pulled code and, on failure, performs a
  user-authorized controlled `git reset --hard` back to the pre-pull commit,
  restores deps + config, and writes an `update_rolled_back` audit. Never
  auto-restarts. Still not agent-callable.

### C5 — Process liveness + health visibility (safe to walk away)
- `_gateway_runner.py` is now a supervisor: it respawns the inner uvicorn child on
  abnormal exit with backoff, gives up after a bounded number of restarts (audited
  + alerted), and routes the inner server's stderr to `data/logs/errors.log`. Pid
  semantics preserved (written once; `gateway stop`/`status` unchanged).
- New `agent_runtime/health.py` + 10-min `health_check` job: honest signals
  (agent-failure ratio, kill switch) with transition-only, de-duped Telegram
  alerts. Never fabricates a positive "healthy" push. `HealthConfig` added.

## [0.3.0] - 2026-06-03 — Full MVP: risk hardening, live path, reconciliation

Closes the remaining functional gaps between 0.2.0 and a feature-complete MVP per
the implementation guide. Strictly in-guide scope (no roadmap §30 items). 178 tests
passing (was 137). Frontend rebuilt.

### Risk Engine — all 13 §21.3 checks (CHANGE-CONTROLLED, guide §31.3)
- Added the 7 missing checks: max_daily_loss (global; auto-trips kill switch),
  account_mode_match, max_leverage (futures), data_freshness (live),
  max_single_trade_risk (live, stop-based), strategy_lifecycle_state (live).
- New checks gate **live** primarily; paper/demo stays permissive. Daily-loss and
  kill-switch are global brakes across all modes.
- `TradeProposal` gained optional `leverage` / `stop_price` (additive migration).

### Kill Switch (§21.3, §2.4)
- `risk/kill_switch.py`: activate/deactivate/is_active, persisted in
  `runtime.kill_switch_active` (survives restart). Risk engine rejects every
  proposal when engaged. Auto-trips on daily-loss breach.
- Surfaces: CLI `aletheia risk kill|resume|status`, API `GET/POST /api/risk/kill-switch`,
  `GET /api/risk/boundaries`, dashboard Settings + Overview. All changes audited.

### Live order path (CHANGE-CONTROLLED, guide §31.3)
- `execution/bitget_broker.py`: approved live-limited proposals now actually place
  a Bitget order via `BitgetClient.place_order` and record order (with
  `exchange_order_id`) / fill / position. Gateway wires the `bitget_live_limited`
  branch to it; still blocked when `live_disabled` (defense in depth) + audited.

### Bitget adapter — full §19.3 interface
- Added get_account, get_positions, get_open_orders, cancel_order, get_fills
  (was 5/9 methods; now 9/9).

### Reconciliation (§7, §20.2)
- `execution/reconciliation.py`: compares local live orders vs exchange open
  orders, marks vanished orders filled, audits divergence. Never places/cancels.
  Runs on a scheduler interval (live mode only).

### Portfolio: position close + realized PnL
- `close_position` (realizes PnL, marks closed) and `today_realized_pnl` (source
  for the daily-loss check). Scheduler stores periodic portfolio snapshots.

### Optional Git versioning (§2.2, §9.4)
- `files/git.py`: opt-in (`runtime.git_enabled`, default off) versioning of
  knowledge/playbooks/reports; never stages secrets.enc/app.db/logs. Graceful
  when git is absent.

### Scheduler
- Added reconciliation (2 min, live-only) and portfolio-snapshot (10 min) jobs.

### Fix — `gateway stop` on Windows (§32 #3)
- `is_running()` used `os.kill(pid, 0)`, which TERMINATES on Windows rather than
  probing liveness, so the PID file was cleared while the server kept running and
  `stop` couldn't find it. Now uses a platform-aware `_pid_alive` (tasklist on
  Windows). Verified start→status→stop end-to-end.

---

## [0.2.0] - 2026-06-03 — Functional MVP completion

Completion pass after an audit found the initial "MVP" was ~35% of the dashboard
spec with several non-functional core pieces. This release closes those gaps so the
system is configurable and usable entirely from the dashboard.

### P1 — Backend config + secrets surface
- `ProviderConfig.api_key_secret_ref`; router injects API key from secret storage
- Real secrets CLI (`set` hidden prompt, `list`, `test bitget`); `model set` CLI
- Routes: `GET/POST /api/settings/model`, `GET /api/model/health`,
  `POST /api/bitget/test-connection`, `POST /api/settings/exchange-credentials`,
  `GET /api/bitget/credentials-status`
- Write-only secrets: GET endpoints never return raw keys (configured + last4 only)

### P2 — Dashboard configuration UX (OpenAlice-style)
- Settings page with all 8 §23.12 controls: model provider + write-only API key,
  Bitget demo/live credentials + test connection, exchange-mode switch,
  live unlock/lock, risk display, telegram, permission, pause/resume
- SetupBanner onboarding nudge; Overview shows account/PnL/positions/latest activity

### P3 — Runtime (the system now actually runs)
- FIX: scheduler now starts on gateway boot (was never invoked)
- Real pause/resume (halts scheduler jobs); market-data loop writes market_snapshots
- Agents call the LLM with graceful degradation; tool calls logged
- 4 new agents: strategy_designer, risk_critic, portfolio_agent, post_trade_reviewer
- portfolio/pnl.py: mark-to-market PnL + portfolio snapshots

### P4 — Trading workflow + dashboard actions
- §25 routes: proposal approve/reject, strategy get/promote/retire, portfolio
- FIX: gateway passed empty risk_decision_id (FK violation); now passes real id
- Dashboard action buttons (approve/reject, promote/retire, run-agent); KB tabs;
  Positions grouped + exposure; 5 seed playbooks on bootstrap

### P5 — Charts
- lightweight-charts: candlestick price chart + trade markers, PnL line chart
- Charts page, Reports page renders real summaries

### P6 — Test matrix + acceptance hardening
- Security tests: secrets never in logs/config/GET responses; restricted tools
  unreachable; live blocked by default; permission/live changes audited
- FIX: tool registry now initialized in orchestrator (agents were silently no-op
  when run before the app lifespan registered tools)
- §32 acceptance tests through the API/CLI
- 137 tests passing (was 84)

---

## [0.1.0] - 2026-06-03

### Initial milestones M0–M11 (function-level)

#### M0 — Repo Skeleton
- Python 3.12 project with uv, pyproject.toml
- CLI skeleton (aletheia/ato) with Typer
- Config schema + loader + YAML persistence
- Secrets handling (keyring + encrypted file fallback)

#### M1 — Gateway + Local Server
- FastAPI server on localhost:8787
- Gateway start/stop/status/restart/pause/resume
- PID file management, browser auto-open
- Health and status API endpoints

#### M2 — Storage
- SQLite schema (17 tables)
- JSONL event stream writer/reader
- Markdown writer for knowledge documents
- Repository CRUD functions
- Audit event system

#### M3 — Dashboard MVP
- React/Vite/TypeScript frontend (11 pages)
- Overview, Agent Runs, Dreaming, Knowledge Base, Opportunities, Proposals, Paper Trades, Positions, Strategy Lifecycle, Reports, Settings
- Dark theme, sidebar navigation
- Built to static files served from FastAPI

#### M4 — Model Provider Layer
- OpenAI-compatible (chat_completions + responses)
- Anthropic-compatible (Messages API)
- Codex CLI (graceful when unavailable)
- Router with caching and healthcheck

#### M5 — Agent Runtime
- APScheduler-based scheduler
- Tool registry with 5-level permission enforcement
- Agent orchestrator with full lifecycle logging
- Market Observer, Research Agent, Dreaming Agent

#### M6 — Knowledge Base
- Research Notes, Strategy Memory, Dreaming Logs
- Confidence levels (high/medium/speculative/deprecated)
- Archive/deprecate (no hard delete enforced)
- SQLite index

#### M7 — Bitget Adapter
- Native HMAC-SHA256 auth (no ccxt dependency)
- Demo mode header support (paptrading: 1)
- Spot + USDT futures market data
- Account/balance/ticker/candle/order endpoints

#### M8 — Risk Engine + Paper Broker
- Hard limits (immutable to agents)
- 7+ risk checks (symbol, market, live state, permission, position size, etc.)
- Paper broker with simulated fills
- Execution gateway (full pipeline)

#### M9 — Strategy Lifecycle
- 10 lifecycle states (draft -> live_limited -> retired)
- Forward-only transitions enforced
- Promotion gates for live candidacy

#### M10 — Telegram Light Adapter
- /status /pause /resume /latest /help commands
- Allowed chat IDs enforcement
- Audit events for all actions

#### M11 — Live-Limited Gated Path
- live_disabled by default
- Explicit human confirmation required for unlock
- Risk engine blocks all live orders when disabled
- Lock/unlock via API and dashboard

### Test Coverage
- 84 tests passing (unit + integration + security)
- Config, secrets, permissions, risk, lifecycle, API, gateway, telegram
