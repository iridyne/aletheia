# Decisions

## 2026-06-17 - Self-Evolution Tier 1：4h 作为触发窗口，medium_confidence 不进 FTS

### Context

Universe forward outcomes 有 4 个时间窗口（15m / 1h / 4h / 24h）。需要选一个作为
自动生成 guard_candidate 的触发窗口。同时，guard_candidate 的 confidence 级别决定
它是否会被 LLM 在市场分析时检索到。

### Decision

1. **触发窗口选 4h**：15m/1h 噪声过大，24h 延迟过长，4h 是信噪比最好的窗口。
2. **生成 `medium_confidence`（不进 FTS）**：guard_candidate 是机器自动生成的候选，
   未经人工审核，不应自动进入 LLM 检索池（防止低质量噪声干扰分析）。
   需人工确认后用 `create_trading_guard()` 升级为 `high_confidence` 才可检索。
3. **Tier 1 零 LLM**：所有内容由 `_build_guard_candidate_content()` 程序化生成，
   零额外 token 成本，不依赖 LLM 可用性。

### Reasoning

- 4h 窗口与 paper_trader 的持仓周期匹配，是判断"入场是否合理"的最有意义时间段。
- `medium_confidence` 确保人工是质量关的最终把控者；FTS 只服务高置信知识。
- Tier 1 无 LLM 依赖意味着即使 LLM 提供商不可用，反馈闭环仍然运转。

---

## 2026-06-13 - Dev 栈统一到 single-port :8787,移除独立 Vite dev server

### Context

此前本地开发跑两个端点:Vite dev server(:5173,HMR)+ gateway(:8787,API + 生产静态)。
两端口导致 dev 与生产形态分叉:dev 下前端走 Vite proxy、生产下走 gateway 的 StaticFiles,
路径/CORS/资源解析行为不一致,且 SSE 触发的 `static 404` bug(`ba06f5c`)正源于此分叉。

### Decision

dev 与生产**统一为单端点 :8787**。dev.ps1 不再起 Vite dev server;改用
`vite build --watch`(产物落 `aletheia/dashboard_static/`)+ gateway StaticFiles 服务,
backend `uvicorn --reload` 提供后端热重载,SSE(`frontend-built` 事件)驱动浏览器自动刷新。

### Reasoning

- dev 形态 ≡ 生产形态:同一个 gateway 服务 API + 静态,消除 proxy/StaticFiles 行为分叉。
- 单端点 = 单一可信地址,符合项目 "localhost 单控制面" 的定位(CLAUDE.md frontend 段)。
- Starlette StaticFiles 每请求 `os.stat()`,无目录缓存,故前端 rebuild 无需重启 worker——
  SSE 事件单独触发浏览器刷新即可(`cbbab00` 据此移除了多余的 worker restart)。

### Consequences

- 所有 dev-reload 能力门控 `ALETHEIA_DEV_RELOAD=1`(仅 dev.ps1 设)+ `live_disabled`;
  生产模式 supervisor 崩溃保护不受影响。
- UI 迭代统一用 `scripts/dev.ps1`,**不要**在紧循环里手动 rebuild `dashboard_static/`
  (已写入 CLAUDE.md frontend 规则)。

### Alternatives Considered

- 保留双端口 + 修 proxy 配置:治标,dev/prod 分叉根因仍在。
- 纯 HMR(不落静态):dev 与生产形态进一步分叉,且 gateway 需感知 Vite 内部协议,耦合加重。

---

## 2026-06-12 - 修订 trust §1.1:外部仓位可经"合成诚实因果链"纳入本地账本(adopted)

### Context

Aletheia 原先只管理**自己开的**仓位(proposal → risk → order → position 完整因果链)。
交易所账户上**先于 Aletheia 存在**或在系统外开出的仓位,对管理层完全不可见:`exchange_sync`
只读地把它们拉进缓存,仅向 UI 暴露一个 drift *计数*。当前 demo 账户就坐实了这个缺口——
2 个外部仓位(BTC 空、SOL 空 0.2)持续产生浮亏,却没有止损、没有策略、无人管理。

项目所有者(2026-06-12)做出明确立场决策:**Aletheia 一旦启动接管账户,人就很难再与 AI
并行管理仓位,因此账户上的全部仓位(含外部仓位)都应统一交由 AI agent 管理**("接管即纳管")。

