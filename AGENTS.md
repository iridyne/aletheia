# AGENTS.md - Aletheia Trading OS Agent Rules

> 本文件是唯一权威规则源。原 CLAUDE.md 内容已合并入此，不再维护双份。

---

## Project Overview

AI-native agentic trading system. Local-first, file-based "Trading-as-Git" platform.
- **Target users**: Solo trader (project owner) running automated trading strategies
- **Core function**: 7 AI agents orchestrate market analysis, strategy generation, risk management, and order execution on Bitget
- **Stage**: MVP — working prototype, needs trust hardening, not feature expansion
- Design reference: `docs/governance/implementation_guide.md`

## Architecture

- **Data flow**: Market data → Agent analysis → Strategy proposal → Risk Engine approval → Execution Gateway → Bitget API
- **External deps**: Bitget API (spot/futures), LLM providers (OpenAI-compatible, Anthropic-compatible)
- **Frontend**: `frontend/` — React/Vite dashboard, single endpoint `:8787`. Dev iteration: `scripts/dev.ps1` (Vite watch + backend watcher). Do NOT rebuild `aletheia/dashboard_static/` manually in a tight loop.
- **Tests**: `tests/` — 515+ tests. See **Testing Strategy** below.

---

## Core Operating Rule

Before changing code, state what boundary you are touching and what you will not change.
If the task does not require code changes, do not change code.
Inaction, analysis, or asking for clarification is a valid successful outcome when the
requested change would expand scope, weaken safety, or make system state less trustworthy.

---

## Development Rules

- Smallest change principle — MVP, not greenfield
- No uncontrolled expansion: no new exchanges, no Docker/Postgres/Redis, no vector search
- **EXPERIMENT FREEZE —— 已于 2026-06-27 由 owner 决定提前结束/解除**: Round 1 因实验有效性塌方提前结束；**冻结约束已解除**，原 AI 决策链代码现可修改。当前处于「Round 1 后全量修复」阶段（见 `docs/plans/round2_week1_decision_chain_fixes.md` + `docs/issues/` master ledger）。原冻结路径（`agent_shell/trading_context.py`, `agent_shell/trading_decision.py`, `agents/market_observer.py`, `agents/paper_trader*.py`, `agents/pi_agent*`, `strategy/`, `learning/`）仍是高敏感决策链：修改时**必须**补/更新对应 `tests/specs/spec_*.py`、走 SDD 流程、改后 `aletheia doctor` 验证。详见 `docs/product/EXPERIMENT_PROTOCOL.md`。
- Trust boundaries (change-controlled — touch only for tasks that explicitly require it):
  `aletheia/risk/`, `aletheia/execution/`, `aletheia/exchanges/bitget/auth.py`, `aletheia/exchanges/bitget/client.py`,
  `aletheia/config/secrets.py`, `aletheia/agent_runtime/permissions.py`, `aletheia/agent_runtime/tool_registry.py`,
  `aletheia/agent_shell/registry.py`, `aletheia/files/jsonl.py`, `aletheia/server/routes/live.py`
  For these files, every behavioral change must have a focused regression test — and the machine-readable
  policy in `.agent/policy.yaml` requires matching spec evidence in the task packet (`workflow check --strict`).
  Keep this list in lockstep with `.agent/policy.yaml` and `docs/governance/trust_and_change_control.md §6.1`;
  `tests/specs/spec_trust_boundary.py` fails on drift.
