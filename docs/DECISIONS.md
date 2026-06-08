# Decisions

## 2026-06-08 - InstrumentId: three-part unique instrument identity

### Context

Multiple bugs traced back to symbol ambiguity: Binance BTC/USDT spot and Bybit BTC/USDT spot are different data sources but were stored with the same key. Spot and perpetual for the same symbol have different price series but shared the same canonical string. `paper_trader` silently dropped all opportunities because config stored Bitget format (`BTCUSDT`) while opportunities stored canonical (`BTC/USDT`).

Reference systems (OpenAlice, Nautilus Trader) all use a three-part instrument identity: venue/exchange + symbol + market type.

### Decision

Introduce `InstrumentId` frozen dataclass in `aletheia/data/symbols.py`:
```
exchange : str   # CCXT exchange id: "binance", "bybit", "bitget"
symbol   : str   # canonical CCXT: "BTC/USDT"
market_type: str # "spot" | "perp" | "futures"
```
String form: `"binance:BTC/USDT:spot"`. Parsed via `InstrumentId.parse()`.

Config gains `market_data.observed_instruments: list[str]` (e.g. `["binance:BTC/USDT:spot"]`). Legacy `observed_symbols` still works as fallback when the new field is empty.

Storage: `market_snapshots` and `market_ohlcv` tables gain `exchange TEXT` column (additive migration, zero downtime).

The entire pipeline — `CCXTProvider` → `collect_market_snapshots` → `market_observer` → `Opportunity` → `paper_trader` — now uses canonical `BTC/USDT` throughout. Only the risk engine and order router convert to Bitget format (`BTCUSDT`) at the execution boundary.

### Rationale for `exchange:symbol:market_type` vs `exchange:symbol` alone

Spot and perpetual of the same coin have different price series, funding rates, and risk profiles. A perp needs CCXT symbol `BTC/USDT:USDT` (settle currency suffix) while spot needs `BTC/USDT`. Encoding market_type in the identifier makes the CCXT call unambiguous without any inference.

### Consequences

- `aletheia/data/symbols.py` — `InstrumentId`, `to_ccxt_perp()`, `to_canonical()` strips perp suffix
- `aletheia/config/schema.py` — `MarketDataConfig.observed_instruments`
- `aletheia/trading/instruments.py` — `observed_instruments_from_config()`, `observed_symbols_from_config()`
- `aletheia/data/ccxt_provider.py` — accepts `InstrumentId` or `str`; `_resolve_ccxt_symbol()` returns `(ccxt_sym, canonical)` tuple
- `aletheia/storage/models.py` + `migrations.py` — `exchange` column on `market_snapshots`, `market_ohlcv`
- `aletheia/storage/repositories.py` — `exchange=` param on snapshot/OHLCV queries
- `aletheia/server/routes/data.py` — `/market/ohlcv` and `/market/snapshots` accept `exchange=` and `market_type=`
- `aletheia/server/routes/settings.py` — `/settings/symbols` syncs `observed_instruments`; `GET /settings` exposes `market_data` block
- `frontend/src/api/client.ts` — `getOhlcv()` uses `URLSearchParams` to encode `BTC/USDT`
- `frontend/src/pages/Settings.tsx` — read-only "Observed instruments" chips in SymbolsCard
- `tests/test_instrument_id.py` — 45 new tests
- `tests/test_entry_logic.py` — fixtures changed to canonical format

---

## 2026-06-08 - Post-MVP scope expansion: multi-asset observation + Polymarket

### Context

Implementation guide §3.3 originally listed Polymarket as "roadmap only" under MVP constraints. During the full-market-analyzer session (2026-06-08), the user confirmed the system is no longer in MVP state and explicitly authorized expanding scope to include:

- Additional crypto symbols beyond BTC/ETH/SOL (observation + trading)
- US equity observation (yFinance, paper-observation only)
- Polymarket prediction market observation (Gamma API, observation only)
- Roadmap: additional exchanges (xStocks, etc.)

### Decision

**Option A selected by user.** Retain `prediction_observer.py` and `polymarket_provider.py` as shipped. The user's explicit authorization in this turn satisfies the AGENTS.md exception clause ("unless the user explicitly asks for that exact capability in the current turn"). The §3.3 constraint belongs to MVP, which is now superseded.

### Boundaries preserved

