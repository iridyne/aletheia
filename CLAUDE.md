# Aletheia Trading OS

## Project Overview

AI-native agentic trading system. Local-first, file-based "Trading-as-Git" platform.
- **Target users**: Solo trader (project owner) running automated trading strategies
- **Core function**: 7 AI agents orchestrate market analysis, strategy generation, risk management, and order execution on Bitget
- **Stage**: MVP — working prototype, needs trust hardening, not feature expansion

## Architecture

- **Data flow**: Market data → Agent analysis → Strategy proposal → Risk Engine approval → Execution Gateway → Bitget API
- **External deps**: Bitget API (spot/futures), LLM providers (OpenAI-compatible, Anthropic-compatible)
- **Frontend**: `frontend/` — React/Vite dashboard, single endpoint `:8787`. Dev iteration: `scripts/dev.ps1` (Vite watch + backend watcher). Do NOT rebuild `aletheia/dashboard_static/` manually in a tight loop.
- **Tests**: `tests/` — 515+ tests. See **Testing Strategy** below.

## Development Rules

- Smallest change principle — MVP, not greenfield
- No uncontrolled expansion: no new exchanges, no Docker/Postgres/Redis, no vector search
- **EXPERIMENT FREEZE —— 已于 2026-06-27 由 owner 决定提前结束/解除**: Round 1 因实验有效性塌方(干净窗 PF≈0.06、方向集中 short 17/23、唯一活学习闭环喂幻觉)提前结束;**冻结约束已解除**,原 AI 决策链代码现可修改。当前处于「Round 1 后全量修复」阶段(见 `docs/plans/round2_week1_decision_chain_fixes.md` + `docs/issues/` master ledger)。原冻结路径(`agent_shell/trading_context.py`, `agent_shell/trading_decision.py`, `agents/market_observer.py`, `agents/paper_trader*.py`, `agents/pi_agent*`, `strategy/`, `learning/`)仍是高敏感决策链:修改时**必须**补/更新对应 `tests/specs/spec_*.py`、走 SDD 流程、改后 `aletheia doctor` 验证。详见 `docs/product/EXPERIMENT_PROTOCOL.md`。
- Trust boundaries (change-controlled — touch only for tasks that explicitly require it):
  `aletheia/risk/`, `aletheia/execution/`, `exchanges/bitget/auth.py`, `exchanges/bitget/client.py`,
  `config/secrets.py`, `agent_runtime/permissions.py`, `server/routes/live.py`
- Trading state invariants: Orders flow through Execution Gateway only; live trading disabled by default
- Market data: always go through `get_market_data_provider()` (`aletheia/data/ccxt_provider.py`) — never import `BitgetClient` for market data (execution-context only; REST v2 rejects canonical `BTC/USDT`). Providers are per-call, never cached (CCXT binds aiohttp to the creating event loop). Only exception: Bitget-only ON-series xStocks, which construct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly (see comments at call sites).
- Symbol format: canonical `BTC/USDT` everywhere internally (storage, risk, PnL, opportunities); `BTCUSDT` exists ONLY at the execution adapter boundary via `to_bitget()`. Converters/`InstrumentId` live in `aletheia/data/symbols.py`. A mismatch silently drops opportunities at `paper_trader`'s filter.
- Symbol DISPLAY rule (frontend, immutable owner rule 2026-06-26): the slash IS the spot-vs-derivative signal and must never contradict the instrument type — **perp/futures → no slash (`BTCUSDT`); spot → slash (`BTC/USDT`)**. Internally everything is stored canonical (with slash), so display is a pure transform: every symbol render in the frontend MUST go through `fmtSymbol(symbol, marketType)` in `frontend/src/lib/format.ts` (strips the slash only when `isPerpMarket(marketType)`; spot/unknown keeps the canonical slash — never guess). Pass the row's `market_type` (Position/Order/TradeProposal/MarketSnapshot), `market` (Opportunity), or the board's `market_type` (Universe). Exemptions: bare research/news tickers without a quote (e.g. `BTC`), user-entered symbol-filter chips, fixed ticker labels. Adding a new symbol display without `fmtSymbol` is a regression.
- SQLite concurrency (do not break): Three pillars must all hold — (1) WAL + `busy_timeout` (`storage/sqlite.py`); (2) one connection per thread via `threading.local` (never cache a connection across threads); (3) `was_in_tx = db.in_transaction` captured **before** every write in `repositories.py`, committing only `if not was_in_tx` — sqlite3 auto-opens a tx on INSERT/UPDATE/DELETE, so checking *after* the write never commits and hangs the WAL lock (the historical `database is locked` bug). Scheduler capped at `max_workers=3`; don't raise it.
- Config ↔ UI rule: every new field in `aletheia/config/schema.py` MUST have a corresponding control in the Web frontend settings page. Users never edit `data/config.yaml` directly.
- WebUI static build: `aletheia/dashboard_static/` is gitignored (build artifact, not tracked). After frontend changes run `cd frontend && npm run build`. On a fresh clone or new machine, run this once before starting the gateway. Gateway startup fails with a clear message if static files are missing.
- GitHub Actions failures: disable the broken workflow to stop alerts; do not archive the repo.
- Priority: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy

