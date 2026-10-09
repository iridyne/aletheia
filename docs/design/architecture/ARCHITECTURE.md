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
| 语言 | Python 3.12（后端）/ TypeScript + React 19（前端）|
| Web 框架 | FastAPI + uvicorn（`--loop none` 强制，防 IOCP 冻结）|
| 数据库 | SQLite（WAL 模式，进程级 `_WRITE_LOCK` 写序列化，`busy_timeout=30s`，线程本地连接；运行实例使用 owner 选定的数据根目录）|
| 包管理 | `uv`（禁用 pip/poetry）|
| 前端构建 | Vite + React（产物 `core/aletheia/dashboard_static/`，gitignored）|
| 交易所接入 | Core 通过 UTA/Coordinator 与 Exchange Port 访问已注册的 `bitget_demo`、`okx_demo`、`binance_testnet`；注册不等于凭据、能力或资格证明 |
| LLM 路由 | 兼容 OpenAI / Anthropic 格式；实际 provider 以运行实例 provenance 为准 |
| 进程管理 | supervisor → frontdoor(:8787) + backend(:8788)，`lifecycle.py` 统管 |
| 任务调度 | APScheduler，命名 executor 池各 `max_workers` ≤ 3（[scheduler_contract](../contracts/scheduler_contract.md) INV-SCH-1）|

---

## Where modules live

模块与调用链导航只有一个入口：[System Map](SYSTEM_MAP.md)（任务 → owner → 源码 → focused tests），
stage → location 另见 [`AGENTS.md`](../../../AGENTS.md#where-the-stages-live)。本页不再维护第二份目录树副本：
旧树把 core 写成仓库根下的 `aletheia/`、把 venue adapter 挂在 core 下，都与
`core/` + `extensions/` 的现状相反。信任边界规则见 [`AGENTS.md`](../../../AGENTS.md)，symbol 显示入口的规则见 [`frontend/AGENTS.md`](../../../frontend/AGENTS.md)。

---

## Module Seams

大文件按领域拆成 seam 模块（facade + `<facade>_<domain>.py`）。facade 保留 shared writers、事务 helper 和公开签名；seam 只承担该域的读/纯计算。所有 seam 通过 facade `from ... import *` 再导出，`from aletheia.<pkg> import <name>` 的旧用法不变。

| Facade | Seams | Facade 只留 |
|---|---|---|
| `storage/repositories.py` | `repositories_positions` `repositories_universe` `repositories_research` `repositories_market_data` `repositories_execution` | `_stamp_provenance` / `_insert` / `_update` / `_select_*` / `transaction`；所有 writers（domain seam 因 ownership guard 拒绝导入 generic writers）|
| `diagnostics/doctor.py` | `probes/content` `probes/storage_trust` `probes/environment` `probes/data_quality` `probes/trading` | `Check` / `Report` 类；`_build_report` 编排；shared helpers（`_age_minutes`, `_fmt_age`, `_parse_iso_datetime`）|
| `extensions/observation_lab/canonical.py` | `canonical_adapters`（Domain A：value helpers + adapter Protocol/registry singleton）`canonical_lifecycle`（Domain B：Lab 证据 + lifecycle writes）| Domain C/D/E（authority/treatment 决策 · Main/Lab transport · retention/archive）— 跨事务边界，暂未拆 |
| `agent_runtime/scheduler.py` | `scheduler_retention`（`_prune_*` × 7 + `_cleanup_catchup_due`）| `_run_cleanup` 因 `spec_agent_contracts` AST-scan 阻拦保留；所有 `add_job()`、`_ESSENTIAL_JOBS`、timeout workers |
| `analytics/profit_gradient_evidence.py` | `profit_gradient_evidence_utils`（8 SAFE：`_json` `_hash` `_utc_text` `_text` `_safe_json_object` `_parse_utc` `_iso` `_finite`）| builder / verifier / registration / pairing / coverage — 跨事务 + `source_hash` binding，未拆 |

大函数拆分的逐项记录不在此维护；以 Git 历史与 #3561 为准。

---

## Data Flow（简版）

```mermaid
flowchart TD
    D[Data Collectors] --> U[Universe candidate planning]
    U --> O[System Operator]
    O --> P[Immutable Entry Plan / TradeProposal]
    P --> R[Risk Engine]
    R --> G[Execution Gateway]
    G --> C[Coordinator / UTA]
    C --> F[Exchange Order / Private Fill / Position]
    F --> M[Position Manager]
```

本地模拟执行已退役，旧定义见[历史归档](../history/local_simulated_risk_execution.md)。

## Unified Trading Account boundary

[`unified_trading_account.md`](../proposals/unified_trading_account.md) 已接受
完整 UTA 设计，取代“UTA 仅只读”这一旧决策。UTA 是六个现有外部执行模式的唯一
私有账户读路径；Gateway 的授权变更通过通用 Coordinator 和 `CCXTUTAAdapter`，
本地 storage 保留事实与 CAS。Binance、OKX 与 Bitget UTA-v3 的差异
限制在 `extensions/exchanges/{venue}/profile.py` 与 venue-native exception，返回 Core 可消费的规范化 DTO；
不存在 legacy/shadow selector 或私有 broker fallback。

Coordinator 负责 pending/claim、外部调用后的短 CAS、审计以及未知结果的重对账，
UTA 只负责外部账户 I/O。已注册的 Demo/Testnet 模式仍不证明凭据、仪器能力、账号
可达或端到端资格；Live 仍在当前支持面之外，读取账户也不会授予变更权限。该边界
记录的是静态代码事实，不声称外部往返或 Fedora 部署已经完成。

---

## 高敏感区域

Risk、Execution、凭证、Agent 权限与 Live 开关的改动边界只在
[SAFETY_AND_TRUST](../../operations/development/agent-guides/SAFETY_AND_TRUST.md) 定义。

---

## 深度阅读

- 架构全景（8章）→ [`docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md`](SYSTEM_ARCHITECTURE_GUIDE.md)
- 数据模型 → [`docs/design/data/data_model.md`](../data/data_model.md)
- Agent 契约 → [`docs/design/contracts/agent_contracts.md`](../contracts/agent_contracts.md)
- 信任边界规则 → [`docs/governance/policies/trust_and_change_control.md`](../../governance/policies/trust_and_change_control.md)
- 完整 AI 规则 → [`AGENTS.md`](../../../AGENTS.md)
