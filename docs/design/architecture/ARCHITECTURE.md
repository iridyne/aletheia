# Architecture Reference

> 快速导航层。深度内容见各链接文档；本文不重复细节。

> 当前支持边界只认
> [`ACTIVE_RUNTIME_SCOPE.md`](../../product/ACTIVE_RUNTIME_SCOPE.md) 和
> [`CAPABILITY_GROUND_TRUTH.md`](../../product/CAPABILITY_GROUND_TRUTH.md)；
> 本页仅作导航，不把已实现模块或历史图表升级为受支持能力。

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| 语言 | Python 3.12（后端）/ TypeScript + React 18（前端）|
| Web 框架 | FastAPI + uvicorn（`--loop none` 强制，防 IOCP 冻结）|
| 数据库 | SQLite（WAL 模式，进程级 `_WRITE_LOCK` 写序列化，`busy_timeout=30s`，线程本地连接；运行实例使用 owner 选定的数据根目录）|
| 包管理 | `uv`（禁用 pip/poetry）|
| 前端构建 | Vite + React（产物 `aletheia/dashboard_static/`，gitignored）|
| 交易所接入 | 当前主线只以 Bitget crypto spot/perpetual 为活动证据路径；其他 adapter 的存在不等于 Supported |
| LLM 路由 | 兼容 OpenAI / Anthropic 格式；实际 provider 以运行实例 provenance 为准 |
| 进程管理 | supervisor → frontdoor(:8787) + backend(:8788)，`lifecycle.py` 统管 |
| 任务调度 | APScheduler（max_workers=3，不得上调）|

---

## Directory Structure

```
aletheia-trading-os/
├── aletheia/               # 后端 Python 包（核心）
│   ├── agents/             # 观察、候选整理、Operator 决策和复盘 worker
│   ├── risk/               # ⚠️ 信任边界：风控引擎（勿随意改）
│   ├── execution/          # ⚠️ 信任边界：执行网关（勿随意改）
│   ├── data/               # 数据提供者、symbol 转换、CCXT provider
│   ├── config/             # 配置 schema（改此处必须同步前端 Settings 页）
│   ├── storage/            # SQLite 连接池、repositories
│   ├── learning/           # 学习闭环
│   ├── server/             # FastAPI 路由（routes/）
│   ├── scheduler/          # 定时任务注册
│   ├── dashboard_static/   # 前端构建产物（gitignored，需手动 build）
│   └── exchanges/          # 交易所适配器；当前支持边界见 Active Runtime Scope
│       └── bitget/auth.py  # ⚠️ 信任边界：凭证签名（勿随意改）
├── frontend/               # React/Vite 前端源码
│   └── src/
│       └── lib/format.ts   # fmtSymbol()：唯一合法 symbol 显示入口
├── tests/                  # 回归测试与可执行规格
│   └── specs/spec_*.py     # SDD executable claims
├── scripts/                # 运维、验证与文档治理脚本
├── data/                   # checkout 本地数据（gitignored；不得代表 Fedora 运行状态）
├── docs/                   # 文档（见 docs/README.md 四层金字塔）
├── AGENTS.md               # ← AI agent 仓库规则路由
└── aletheia/config/secrets.py # ⚠️ 信任边界（勿随意改）
```

---

## Data Flow（简版）

```
Bitget spot/perpetual 市场事实与活动 Universe
    ↓
Data Collectors → 快照、结构化上下文与来源证据
    ↓
Universe signal emitter → deterministic eligibility → opportunities
    ↓
Market Observer（每10min）→ 可选 LLM advisory enrichment
    ↓
Paper Ranker（确定性筛选）→ signal_pending PiDecision
    ↓
System Operator → trade / skip
    ↓
不可变 TradeProposal
    ↓
Risk Engine（⚠️ 信任边界）→ approve / reject
    ↓
Execution Gateway（⚠️ 信任边界）→ paper_internal order/fill/position
    ↓
Position Manager（保护性止损/止盈/追踪与平仓）
```

---

## 禁止修改区域（无 owner 明确授权不得碰）

| 路径 | 理由 |
|------|------|
| `aletheia/risk/` | 风控核心，行为变更必须有回归测试 |
| `aletheia/execution/` | 执行网关，直接影响资金安全 |
| `aletheia/exchanges/bitget/auth.py` | 凭证签名逻辑 |
| `aletheia/config/secrets.py` | 密钥处理 |
| `aletheia/agent_runtime/permissions.py` | Agent 权限边界 |
| `aletheia/server/routes/live.py` | 实盘开关 |

---

## 深度阅读

- 架构全景（8章）→ [`docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md`](SYSTEM_ARCHITECTURE_GUIDE.md)
- 数据模型 → [`docs/design/data/data_model.md`](../data/data_model.md)
- Agent 契约 → [`docs/design/contracts/agent_contracts.md`](../contracts/agent_contracts.md)
- 信任边界规则 → [`docs/governance/policies/trust_and_change_control.md`](../../governance/policies/trust_and_change_control.md)
- 完整 AI 规则 → [`AGENTS.md`](../../../AGENTS.md)
