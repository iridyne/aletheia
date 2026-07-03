# Architecture Reference

> 快速导航层。深度内容见各链接文档；本文不重复细节。

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| 语言 | Python 3.12（后端）/ TypeScript + React 18（前端）|
| Web 框架 | FastAPI + uvicorn（`--loop none` 强制，防 IOCP 冻结）|
| 数据库 | SQLite（WAL 模式，`busy_timeout=10s`，单文件 `data/app.db`）|
| 包管理 | `uv`（禁用 pip/poetry）|
| 前端构建 | Vite + React（产物 `aletheia/dashboard_static/`，gitignored）|
| 交易所接入 | CCXT（统一接口，三家：Bitget / Binance / OKX）|
| LLM 路由 | 兼容 OpenAI / Anthropic 格式（本地 Ollama 可替代）|
| 进程管理 | supervisor → frontdoor(:8787) + backend(:8788)，`lifecycle.py` 统管 |
| 任务调度 | APScheduler（max_workers=3，不得上调）|

---

## Directory Structure

```
aletheia-trading-os/
├── aletheia/               # 后端 Python 包（核心）
│   ├── agents/             # 14 个 AI agent（market_observer, paper_trader, pi_agent 等）
│   ├── risk/               # ⚠️ 信任边界：风控引擎（勿随意改）
│   ├── execution/          # ⚠️ 信任边界：执行网关（勿随意改）
│   ├── data/               # 数据提供者、symbol 转换、CCXT provider
│   ├── config/             # 配置 schema（改此处必须同步前端 Settings 页）
│   ├── storage/            # SQLite 连接池、repositories
│   ├── learning/           # 学习闭环
│   ├── strategy/           # 策略引擎（RuleSet）
│   ├── server/             # FastAPI 路由（routes/）
│   ├── scheduler/          # 定时任务注册
│   └── dashboard_static/   # 前端构建产物（gitignored，需手动 build）
├── exchanges/              # 交易所适配器（bitget/binance/okx）
│   └── */auth.py           # ⚠️ 信任边界：凭证签名（勿随意改）
├── frontend/               # React/Vite 前端源码
│   └── src/
│       └── lib/format.ts   # fmtSymbol()：唯一合法 symbol 显示入口
├── tests/                  # 515+ 测试
│   └── specs/spec_*.py     # SDD executable claims
├── scripts/                # 运维脚本（dev.ps1, db_maintenance.py 等）
├── data/                   # 运行时数据目录（gitignored）
│   ├── app.db              # SQLite 主数据库
│   └── config.yaml         # 运行时配置（非 git，用 doctor 读）
├── docs/                   # 文档（见 docs/README.md 四层金字塔）
├── AGENTS.md               # ← AI agent 唯一规则权威源
└── config/secrets.py       # ⚠️ 信任边界（勿随意改）
```

---

## Data Flow（简版）

```
交易所 OHLCV/Funding/OI
    ↓
Data Collectors（每5min）→ universe_feature_snapshots
    ↓
Market Observer（每10min）→ LLM 分析 → opportunities
    ↓
Ranker（5层过滤）→ signal_pending → pi_decisions
    ↓
PI Agent（System Operator）→ approve/reject
    ↓
Risk Engine（⚠️ 信任边界）→ approve/block
    ↓
Execution Gateway（⚠️ 信任边界）→ 交易所下单
    ↓
Position Manager（止损/止盈，唯一有自治执行权的 agent）
```

---

## 禁止修改区域（无 owner 明确授权不得碰）

| 路径 | 理由 |
|------|------|
| `aletheia/risk/` | 风控核心，行为变更必须有回归测试 |
| `aletheia/execution/` | 执行网关，直接影响资金安全 |
| `exchanges/*/auth.py` | 凭证签名逻辑 |
| `config/secrets.py` | 密钥处理 |
| `agent_runtime/permissions.py` | Agent 权限边界 |
| `server/routes/live.py` | 实盘开关 |

---

## 深度阅读

- 架构全景（8章）→ [`docs/SYSTEM_ARCHITECTURE_GUIDE.md`](SYSTEM_ARCHITECTURE_GUIDE.md)
- 数据模型（83张表）→ [`docs/design/data_model.md`](design/data_model.md)
- Agent 契约（14个 agent）→ [`docs/design/agent_contracts.md`](design/agent_contracts.md)
- 信任边界规则 → [`docs/governance/trust_and_change_control.md`](governance/trust_and_change_control.md)
- 完整 AI 规则 → [`AGENTS.md`](../AGENTS.md)
