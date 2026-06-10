# Decisions

## 2026-06-10 - CLI 分层 + 运营命令软弃用(人类交互收口到 Web)

### Context

人类交互散落在两个面:CLI(Typer)与 Web dashboard。多数 CLI 子命令已有等价的
`/api/*` 端点。提出"是否完全去掉 CLI、把人类交互全部走 Web UI"。

排雷发现一个**鸡生蛋的硬约束**:Web UI 由 gateway **进程**伺服,进程没起就没有 UI
——无法用"还不存在的 UI"去启动那个进程。且 Windows 看门狗任务的命令体写死
`aletheia.main gateway start --no-browser`,`doctor` 最需要的时刻恰是 gateway 挂掉、
UI 够不着时。这两类命令天然属于进程外的命令行。

另一处雷:`doctor` 的 blocker/warn 修复提示(fix_hint)写死了 `aletheia risk resume`、
`aletheia secrets set`、`aletheia model set`——若删命令而不改提示,提示即成谎话
(正是本项目反复对抗的"记录-事实不符")。

### Decision

CLI 不去掉,而是**分三层**,人类日常交互收口到 Web:

1. **Bootstrap 内核(保留,Web 无法替代)**:`gateway start/restart/enable-autostart`、
   `doctor`、`version`。"让机器活起来 + 看它活没活"的最小集。
2. **运维/调试工具(保留,无 Web 平价)**:`bitget sync`、`knowledge reindex`、
   `knowledge search`。
3. **运营命令(软弃用,已有 Web 平价)**:`permission`/`model`/`risk`/`strategy`/
   `update` 整组 + `secrets`(set/list/test)+ `bitget test`。

**软弃用 = 命令照常工作,仅在运行时往 stderr 打一行指向 Web 位置的提示**
(`cli/_deprecated.py::warn_deprecated`);stdout 不变,脚本与现有测试零影响。

### Reasoning

- 选软弃用而非硬删:零回退风险、零功能损失、保留逃生口。硬删要同步删测试 +
  清文档 + 改 doctor 提示,改动面大且与"逐步去掉"的渐进意图相悖。
- 提示走 stderr 而非 stdout:保证 538 测试(断言 stdout/exit_code)原样通过,
  且管道/脚本不被污染。
- doctor fix_hint 同步改为"Web 首选、CLI 备选",维持指引指向被维护的面。

### Consequences

- 将来某运营命令的 Web 路径用顺手、确认无依赖后,物理删除只剩"删代码 + 删对应
  测试"两步,无连带风险。
- 新增运营功能应优先(或仅)做 Web 端;CLI 侧不再扩充运营命令。
- Bootstrap 内核(`gateway start` + `doctor`)是**不可删**的——删它会切断刚稳定的
  无人值守复活链(看门狗依赖 `gateway start`)。

### Alternatives Considered

- **完全去掉 CLI**:被鸡生蛋约束否决——UI 架在 gateway 进程上,无进程即无 UI;
  看门狗也依赖 `gateway start`。
- **立即硬删运营命令**:回退成本高、改动面大,与渐进意图相悖;留作将来一次性收尾。

---

## 2026-06-09 - Bitget demo wiring: empirical probe results (STEP 0)

### Context

Connecting paper trading to Bitget's real demo/simulated API (instead of local paper_broker). Web docs were unreachable (403/Cloudflare), so the v2 demo mechanism was determined by placing real minimal demo orders and inspecting raw responses (`scripts/_probe_*.py`, since deleted).

### Findings (all empirically verified against the live demo account)

1. **Outcome A confirmed**: demo uses `productType=USDT-FUTURES` + `paptrading:1` header. The S-prefixed `SUSDT-FUTURES` is **rejected** (`40778: does not support SUSDT currency as margin`). → No productType/symbol mapping needed; the planned STEP 5 is dropped.
2. **Demo account is `hedge_mode`** (two-way positions), margin `crossed`, ~198 USDT available.
3. **Mix v2 order body requires fields the current `client.py:211-218` omits** — this is a latent bug in `execute_live_order`'s path that was never caught because every live test mocks `place_order`:
   - `marginCoin: "USDT"` (omitting → `400172 Margin Coin cannot be empty`)
   - **Open**: `tradeSide: "open"` + `side: "buy"|"sell"` (omitting tradeSide in hedge mode → `40774 ... unilateral position type`)
   - **Close**: `tradeSide: "close"` + `holdSide: "long"|"short"` + `side` SAME as the open side (close a long = `side:"buy", tradeSide:"close", holdSide:"long"`). `reduceOnly` is NOT the hedge-mode close mechanism; `tradeSide:close`+`holdSide` is. Omitting holdSide → `22002 No position to close`.