- Trading state invariants: Orders flow through Execution Gateway only; live trading disabled by default
- Market data: always go through `get_market_data_provider()` (`aletheia/data/ccxt_provider.py`) — never import `BitgetClient` for market data (execution-context only; REST v2 rejects canonical `BTC/USDT`). Providers are per-call, never cached (CCXT binds aiohttp to the creating event loop). Only exception: Bitget-only ON-series xStocks, which construct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly (see comments at call sites).
- Symbol format: canonical `BTC/USDT` everywhere internally (storage, risk, PnL, opportunities); `BTCUSDT` exists ONLY at the execution adapter boundary via `to_bitget()`. Converters/`InstrumentId` live in `aletheia/data/symbols.py`. A mismatch silently drops opportunities at `paper_trader`'s filter.
- Symbol DISPLAY rule (frontend, immutable owner rule 2026-06-26): the slash IS the spot-vs-derivative signal and must never contradict the instrument type — **perp/futures → no slash (`BTCUSDT`); spot → slash (`BTC/USDT`)**. Internally everything is stored canonical (with slash), so display is a pure transform: every symbol render in the frontend MUST go through `fmtSymbol(symbol, marketType)` in `frontend/src/lib/format.ts` (strips the slash only when `isPerpMarket(marketType)`; spot/unknown keeps the canonical slash — never guess). Pass the row's `market_type` (Position/Order/TradeProposal/MarketSnapshot), `market` (Opportunity), or the board's `market_type` (Universe). Exemptions: bare research/news tickers without a quote (e.g. `BTC`), user-entered symbol-filter chips, fixed ticker labels. Adding a new symbol display without `fmtSymbol` is a regression.
- SQLite concurrency (do not break): Three pillars must all hold — (1) WAL + `busy_timeout` (`storage/sqlite.py`); (2) one connection per thread via `threading.local` (never cache a connection across threads); (3) `was_in_tx = db.in_transaction` captured **before** every write in `repositories.py`, committing only `if not was_in_tx` — sqlite3 auto-opens a tx on INSERT/UPDATE/DELETE, so checking *after* the write never commits and hangs the WAL lock (the historical `database is locked` bug). Scheduler capped at `max_workers=3`; don't raise it.
- Config ↔ UI rule: every new field in `aletheia/config/schema.py` MUST have a corresponding control in the Web frontend settings page. Users never edit `data/config.yaml` directly.
- WebUI static build: `aletheia/dashboard_static/` is gitignored (build artifact, not tracked). After frontend changes run `cd frontend && npm run build`. On a fresh clone or new machine, run this once before starting the gateway. Gateway startup fails with a clear message if static files are missing.
- GitHub Actions failures: disable the broken workflow to stop alerts; do not archive the repo.
- Priority: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy

---

## API 性能规则（血泪教训 2026-07-02 — 强制执行）

`async def` handler 里的同步阻塞操作（磁盘读、`threading.Lock`、SQLite）会冻住整个 uvicorn event loop，等效于 `time.sleep(N)`。FastAPI 只对 `sync def` 自动 threadpool 保护，`async def` 是开发者的责任。根因案例：`GET /api/settings` 因 `load_config()` 零缓存，冷路径实测 **30 秒**（backend 直连 :8788），修复（5s TTL 缓存）后降至 <50ms。

**写新 handler 前必须确认以下四条：**

1. **热路径读必须有缓存**：每次请求都要读的数据（config、health threshold、retention 配置等）必须加 TTL 内存缓存（5–60 s）+ 写入时主动失效。不加缓存的 `load_config()` / `yaml.safe_load()` / `model_validate()` 禁止出现在 async handler 热路径。
2. **初始化操作只跑一次**：`ensure_data_dirs()` 类"首次运行才需要"的操作必须用 flag 保护，不得在每次写操作中重复执行（35 次 mkdir 系统调用 × 每次保存 = 累积延迟）。
3. **新端点上线前直连实测**：`Measure-Command { Invoke-WebRequest -Uri 'http://127.0.0.1:8788/api/<endpoint>' -UseBasicParsing }` — 直连后端（:8788），绕过 frontdoor 缓存。冷路径 >100ms 必须有解释，>500ms 必须修。
4. **UI 轮询间隔下限**：健康探针（exchange 连接测试）≥ 60s；状态轮询（status/kill-switch）≥ 10s 且 handler 必须是纯内存操作（<5ms）；LLM 相关 ≥ 30s。Settings 页面打开时并发轮询数不超过 8 个。