- `asset_class="prediction"` opportunities are observation-only — paper_trader gate confirmed to skip them.
- No Polymarket order placement, no live trading on prediction markets, no credentials stored.
- All new agents remain subject to full AGENTS.md safety invariants (audit logs, permission mode, kill switch).

### Consequences

- `aletheia/agents/prediction_observer.py` — new agent, registered in scheduler (every 30 min)
- `aletheia/data/polymarket_provider.py` — new Gamma API provider
- `aletheia/agents/stock_observer.py` — new US equity observer (Mon–Fri market hours)
- Config expanded: `observed_stocks`, `observed_prediction_markets`
- §3.3 is superseded; `docs/aletheia_trading_os_implementation_guide.md` remains as historical MVP reference, not as a current constraint.

---

## 2026-06-07 - WebUI: lightweight cockpit IA fix, defer 5-zone shell

### Context

User raised two concrete UI pains: (1) sidebar `.brand` and content `.topbar` baselines don't align — visually jarring; (2) account total value / today PnL hidden inside Paper Trades subtab while Overview's 4 panels are all system-health (Risk/Live/Model/Exchange) — operator's most-wanted data is missing from the homepage. User additionally provided `aletheia_web_ui_implementation_plan.md` (the design doc behind the discarded local 5-zone cockpit), asking for a redesign referencing OpenAlice / TradingView / Bloomberg.

### Decision

Take the lightweight slice: adopt the implementation plan's §6.1 information-architecture goals (Cockpit answers operator's first-glance questions, including PnL) without adopting its §3 five-zone shell, §4 global Command Center, or §5 News Intelligence elevation. Specifically:

- Align `.brand` to `.topbar` (52px shared baseline) — pure CSS, ~5 lines.
- Add `<AccountSnapshot/>` (Total Value / Today PnL / Open Positions / Pending Proposals) at the top of Overview, Paper Trades, and Positions pages. Total Value carries an honest "aletheia-managed only · exchange sync pending" caveat per the portfolio-sync-gap (BACKEND_ISSUES §2.3).
- Introduce minimal `MetricTile` shared component (no `EntityCard`/`EvidenceStack`/`ActionBar` etc — those tie to the 5-zone Inspector, deferred).
- Plan archived at `~/.claude/plans/open-alice-tradingview-bloomberg-ai-ui-temporal-sifakis.md`.

### Reasoning