4. **Market orders fill synchronously**: immediately after place-order, `GET /mix/order/detail?orderId=...` returns `state=filled, baseVolume, priceAvg`. → STEP 4 can parse fill status synchronously from a follow-up detail query; STEP 7 reconciliation does NOT need to be extended for demo.

### Decision

- `client.place_order` (mix branch) must add `marginCoin` + `tradeSide:"open"`, and after submit, query order/detail to resolve `filled` + `priceAvg` synchronously (don't fabricate).
- A hedge-mode close needs `tradeSide:"close"` + `holdSide` — the demo close helper (STEP 8) and any close path must pass these.
- STEP 5 (S-prefix) is dropped. STEP 7 reconciliation change is dropped (sync fill).
- The hedge-mode order-shape fix lives in change-controlled `client.py`; it also fixes the never-exercised live path.

### Implementation outcome (2026-06-09, all 9 steps, 514 tests green — UNCOMMITTED per observation no-commit rule)

- `client.place_order` (mix): adds `marginCoin:"USDT"` + `tradeSide:"open"` (or `tradeSide:"close"`+`holdSide` when `OrderRequest.hold_side` is set), and after submit queries `/mix/order/detail` to resolve real `fill_price`/`filled_quantity` synchronously. Never fabricates a fill (stays `submitted` if detail unavailable).
- `bitget_broker.execute_live_order` generalized with `execution_mode`/`fee_rate` params; `_find_open_position` parameterized by mode. New `close_demo_position()` places a real hedge-mode close then realizes PnL via `pnl.close_position` using the exchange's confirmed exit price; refuses to close locally if the exchange call fails (books stay consistent).
- `gateway._execute_proposal`: split `bitget_demo` into its own branch → broker (no `live_disabled` guard), audit `demo_order_executed`/`demo_order_error`.
- `risk/engine`: added symmetric `account_mode_mismatch:order=demo` (demo order requires `exchange.mode==demo`) + a looser demo data-freshness gate (`stale_market_data_demo`, 1800s vs live 120s, since data collects every 5 min). All other live-only checks stay live-only; `_is_live` untouched.
- `position_manager`: `bitget_demo` exits route to `close_demo_position` (real close); `paper_internal` still local.
- **Three latent bugs found + fixed**, all masked because every live test mocks `place_order`: (1) the mix body omitted `marginCoin`/`tradeSide`; (2) the live path never converted canonical `BTC/USDT` → Bitget `BTCUSDT` (now done at the exchange boundary, local rows stay canonical); (3) the broker recorded the *requested* price as the fill price instead of the exchange's confirmed `priceAvg`.
- Safety: `tests/conftest.py` autouse guard makes `place_order` raise in any unmocked test (keyring creds are NOT sandboxed by the `tmp_path` DATA_DIR patch, so an unmocked demo order would otherwise hit the real API during pytest). Existing paper_trader tests pinned to `paper_internal` (they validate the fallback path); demo path covered by `tests/test_demo_trading.py` (10 tests, all mocked).

### Live end-to-end verification on real Bitget demo (2026-06-09, user-authorized one round-trip)

Called the broker directly (NOT the gateway — kill switch / autonomous loop untouched), min size, opened+closed in one run (net-flat), local rows in a throwaway DB:
- **Open**: real demo order `1448153278595629057`, filled @ **62846.5** — the price came from the exchange's confirmed `priceAvg` (order/detail), not the request. Local order tagged `execution_mode=bitget_demo`, symbol stored canonical `BTC/USDT`, exchange call used `BTCUSDT`.
- Exchange `get_positions()` confirmed `BTCUSDT long 0.0001` appeared.
- **Close**: real hedge-mode close `1448153285251989505` (`tradeSide:close`+`holdSide:long`, `side:buy`), exit 62841.7, realized −0.008 USDT.
- Exchange went **flat** (no dangling position); local round-trip recorded `status=closed`.

Confirms the full path works against the real demo exchange and the three latent-bug fixes are correct end-to-end. Demo account ~198 USDT, BTCUSDT minTradeNum 0.0001.

---

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