> 完整根因分析 + 已知遗留慢点 + 快速检查清单：`docs/_ai/ASYNC_PERFORMANCE_LESSONS.md`

---

## Trading State Invariants

- Every order must flow through the backend Execution Gateway.
- Never allow direct `agent → exchange`, `dashboard → exchange`, or `telegram → exchange` order placement.
- Live trading starts disabled and must remain blocked unless explicitly unlocked by a human-controlled route.
- Risk Engine approval is required before any order is executed.
- Proposal, risk decision, order, fill, and position records must keep their original causal IDs.
- Audit logs must describe the real actor, source, entity type, entity ID, and state change.
- API keys and secrets must never be returned by GET endpoints, written to logs, inserted into Markdown, or passed into agent prompts.

---

## UI Honesty

The dashboard must never display a reassuring state not backed by a real backend value.

- Do not hardcode risk state as "within limits".
- Do not show Daily Reviews unless backend data has `item_type="daily_review"`.
- Do not show live readiness unless live state, permission mode, and risk status all support it.
- Do not create UI tabs for capabilities that only exist in the guide, not the implementation.

When in doubt, show "not available" or "not configured" rather than inventing a positive status.

---

## Knowledge Base Rules

Knowledge items must be semantically honest:
`research_note` / `strategy_memory` / `dreaming_log` / `daily_review` / `post_trade_review`
— do not store one type as another to make a page populate.
Hard deletion of knowledge is forbidden. Archive or deprecate instead.

---

## Agent Autonomy Limits

Agents **may**: create research, opportunities, paper proposals, strategy drafts, reviews, and logs.

Agents **must not**:
- Unlock live trading or place live orders directly.
- Change hard risk limits or modify credentials / signer logic.
- Modify execution gateway semantics.
- Edit these project instructions to grant themselves more power.

Restricted tools must remain uncallable by LLM-driven agents.

---

## Change Discipline

1. Inspect the current code path before editing.
2. Add or update a focused regression test first for safety/trading/audit/secrets/lifecycle changes.
3. Make the smallest change that satisfies the requested behavior.
4. Run the narrow test that proves the fix.
5. Run broader verification only after focused tests pass.
6. Report what changed, what was verified, and what risk remains.

Never claim a trading, risk, live, or secret-handling fix is complete without test evidence.

---

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
8. 新增/删除 config 字段 → 同步更新前端 Settings 页面（"Config ↔ UI rule" 节）
9. 新增/删除信任边界子系统 → 同步 `docs/governance/trust_and_change_control.md` §6.1 受控模块清单
10. **新增/删除数据源** → 先在 `docs/design/data_utilization.md` §3 登记表补行（来源/消费者/状态），同步加 `spec_data_utilization.py` DU-* claim，再写采集代码（R5 前置条件，无登记不接入）

**运行命令**：
- `uv run pytest tests/test_architecture_fitness.py -q` — L2a 依赖方向检查（< 1s）
- `uv run pytest tests/specs/ -q` — 全部 spec claims（< 2s）
- `uv run pytest tests/test_spec_conformance.py -v -s` — 打印 Conformance Matrix
- `uv run pytest tests/specs/spec_api_reference.py tests/specs/spec_data_layer.py tests/specs/spec_operator_loop.py tests/specs/spec_subsystem_observers.py -q`

---

## Testing Strategy

全量测试 ~5min（2300+ tests，I/O-bound，SQLite setup/teardown 是瓶颈，并行无收益）。

- **日常**：`uv run pytest tests/test_xxx.py -q`（秒级）；针对性用 `-k "knowledge or dreaming"`
- **提交前**：后台跑全量，不阻塞工作流
- **全量命令**：`uv run pytest tests/ -q --tb=line`