这与旧的 trust §1.1 铁律直接冲突——原铁律规定"外部仓位**永不**写入本地 positions 表,因为
它们没有 proposal 因果链回链,会破坏因果链诚实性",并有一条红线测试
`test_external_positions_not_written_to_local_table` 强制执行。

### Decision

修订 trust §1.1:外部仓位**可以**进入本地 `positions` 表,但**必须**经由一条
**合成但诚实**的因果链被"纳管(adopt)":

1. 新增 `source` 列(`positions` / `orders` / `trade_proposals`),取值 `native`(Aletheia
   正常开出)或 `adopted`(reconciler 纳管外部仓位时合成)。已有行一律 `native`。
2. `position_reconciler.adopt_position()` 在单个 `transaction()` 内合成
   proposal → order(filled) → position,全部 `source="adopted"`,position 的
   `proposal_id`/`order_id` 回链非空,入场价/数量取自交易所(真相),不伪造。
3. 合成的 proposal/order 语义上断言的是**"Aletheia 纳管了一个既存仓位"**,**不是**
   "Aletheia 决定开这笔交易"——`source=adopted` 与 rationale 文案显式说明这一点。
4. 纳管仓位的 `execution_mode` 由账户模式映射:demo → `bitget_demo`,live →
   `bitget_live_limited`。这一个映射点同时决定了管理策略:`position_manager` 自动管理
   `bitget_demo`、**跳过** `bitget_live_limited`——**live 仍是人工专属,由架构强制保证**。
5. 红线测试 `test_external_positions_not_written_to_local_table` 改写为
   `test_adopted_positions_carry_synthetic_chain`:断言纳管行带 `source="adopted"` 且
   回链非空(从"禁止入表"改为"入表必须带诚实链")。

### Reasoning

- 并行的人+AI 仓位管理在实践中不可行;单一权威比双头管理更安全(符合优先级
  State consistency > Safety)。
- 旧铁律的**目的**是保持因果链诚实——而非永久禁止纳管。合成诚实因果链同时满足两者:
  外部仓位进入管理,且每个仓位仍有完整、如实标注的链,可审计。
- demo 自动 / live 人工 的拆分不靠散落的 if 判断,而由 `execution_mode` 一处如实设置
  后被既有的 `position_manager` 守卫(`_PAPER_MODES`)免费强制执行,改动面最小。
- live 真实资金的纳管与平仓**始终需人工确认**,当前绝不自动;但架构预留了清晰的、
  默认关闭的接缝,未来 `Full` 模式 + live-unlock 下可由顶层 LLM agents 接管 live(P4)。

### 补充决策(2026-06-12):demo 自动纳管任务 **默认开启**

项目所有者明确要求:Phase 3 的 demo 自动纳管定时任务 **默认开启**(`auto_adopt_enabled`
默认 `true`),而非默认关闭后手动启用。理由:所有者的立场是"接管即纳管,全账户统一交由
AI 管理";若默认关闭,外部仓位会被静默地继续无人管理,正好违背初衷。该开关只作为
**关闭行为的 kill-switch 存在**,不是"选择性加入"的开关。

> ⚠️ **行为警告(务必知晓)**:此任务默认开启意味着——一旦 gateway 在 demo 模式下启动,
> 账户上**任何系统外的 demo 仓位都会被自动纳管**,随即 `position_manager` 会按
> `cfg.trading.default_stop_loss_pct` / `default_take_profit_pct` 给它们加默认止损/止盈,
> 并可能据此**自动平仓**(平仓会下真实 demo 单)。这是"接管即纳管"的预期行为,但它是系统
> 第一次对真实 demo 仓位**主动下手**而非仅观察。Live 仓位不受影响(始终人工确认)。
> 要临时停用,把 `auto_adopt_enabled` 设为 `false`。



### Context

用户在 `worktree-dev` 上明确要求继续推进 **Pi Agent / Operator Layer** 的完整设计,并要求
不要漏掉外部参考文件 `C:\Users\Administrator\Downloads\aletheia_pi_agent_implementation_plan.md`
里的任何关键内容。与此同时,Phase 0 的 `72h` 观察窗继续在另一台观测机上运行,本 checkout 的职责
是**并行设计与原型准备**,不是拿 branch 进展冒充 observation gate 达标。

repo 现状也决定了不能继续含糊:

- 现有 `aletheia/agent_shell/` 已存在,但它只是**单回合 request-response runtime**,不是常驻
  PI-style operator runtime