- AGENTS.md priority #4 (UI honesty) is genuinely violated by Overview omitting account state — fixing that is on-charter.
- AGENTS.md "No Uncontrolled Expansion" forbids importing the 5-zone shell wholesale (Right Inspector + Bottom Timeline + global Command Center are exactly the 18 local files this same day's earlier merge decision discarded).
- north_star §7 places us in stage 0 (stable run); large UI shell rework belongs to stage 1+, not now.
- The implementation plan's IA chapter and the shell chapter are separable. Taking IA without shell gives the user the actual win they asked for at ~1% of the work.

### Consequences

- 4 source files touched (`index.css`, `Overview.tsx`, `PaperTrades.tsx`, `Positions.tsx`) + 2 new components (`MetricTile.tsx`, `AccountSnapshot.tsx`); rebuild of `aletheia/dashboard_static/`.
- Total Value caveat permanently documents the data-source gap in the UI itself, in lockstep with BACKEND_ISSUES §2.3 — the caveat removes only after Exchange Account Sync ships.
- Five-zone shell / Right Inspector / global Command Center / News upgrade explicitly deferred to "stage 0 stable run + stage 1 learning quality validated", whichever later.
- 352 tests still passing.

### Alternatives Considered

- Full implementation per `aletheia_web_ui_implementation_plan.md` Milestones 1-7 — rejected: equivalent to reverting today's earlier merge decision and doing what AGENTS.md forbids at the current stage.
- Inspector-only (medium scope) — rejected: still requires reorganizing all 14 pages to feed the inspector; not justified by the two pains user named.

---

## 2026-06-07 - Merge: adopt remote cockpit, discard local 5-zone shell

### Context

Local branch (`9d91b15`/`edb791b`/`02449f0`) and origin (`ab29932`..`eb50f7f`) independently built an "Agentic Trading Cockpit" touching the same frontend files (`App.tsx`, `index.css`, all `pages/*`). Three-way merge was structurally impossible; one side had to win wholesale. Merge `ace780a` chose origin. This entry is the post-hoc review and ratification.

### Decision

Keep `ace780a` as-is. None of the 18 local-only files (`LeftNav`, `RightInspector`, `BottomTimeline`, `CommandCenter`, `InspectorContext`, `shared/*`, consolidated `Portfolio.tsx`, beefier `NewsIntelligence`, `statusColor.ts`, build artefacts) get cherry-picked back.

### Reasoning

- AGENTS.md priority order ranks user-facing capability at #6; UI shell rework belongs there. State consistency, safety, audit, UI honesty (#1–#4) are not improved by porting any of the 18 files.
- AGENTS.md "No Uncontrolled Expansion" forbids large refactors / styling rewrites — porting a 5-zone shell qualifies.
- north_star_references.md §7 places the project in stage 0 (prove stable run); UI revamp is not a stage 0 task.
- The single local file that maps to a real north-star (`pages/Portfolio.tsx`, consolidated three-tab view) does not actually fix `aletheia-portfolio-sync-gap` — its data still comes from local SQLite only. The real fix is a backend Exchange Account Sync layer; building the storefront before the merchandise inverts the order. Filed as a backend issue (BACKEND_ISSUES.md §2.3) with the consolidated page noted as a candidate UI for after the backend lands.
- News Intelligence local variant conflicts with `issue_register.md::ISSUE-008` (news ingestion should be marked experimental, not expanded).

### Consequences

- `frontend/src/` and `aletheia/dashboard_static/` stay byte-identical to origin.
- Discarded local commits remain reachable through `02449f0` for archaeology.
- `docs/AI_HANDOFF.md` Known Issues list is updated to surface the two unfixed north-star gaps that the previous "no remaining open items" line obscured.
- Full audit and 18-file judgement table archived at `docs/superpowers/specs/2026-06-07-merge-postmortem-design.md`.

### Alternatives Considered

- Cherry-pick the consolidated Portfolio page only — rejected: storefront-before-merchandise; data-source gap is in backend.
- Revert the merge and rebuild on local cockpit — rejected: would discard ~60 backend hardening files for a UI preference, inverting AGENTS.md priority order.

---

## 2026-06-07 - Gateway: Pure ctypes for Windows process management

### Context

The gateway watchdog (every 5 min via Task Scheduler) and crash-recovery path spawned external processes (wmic.exe, tasklist.exe, powershell.exe, taskkill.exe) for liveness checks and orphan cleanup. Despite CREATE_NO_WINDOW flags, these console-subsystem executables still occasionally flashed Windows Terminal windows on Win11.

### Decision

Replace all subprocess-based process queries with pure ctypes Win32 API calls (kernel32 + ntdll):
- `_pid_alive`: OpenProcess + NtQueryInformationProcess(ProcessCommandLineInformation=60)
- `_list_processes`: CreateToolhelp32Snapshot + Process32First/Next
- `kill_pid_tree`: TerminateProcess + BFS child traversal

### Reasoning

1. Zero subprocess = zero chance of console window allocation
2. Faster: in-process ctypes calls vs. spawning + waiting on external .exe
3. NtQueryInformationProcess(60) is stable API available since Windows 10 1511
4. Keeps POSIX paths unchanged (no regression risk for future Linux support)

### Consequences

- Windows-specific code isolated in `aletheia/gateway/_win32_proc.py`
- Tests must be Windows-only for ctypes paths (pytest.mark.skipif)
- If targeting Windows < 10 1511 in future (unlikely), would need fallback

### Alternatives Considered

- Keep subprocess with CREATE_NO_WINDOW: still occasionally flashes on Win11
- Windows Service (pywin32/nssm): adds external dependency, overkill for current scope
- Named pipe heartbeat: more complex, doesn't solve orphan discovery

---

## 2026-06-06 - AI Workspace Initialized

### Context

Project needs cross-device AI collaboration support for seamless handoff between machines.

### Decision

Adopted standard AI workspace structure with context recovery and handoff protocols.

### Reasoning

Enables seamless project handoff between devices and AI tools (Claude Code, Codex, etc.).

### Consequences

All AI sessions should follow start/end protocols documented in AGENTS.md.

### Alternatives Considered

None — process improvement, not technical tradeoff.