不等全量完成再继续工作——后台跑，有失败再回头修。

Additional: `.\scripts\dev.ps1` runs gateway on `:8787` + `vite build --watch` during UI iteration.
Iterate against `http://127.0.0.1:8787/`; do not use `:5173` as the daily entrypoint.

---

## Gateway 进程管理

三进程架构（supervisor → frontdoor:8787 + backend:8788），`lifecycle.py` 统管。不要手动 kill，用 `uv run aletheia gateway <restart|stop|start|status|logs>`。代码改动后必须 restart 才能生效。`--no-browser` 避免弹浏览器。Task Scheduler 看门狗每 5 分钟自动拉起。

---

## Repo Setup

**`aletheia/data/` is the source package** (symbols.py, ccxt_provider.py, etc.) — distinct from repo-root `data/` runtime dir (gitignored). `.gitignore` must keep the runtime rule root-anchored as `/data/`; a bare `data/` silently excludes the source package from git. After cloning verify: `uv run python -c "import aletheia.risk.engine"`.

**Python 调用铁律**：所有 Python 命令必须通过 `uv run` 执行（`uv run python`、`uv run pytest`、`uv run aletheia ...`），**禁止裸 `python` / `python -m`**。裸 `python` 可能指向系统 Python 或其他 venv（如 hermes-agent），缺少项目声明的依赖（`typer`、`hypothesis` 等），导致 import 失败。`uv run` 保证使用项目 `.venv` + `pyproject.toml` 声明的完整依赖树。

**依赖同步**：`git pull` 后如果 `pyproject.toml` 有变更，必须跑一次 `uv sync`（幂等、秒级）。会话开始时建议无条件跑 `uv sync`——无变更时 <1s，有变更时自动补装。

---

## AI Collaboration Rules

**面向 owner 汇报用大白话，不甩黑话（强制 — 血泪教训 2026-07-02）**：owner 是单人 trader，不是读代码的工程师同事。对话汇报 / handoff 摘要 / 状态说明里**禁止甩内部代号和术语**——"契约 / spec / claim / bucket / signal_source / trade_school / LP-S07"这类词对他是噪音，只会抬高认知与判断压力。规则：①**先讲人话结论**——"哪个模块、什么后果、现在什么状态"，再展开细节；②内部概念用生活化翻译；③不得已要用代号，首次出现紧跟一句人话解释；④**别因为"任务本身很技术"就默认可以甩术语**。唯一例外：写进代码 / docs / spec 的技术措辞照常严谨。

意图/事实分离：git 同步**意图**（代码+文档），绝不写运行态事实（gateway 进程/app.db/config.yaml/凭证）进任何 git 文档——在另一台机器上必然过期。详见 `docs/_ai/DEV_PROTOCOL.md`。

会话开始：`git pull --ff-only` → `uv sync` → `uv run aletheia agent workflow bootstrap` → `uv run aletheia doctor` → 读 AGENTS.md、当前任务包和其 handoff。`bootstrap` 是 Git 中任务与证据的入口；`doctor` 是本机运行态的唯一来源，两者不可互相替代。
会话结束：先提交实现，再用 `uv run aletheia agent workflow task verify TASK-ID --command-key KEY` 真实执行登记过的验证；**每个任务的结构化交接就写在 `.agent/handoffs/TASK-*.md`——这是主渠道**，无剩余工作时运行 `task complete`，最后运行 `uv run aletheia agent workflow check --strict`。CI 会阻止代码/文档变更缺少任务包或交接更新。**`docs/_ai/AI_HANDOFF.md` 已于 2026-07-12 冻结为历史存档**，不再 append；变更日志继续写 `DEVLOG.md`，架构决策记入 `docs/_ai/DECISIONS.md`。任务文件只写计划/意图/验证证据，不写运行态。

Issue 分工：`docs/issues/BACKEND_ISSUES.md`（bugs/contract drift/dead code，带 file:line）；`docs/issues/FRONTEND_ISSUES.md`（UI spec gaps/honesty breaches）。