## API 性能规则（血泪教训 2026-07-02 — 强制执行）

`async def` handler 里的同步阻塞操作（磁盘读、`threading.Lock`、SQLite）会冻住整个 uvicorn event loop，等效于 `time.sleep(N)`。FastAPI 只对 `sync def` 自动 threadpool 保护，`async def` 是开发者的责任。根因案例：`GET /api/settings` 因 `load_config()` 零缓存，冷路径实测 **30 秒**（backend 直连 :8788），修复（5s TTL 缓存）后降至 <50ms。

**写新 handler 前必须确认以下四条：**

1. **热路径读必须有缓存**：每次请求都要读的数据（config、health threshold、retention 配置等）必须加 TTL 内存缓存（5–60 s）+ 写入时主动失效。不加缓存的 `load_config()` / `yaml.safe_load()` / `model_validate()` 禁止出现在 async handler 热路径。
2. **初始化操作只跑一次**：`ensure_data_dirs()` 类"首次运行才需要"的操作必须用 flag 保护，不得在每次写操作中重复执行（35 次 mkdir 系统调用 × 每次保存 = 累积延迟）。
3. **新端点上线前直连实测**：`Measure-Command { Invoke-WebRequest -Uri 'http://127.0.0.1:8788/api/<endpoint>' -UseBasicParsing }` — 直连后端（:8788），绕过 frontdoor 缓存。冷路径 >100ms 必须有解释，>500ms 必须修。
4. **UI 轮询间隔下限**：健康探针（exchange 连接测试）≥ 60s；状态轮询（status/kill-switch）≥ 10s 且 handler 必须是纯内存操作（<5ms）；LLM 相关 ≥ 30s。Settings 页面打开时并发轮询数不超过 8 个。

> 完整根因分析 + 已知遗留慢点 + 快速检查清单：`docs/_ai/ASYNC_PERFORMANCE_LESSONS.md`

## Testing Strategy

全量测试 ~5min（515+ tests，I/O-bound，SQLite setup/teardown 是瓶颈，并行无收益）。

- **日常**：`uv run pytest tests/test_xxx.py -q`（秒级）；针对性用 `-k "knowledge or dreaming"`
- **提交前**：后台跑全量，不阻塞工作流
- **全量命令**：`uv run pytest tests/ -q --tb=line`

不等全量完成再继续工作——后台跑，有失败再回头修。

## Gateway 进程管理

三进程架构（supervisor → frontdoor:8787 + backend:8788），`lifecycle.py` 统管。不要手动 kill，用 `uv run aletheia gateway <restart|stop|start|status|logs>`。代码改动后必须 restart 才能生效。`--no-browser` 避免弹浏览器。Task Scheduler 看门狗每 5 分钟自动拉起。

## Repo Setup

**`aletheia/data/` is the source package** (symbols.py, ccxt_provider.py, etc.) — distinct from repo-root `data/` runtime dir (gitignored). `.gitignore` must keep the runtime rule root-anchored as `/data/`; a bare `data/` silently excludes the source package from git. After cloning verify: `uv run python -c "import aletheia.risk.engine"`.

## AI Collaboration Rules

**面向 owner 汇报用大白话，不甩黑话（强制 — 血泪教训 2026-07-02）**：owner 是单人 trader，不是读代码的工程师同事。对话汇报 / handoff 摘要 / 状态说明里**禁止甩内部代号和术语**——"契约 / spec / claim / bucket / signal_source / trade_school / LP-S07"这类词对他是噪音，只会抬高认知与判断压力，反复出现会让他"渐行渐远、听不出是哪个模块出了问题"。规则：①**先讲人话结论**——"哪个模块、什么后果、现在什么状态"，再展开细节；②内部概念用生活化翻译("契约文档"=某模块的**说明书**：它该怎么跑、哪些红线不能碰；"spec/claim"=自动检查项；"bucket"=分档)；③不得已要用代号，首次出现紧跟一句人话解释，能不用就不用；④**别因为"任务本身很技术"(写文档/审代码)就默认可以甩术语——汇报口径和任务技术度无关**。唯一例外：写进代码 / docs / spec 的技术措辞照常严谨。

意图/事实分离：git 同步**意图**（代码+文档），绝不写运行态事实（gateway 进程/app.db/config.yaml/凭证）进任何 git 文档——在另一台机器上必然过期。详见 `docs/_ai/DEV_PROTOCOL.md`。

会话开始：`git pull --ff-only` → `aletheia doctor` → 读 CLAUDE.md / AGENTS.md / CURRENT_TASK.md / AI_HANDOFF.md。
会话结束：更新 `docs/_ai/AI_HANDOFF.md`、`DEVLOG.md`、`CURRENT_TASK.md`（只写计划/意图，不写运行态）。架构决策记入 `docs/_ai/DECISIONS.md`。

Issue 分工：`docs/issues/BACKEND_ISSUES.md`（bugs/contract drift/dead code，带 file:line）；`docs/issues/FRONTEND_ISSUES.md`（UI spec gaps/honesty breaches）。

## 并发会话 / 多 Workspace 开发（强制 — 血泪教训 2026-07-01）

**前提**：owner 会同时开多个 workspace/会话在同一 repo 上干活。这是常态，不是异常。目标不是"别并发"，而是**在并发下仍不互相破坏**。根问题：多个会话共享**同一个 checkout（工作树 + HEAD + index）**时，一方 `git checkout` 会改变另一方看到的当前分支——曾导致"我以为在 fix 分支、实际在 master"，差点在 master 上误 commit+push。

**隔离优先（首选适应性举措）**：
- **每个并发会话在自己的 `git worktree` 里干活**（`.claude/worktrees/<name>/`，本 repo 已在用）。worktree = 独立目录 + 独立 HEAD + 独立工作树 + 独立 `dashboard_static/` build 产物，从物理上消除分支/暂存/build 冲突。**不要多个会话在主 checkout 上并行切分支**。
- 注意 worktree 陷阱：worktree 的 `data/` 为空，restart gateway 会让交易系统空跑；前端上线必须回主仓 FF-merge（见记忆 worktree-gateway-empty-data-trap）。

**若确实在共享 checkout 上操作（无法隔离时）**：
1. **每次 commit/push/merge/checkout 前**先 `git branch --show-current` + `git status -sb` 重核当前分支与领先/落后，**别信记忆或上文快照**（HEAD 是共享状态，随时被别的会话切走）。
2. **固化工作用 plumbing，不切 HEAD**：`TREE=$(git write-tree); C=$(git commit-tree $TREE -p HEAD -F - <<'MSG'…MSG); git branch <name> $C`。创建 commit+分支引用但不动 HEAD/工作树/index，对并发会话零干扰；远比 `checkout -b`（会被并发切换干扰）可靠。
3. **只 `git add` 明确文件**，禁 `git add -A`。提交后 `git show --stat <commit>` 核实文件数对得上——**工具输出可能串入别的会话的 git 结果**（曾出现假的"2 files changed"成功回显，实为并发会话的 commit）；数字对不上立即核实，别向 owner 谎报成功。
4. **不轻易 `reset --hard` / `checkout`**（会覆盖别的会话正在编辑的工作树）。冲突时优先保护对方改动，不强推。分叉时用 `git merge` 造 merge commit 调和，别 rebase/force。
5. **前端 build 产物陷阱**：`dashboard_static/` 是 gitignore 的 build 产物、不随 git 走；多会话切分支会把它搅成半成品 → **页面能加载但黑屏**（JS chunk 404，React 崩溃）。诊断：`curl --noproxy '*' http://127.0.0.1:8787/` 看引用的 `/assets/*.js` 是否全 200；修复：`cd frontend && npm run build`。
6. **gateway 是全局单例**（不随 worktree 隔离，`.claude/worktrees/` 共用主仓 gateway）；别在多个会话/worktree 同时 restart。

## Spec-Driven Development (SDD)

Spec as Code：L2 契约文档的关键断言已编码为 `tests/specs/spec_*.py` 中的可执行测试。

**开发流程**：
1. 新功能先在对应 `spec_*.py` 中声明 `SpecClaim(status="missing")` → 写 L2a 测试（RED）→ 实现代码 → GREEN → 补 L2b/L2c
2. 修改 risk/execution/trust 模块前，先检查 `tests/specs/` 中是否有 claim 会被影响
3. 修改 `DEFAULT_PROMOTION_GATES` 值 → 同步更新 `promotion_gates_contract.md` + `spec_promotion_gates.py`
4. 新增 `FORBIDDEN_INTENTS`/`RESTRICTED_TOOLS` 条目 → 同步 spec + 契约文档
5. 修改 scheduler job 频率 → 同步更新 `agent_contracts.md` job 表 + `SYSTEM_MAP.md`
6. 新增/删除 API 端点 → 同步更新 `api_reference.md`
7. 新增/删除数据表 → 同步更新 `data_model.md`
8. 新增/删除 config 字段 → 同步更新前端 Settings 页面（CLAUDE.md "Config ↔ UI rule"）
9. 新增/删除信任边界子系统 → 同步 docs/governance/trust_and_change_control.md §6.1 受控模块清单
10. **新增/删除数据源** → 先在 `docs/design/data_utilization.md` §3 登记表补行（来源/消费者/状态），同步加 `spec_data_utilization.py` DU-* claim，再写采集代码（R5 前置条件，无登记不接入）

**运行命令**：
- `uv run pytest tests/test_architecture_fitness.py -q` — L2a 依赖方向检查（< 1s）
- `uv run pytest tests/specs/ -q` — 全部 spec claims（< 2s）
- `uv run pytest tests/test_spec_conformance.py -v -s` — 打印 Conformance Matrix
- `uv run pytest tests/specs/spec_api_reference.py tests/specs/spec_data_layer.py tests/specs/spec_operator_loop.py tests/specs/spec_subsystem_observers.py -q  # 新增子系统 spec`

## Safety Rules

- Never read or output API keys, exchange secrets, or credentials
- Never commit .env, .secret.key, secrets.enc, or token files
- Never enable live trading without explicit user confirmation
- Never modify risk limits, credentials, or execution paths without user approval
- Never place live orders or unlock live mode autonomously