- `frontend/src/components/CommandPalette.tsx` 已有 `Ctrl+K` 入口,但它只是弹窗,不是完整
  Command Center 工作面
- 当前全局权限仍是 `Observe / Paper / Full`,与参考设计要求的
  `ReadOnly / Research / PaperAuto / AllowEditing / Auto` 不兼容
- 安全边界要求任何顶层 operator 都不能直接触碰 live、credentials、hard risk、execution gateway

因此必须先把最终设计方向钉死,避免后续实现沿着两条互斥路线摇摆:

1. 继续把现有 `/api/command` 与 Python `agent_shell` 堆成单进程大壳
2. 采用双进程,把 PI-style 常驻 runtime 与 trading truth layer 分开

### Decision

Operator Layer 采用 **dual-process** 架构:

1. **Python gateway 保持权威真相层**
   - 继续拥有交易状态真相、命令执行权、审计写入权、配置真相、SQLite 持久化权
   - 一切会改动系统状态的 Operator 命令,仍由 Python backend 校验并执行

2. **新增 Node/TypeScript `Operator Shell`**
   - 负责 PI-style 常驻 session runtime、operator loop、任务编排、修复建议、推荐生成
   - 通过 loopback API 与 Python gateway 通信
   - 不直接写 `app.db`,不直接调交易执行,不直接碰 secrets / live / hard risk

3. **交互形态固定**
   - `Ctrl+K` 仅保留为 launcher / quick ask
   - 真正的主产品面是独立的 **Command Center 页面**

4. **权限拆层固定**
   - 保留现有 worker/trading 权限语义
   - 新增独立 `operator.permission_mode`
   - Canonical operator modes: `ReadOnly / Research / PaperAuto / AllowEditing / Auto`

5. **现有 `aletheia/agent_shell` 不推倒重来**
   - 保留并重构定位为 Python 侧 operator substrate / compatibility layer
   - 它不再被视为最终的 Apex Agent Shell

### Reasoning

- 这条路线是外部参考设计与当前 repo 真实结构之间唯一不自相矛盾的落点:
  - PI-style 常驻 runtime 需要自己的服务边界
  - Aletheia 的 trading truth、risk、execution、audit 又必须继续集中在 Python backend
- 若继续走 Python 单进程大壳:
  - 会把 reasoning runtime、command execution、state truth 混进同一层
  - 更难保持安全边界清晰
  - 也更偏离参考 PI 设计的本质
- 若让 Node shell 直接写 DB 或直接执行交易:
  - 会破坏现有后端对交易/风控/审计的权威性
  - 也会把 Windows 本地多进程 SQLite 写入风险放大
- Command Center 必须独立成页,因为参考设计要求持久会话、trace、inspector、recommendation
  等完整 operator workspace,这不是 `Ctrl+K` 弹窗能承载的

### Consequences

- 本地设计 source of truth 收口为:
  - `docs/OPERATOR_LAYER_DESIGN.md` = 概念层
  - `docs/superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md` = repo-specific 双进程蓝图
  - `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md` = 参考文件覆盖矩阵
- 后续 implementation plan 必须按 dual-process 拆 Track 0-9,不能回退成单进程 quick fix
- `CURRENT_TASK.md` / `ROADMAP.md` / `AI_HANDOFF.md` / `PIPELINE_STATUS.md` 都应引用上述本地设计源
- 这条决策**不等于**允许立即合并或上线;上线仍受 `Phase 0 门全绿 + 显式授权` 约束

### Alternatives Considered

- **继续扩写 Python-only `aletheia/agent_shell`**
  - rejected: 会把 operator runtime 与 trading truth layer 混在一处,且偏离参考 PI 设计

- **完全外置独立 daemon,前端直接对接**
  - rejected: 会削弱 gateway 作为本地产品控制平面的地位,也更难保证 dashboard honesty

- **把 `Ctrl+K` 弹窗继续升级成主表面**
  - rejected: 只能承载 quick command / quick ask,承载不了 operator workspace

### Supersedes / Clarifies

- **Clarifies** `2026-06-07 - WebUI: lightweight cockpit IA fix, defer 5-zone shell`
  - 该决策针对当时主分支 UI 范围控制,仍然成立
  - 但其“global Command Center deferred”现在被限定为:
    - 主线合并/上线仍 gated
    - `worktree-dev` 上的 Operator Layer **设计与实现准备**已获明确授权,可以继续推进

---

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