---

## 并发会话 / 多 Workspace 开发（强制 — 血泪教训 2026-07-01）

**前提**：owner 会同时开多个 workspace/会话在同一 repo 上干活。这是常态，不是异常。目标不是"别并发"，而是**在并发下仍不互相破坏**。

**隔离优先（首选适应性举措）**：
- **每个并发会话在自己的 `git worktree` 里干活**（`.claude/worktrees/<name>/`）。worktree = 独立目录 + 独立 HEAD + 独立工作树 + 独立 `dashboard_static/` build 产物，从物理上消除分支/暂存/build 冲突。**不要多个会话在主 checkout 上并行切分支**。
- 注意 worktree 陷阱：worktree 的 `data/` 为空，restart gateway 会让交易系统空跑；前端上线必须回主仓 FF-merge（见 docs/_ai/GIT_WORKTREE_PLAYBOOK.md）。

**若确实在共享 checkout 上操作（无法隔离时）**：
1. **每次 commit/push/merge/checkout 前**先 `git branch --show-current` + `git status -sb` 重核当前分支与领先/落后，**别信记忆或上文快照**。
2. **固化工作用 plumbing，不切 HEAD**：`TREE=$(git write-tree); C=$(git commit-tree $TREE -p HEAD -F - <<'MSG'…MSG); git branch <name> $C`。创建 commit+分支引用但不动 HEAD/工作树/index，对并发会话零干扰。
3. **只 `git add` 明确文件**，禁 `git add -A`。提交后 `git show --stat <commit>` 核实文件数对得上。
4. **不轻易 `reset --hard` / `checkout`**（会覆盖别的会话正在编辑的工作树）。冲突时优先保护对方改动，不强推。
5. **前端 build 产物陷阱**：`dashboard_static/` 是 gitignore 的 build 产物、不随 git 走；多会话切分支会把它搅成半成品 → 页面黑屏（JS chunk 404）。修复：`cd frontend && npm run build`。
6. **gateway 是全局单例**（不随 worktree 隔离）；别在多个会话/worktree 同时 restart。

本项目 git 工作区易处于高强度竞争：多并发会话/后台 job + 自动 git hook(post-merge 重启) + Task Scheduler 看门狗会同时抢 master，导致 HEAD 频繁移动。铁律：任何会写文件的非平凡改动，一律在独立 worktree 中进行（EnterWorktree 或 git worktree add）；纯只读/查询可在原地。详见 `docs/_ai/GIT_WORKTREE_PLAYBOOK.md`。

---

## Git And Workspace Rules

- Do not revert user changes unless explicitly asked.
- Do not run destructive git commands (`git reset --hard`, `git checkout --`) without explicit approval.
- If the worktree is dirty, identify which changes are yours vs pre-existing before summarizing.
- Do not amend commits unless explicitly asked.
- **Branch isolation**: Every code change must land on its own dedicated branch, not the current open PR/branch. Before making any edit, check the current branch (`git branch --show-current`); if it already has an open PR or belongs to an unrelated task, create a new branch first. Never piggyback unrelated fixes onto an existing PR.

---

## Escalation Rules

Stop and ask before proceeding if:
- A requested change would weaken a listed invariant.
- A fix requires changing multiple change-controlled modules.
- Tests reveal a broader architectural defect than the requested task.
- The implementation guide and current code conflict in a way that changes product behavior or trading safety.
- You cannot prove the effect of the change with local tests or inspection.

---

## Safety Rules

- Never read or output API keys, exchange secrets, or credentials
- Never commit `.env`, `.secret.key`, `secrets.enc`, or token files
- Never enable live trading without explicit user confirmation
- Never modify risk limits, credentials, or execution paths without user approval
- Never place live orders or unlock live mode autonomously

---

## Preferred Direction

State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy
