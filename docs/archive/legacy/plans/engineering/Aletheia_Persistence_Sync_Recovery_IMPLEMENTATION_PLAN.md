# Aletheia Persistence / Sync / Recovery Implementation Plan

版本：v1.0  
日期：2026-06-13  
目标读者：代码实现 Agent、系统架构 Agent、后端开发 Agent  
适用项目：Aletheia Agentic Trading Platform  
核心目标：Gateway / Agent / Worker 任意重启后，不丢订单、持仓、运行记录、市场分析、Proposal、复盘经验和可复用 Skill。

---

## 0. 执行摘要

Aletheia 的持久化系统不能按普通应用的“保存配置 + 保存历史记录”来做。它需要按交易系统和 Agent 系统的双重要求设计：

1. **交易系统要求**：订单、成交、持仓、风控状态必须可恢复、可对账、可审计。
2. **Agent 系统要求**：分析上下文、证据、claim、prompt 版本、tool trace、经验教训、策略生成过程必须可迁移、可复盘、可回放。
3. **多设备开发要求**：两台电脑可以共享研究资产和配置，但不能同时无锁写 live gateway，也不能同步密钥或脏运行状态。
4. **Gateway 重启要求**：重启后必须先进入 `RECOVERY_MODE`，通过本地 DB、append-only event log、artifact、交易所真实状态做 reconciliation。状态不一致时进入 `SAFE_FREEZE`，禁止新开仓。

最终方案采用：

```text
Metadata DB
+ Append-only Event Log
+ Artifact Store
+ Market Data Lake
+ Secret Store
+ Export / Import Bundle
+ Gateway Recovery / Reconciliation
+ Agent Checkpoint
+ Policy Versioning
+ Backup / Restore
```

MVP 使用：

```text
SQLite
本地文件 Artifact Store
JSONL Event Log
DuckDB / Parquet
Typer CLI
FastAPI
Pydantic
SQLAlchemy / SQLModel
```

后续可升级：

```text
PostgreSQL
MinIO / S3
Redis / NATS
pgvector / Qdrant
Vault / OS Keychain
OpenTelemetry
远程 Aletheia Home Server
```

---

## 1. 非谈判性系统原则

实现 Agent 必须遵守以下原则。

### 1.1 进程内存不是事实源

任何 gateway、agent、worker 的进程内变量都只是缓存。事实源必须来自：

```text
DB snapshot
append-only event log
artifact manifest
exchange reconciliation result
```

Gateway 可以被 kill，机器可以重启，worker 可以崩溃，但系统必须能恢复。

### 1.2 Live 订单必须幂等

所有 live / paper 订单必须携带确定性 `client_order_id` 和 `idempotency_key`。

禁止：

```text
重启后不知道订单是否已发出，于是重新发一遍。
```

正确做法：

```text
通过 client_order_id 查询交易所状态。
查不到则进入 unresolved 状态。
无法确认时 SAFE_FREEZE。
```

### 1.3 Gateway 启动必须先 recovery

Gateway 每次启动都必须经过：

```text
STARTING → RECOVERY_MODE → RUNNING / SAFE_FREEZE
```

在 `RECOVERY_MODE` 完成之前：

```text
禁止新开仓
禁止增加仓位
禁止 agent 创建 live intent
允许查询账户
允许查询订单
允许查询持仓
允许撤单
允许补写 fill
允许生成 recovery report
```

### 1.4 状态不一致默认冻结

任何本地状态和交易所状态的不可解释差异，都必须进入 `SAFE_FREEZE`。

禁止让 Agent 自己猜：

```text
可能成交了吧
可能撤单了吧
可能没有持仓吧
```

### 1.5 研究资产必须 artifact 化

以下对象必须可以作为文件包迁移：

```text
evidence
claim
market snapshot
analysis artifact
strategy spec
validation report
proposal
paper run
live run
review report
lesson
skill
policy version
```

### 1.6 密钥默认永不导出

导出、备份、同步时默认排除：

```text
API key
wallet private key
exchange secret
database password
.env
*.pem
*.key
config/secrets/
```

### 1.7 Agent 学习只能修改软参数

Agent 经验、lesson、skill 可以用于：

```text
scanner weights
family quota
TTL
prompt template proposal
tradability filter proposal
research workflow
claim verification template
```

不能直接修改：

```text
hard risk limits
live unlock
API 权限
最大仓位
最大杠杆
最大回撤
黑白名单硬边界
```

---

## 2. 范围与非范围

### 2.1 本 PLAN 覆盖

```text
workspace 目录结构
metadata DB schema
event log schema
artifact store
hash / manifest
order / fill / position persistence
gateway recovery
exchange reconciliation
agent checkpoint
market analysis persistence
lesson / skill persistence
policy versioning
export / import
backup / restore
two-machine sync
leader lock
CLI / API
implementation phases
test plan
acceptance criteria
```

### 2.2 本 PLAN 不覆盖

```text
具体交易策略盈利逻辑
具体交易所 API 细节
完整前端 UI 设计
合规法律意见
生产级高频撮合架构
```

---

## 3. 推荐架构

### 3.1 高层架构

```text
+------------------------------------------------------------+
|                    Aletheia Application                    |
|                                                            |
|  +----------------+   +----------------+   +-------------+ |
|  | Agent Workers  |   | Gateway        |   | Scheduler   | |
|  +----------------+   +----------------+   +-------------+ |
|          |                   |                    |         |
|          +-------------------+--------------------+         |
|                              |                              |
|  +------------------------------------------------------+   |
|  |                 Persistence Core                    |   |
|  |                                                      |   |
|  |  Metadata DB   EventStore   ArtifactStore           |   |
|  |  Checkpoints   PolicyStore  ReconciliationStore     |   |
|  +------------------------------------------------------+   |
|                              |                              |
|  +------------------------------------------------------+   |
|  |                  External Systems                   |   |
|  |                                                      |   |
|  |  Exchange API   Object Storage   Secret Store       |   |
|  +------------------------------------------------------+   |
+------------------------------------------------------------+
```

### 3.2 推荐运行模式

#### MVP 本地模式

```text
Laptop A / Laptop B
  aletheia app
  workspace/
    db/aletheia.sqlite
    events/*.jsonl
    artifacts/**
    data/parquet/**
```

适合早期开发和 paper 测试。

#### 推荐稳定模式：Home Server

```text
Aletheia Home Server
  PostgreSQL
  Artifact Store
  Scheduler
  Gateway
  Worker
  API

Laptop A
  frontend
  dev tools
  API client

Laptop B
  frontend
  dev tools
  API client
```

此模式下，长时间运行数据只有一个事实源，不需要两台电脑互相抢状态。

---

## 4. 数据分类与存储策略

| 数据类型 | 示例 | 存储位置 | 同步策略 | 备份策略 |
|---|---|---|---|---|
| 代码 | gateway、agent、scanner、UI | Git | Git | Git remote |
| 默认配置 | 默认 market、默认 scanner | Git | Git | Git remote |
| 用户配置 | 关注市场、prompt template | DB + YAML | export/import | full backup |
| 本机配置 | 数据路径、worker 数量 | config/local | 不同步 | 可选 |
| 密钥 | API key、私钥、密码 | Secret Store | 不同步 | 加密手动备份 |
| 订单 | order intent、order state | Metadata DB | DB sync / export | 高频备份 |
| 成交 | fill、fee、trade id | Metadata DB | DB sync / export | 高频备份 |
| 持仓 | position snapshot | Metadata DB | DB sync / export | 高频备份 |
| 事件日志 | ORDER_FILLED 等 | EventStore | append-only sync | 高频备份 |
| 市场分析 | snapshot、analysis | DB + artifact | export/import | 日备份 |
| 证据 | raw doc、source ref | artifact + DB index | export/import | 日备份 |
| Claim | 结构化事实 | DB + artifact | export/import | 日备份 |
| Proposal | 策略证据包 | artifact + DB index | export/import | 日备份 |
| Lesson | 经验教训 | DB + artifact | export/import | 日备份 |
| Skill | 可复用 agent 方法 | DB + artifact | export/import | 日备份 |
| 原始行情 | K线、orderbook | Parquet / DuckDB | 选择性 | 分区备份 |
| 临时缓存 | HTTP cache、LLM cache | cache/ | 不同步 | 不备份 |

---

## 5. Workspace 目录结构

MVP 初始化时创建以下结构：

```text
workspace/
  config/
    defaults/
    user/
    local/
    policies/
    prompt_templates/
    secrets/                 # gitignore, export 默认排除

  db/
    aletheia.sqlite
    migrations/

  events/
    event_log_2026-06.jsonl

  artifacts/
    evidence/
      2026-06-13/
    claims/
      2026-06-13/
    market_snapshots/
      2026-06-13/
    analyses/
      2026-06-13/
    opportunities/
      2026-06-13/
    strategy_specs/
      2026-06-13/
    validation_reports/
      2026-06-13/
    proposals/
      2026-06-13/
    paper_runs/
      2026-06-13/
    live_runs/
      2026-06-13/
    reconciliation_reports/
      2026-06-13/
    reviews/
      2026-06-13/
    lessons/
      2026-06/
    skills/
      research/
      validation/
      review/

  data/
    duckdb/
    parquet/
      market=crypto/
        venue=okx/
          symbol=BTC-USDT/
            timeframe=1m/

  exports/
  backups/
  logs/
  cache/
  tmp/
```

`.gitignore` 至少包含：

```gitignore
.env
*.key
*.pem
*.secret
config/local/
config/secrets/
workspace/db/
workspace/events/
workspace/data/raw/
workspace/cache/
workspace/tmp/
```

---

## 6. 核心领域对象

系统必须实现以下对象。

```text
EventLog
AgentRun
Checkpoint
EvidenceDoc
Claim
MarketSnapshot
AnalysisArtifact
OpportunitySnapshot
StrategySpec
ValidationReport
Proposal
ExecutionIntent
OrderIntent
Order
Fill
Position
GatewaySession
GatewayLock
ReconciliationReport
PaperRun
LiveRun
ReviewReport
Lesson
Skill
PolicyVersion
ExportManifest
BackupManifest
```

对象关系：

```text
EvidenceDoc -> Claim -> AnalysisArtifact -> StrategySpec -> ValidationReport -> Proposal
Proposal -> ExecutionIntent -> OrderIntent -> Order -> Fill -> Position
Position / PaperRun / LiveRun -> ReviewReport -> Lesson -> Skill / PolicyVersion
```

---

## 7. Metadata DB Schema

MVP 可以使用 SQLite。表设计必须避免使用仅 PostgreSQL 支持的语法，方便后续迁移。

### 7.1 event_log

```sql
CREATE TABLE IF NOT EXISTS event_log (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    run_id TEXT,
    causation_id TEXT,
    correlation_id TEXT,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_aggregate
ON event_log (aggregate_type, aggregate_id, created_at);

CREATE INDEX IF NOT EXISTS idx_event_correlation
ON event_log (correlation_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_event_idempotency
ON event_log (idempotency_key)
WHERE idempotency_key IS NOT NULL;
```

### 7.2 agent_runs

```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    heartbeat_at TEXT,
    machine_id TEXT NOT NULL,
    model_json TEXT,
    prompt_template_id TEXT,
    prompt_hash TEXT,
    input_refs_json TEXT,
    output_refs_json TEXT,
    error_json TEXT,
    artifact_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status
ON agent_runs (status, heartbeat_at);
```

### 7.3 checkpoints

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    state_json TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, step_name)
);
```

### 7.4 evidence_docs

```sql
CREATE TABLE IF NOT EXISTS evidence_docs (
    evidence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    quality_score REAL,
    artifact_path TEXT,
    raw_metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_content_hash
ON evidence_docs (content_hash);
```

### 7.5 claims

```sql
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    subject TEXT,
    predicate TEXT,
    object TEXT,
    confidence REAL NOT NULL,
    price_relevant INTEGER NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    claim_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_claim_hash
ON claims (claim_hash);
```

### 7.6 market_snapshots

```sql
CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    market_type TEXT NOT NULL,
    venue TEXT,
    symbol TEXT,
    timeframe TEXT,
    snapshot_ts TEXT NOT NULL,
    features_json TEXT NOT NULL,
    data_hash TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_snapshot_symbol_ts
ON market_snapshots (venue, symbol, snapshot_ts);
```

### 7.7 analysis_artifacts

```sql
CREATE TABLE IF NOT EXISTS analysis_artifacts (
    analysis_id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    run_id TEXT NOT NULL,
    model_json TEXT,
    prompt_template_id TEXT,
    prompt_hash TEXT,
    input_snapshot_ids_json TEXT,
    input_claim_ids_json TEXT,
    output_summary TEXT,
    opportunity_ids_json TEXT,
    risk_notes_json TEXT,
    artifact_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 7.8 strategy_specs

```sql
CREATE TABLE IF NOT EXISTS strategy_specs (
    strategy_id TEXT PRIMARY KEY,
    market_type TEXT NOT NULL,
    universe_selector TEXT,
    hypothesis TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    artifact_path TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_spec_hash
ON strategy_specs (spec_hash);
```

### 7.9 validation_reports

```sql
CREATE TABLE IF NOT EXISTS validation_reports (
    validation_report_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    pbo REAL,
    artifact_path TEXT,
    created_at TEXT NOT NULL
);
```

### 7.10 proposals

```sql
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    claim_ids_json TEXT,
    validation_report_id TEXT,
    paper_required INTEGER NOT NULL,
    live_unlock_required INTEGER NOT NULL,
    approval_json TEXT,
    expires_at TEXT,
    artifact_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposals_status
ON proposals (status, updated_at);
```

### 7.11 execution_intents

```sql
CREATE TABLE IF NOT EXISTS execution_intents (
    execution_intent_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    mode TEXT NOT NULL,                -- paper | live
    status TEXT NOT NULL,              -- draft | approved | submitted | cancelled | failed
    risk_check_json TEXT,
    unlock_token_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 7.12 order_intents

```sql
CREATE TABLE IF NOT EXISTS order_intents (
    intent_id TEXT PRIMARY KEY,
    execution_intent_id TEXT,
    proposal_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    mode TEXT NOT NULL,                -- paper | live
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                -- buy | sell
    order_type TEXT NOT NULL,          -- market | limit | stop | etc.
    quantity TEXT NOT NULL,
    limit_price TEXT,
    reduce_only INTEGER DEFAULT 0,
    status TEXT NOT NULL,              -- created | submitted | cancelled | failed | completed
    client_order_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 7.13 orders

```sql
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_order_id TEXT,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT,
    status TEXT NOT NULL,              -- submitted | acked | open | partially_filled | filled | cancelled | rejected | unknown
    submitted_at TEXT,
    acknowledged_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status
ON orders (status, updated_at);
```

### 7.14 fills

```sql
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT,
    mode TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_trade_id TEXT,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT,
    fee_currency TEXT,
    filled_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_venue_trade
ON fills (venue, venue_trade_id)
WHERE venue_trade_id IS NOT NULL;
```

### 7.15 positions

```sql
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    proposal_id TEXT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                -- long | short | flat
    quantity TEXT NOT NULL,
    avg_entry_price TEXT,
    realized_pnl TEXT,
    unrealized_pnl TEXT,
    status TEXT NOT NULL,              -- open | closed | orphan | unknown
    opened_at TEXT,
    closed_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_status
ON positions (status, updated_at);
```

### 7.16 gateway_sessions

```sql
CREATE TABLE IF NOT EXISTS gateway_sessions (
    gateway_session_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    heartbeat_at TEXT,
    machine_id TEXT NOT NULL,
    recovery_report_id TEXT,
    freeze_reason TEXT,
    config_hash TEXT
);
```

### 7.17 gateway_locks

```sql
CREATE TABLE IF NOT EXISTS gateway_locks (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
```

### 7.18 reconciliation_reports

```sql
CREATE TABLE IF NOT EXISTS reconciliation_reports (
    reconciliation_report_id TEXT PRIMARY KEY,
    gateway_session_id TEXT NOT NULL,
    status TEXT NOT NULL,              -- ok | diff_found | frozen | failed
    local_state_hash TEXT,
    remote_state_hash TEXT,
    diffs_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL
);
```

### 7.19 lessons

```sql
CREATE TABLE IF NOT EXISTS lessons (
    lesson_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    recommendation_json TEXT,
    status TEXT NOT NULL,              -- proposed | accepted | rejected | applied | archived
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    applied_policy_version TEXT
);
```

### 7.20 skills

```sql
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,               -- research | validation | review | execution_helper
    description TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    allowed_layers_json TEXT NOT NULL,
    forbidden_layers_json TEXT NOT NULL,
    created_from_lessons_json TEXT,
    version INTEGER NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
```

### 7.21 policy_versions

```sql
CREATE TABLE IF NOT EXISTS policy_versions (
    policy_version_id TEXT PRIMARY KEY,
    parent_policy_version_id TEXT,
    policy_type TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    diff_json TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL              -- draft | active | deprecated | rejected
);

CREATE INDEX IF NOT EXISTS idx_policy_versions_type_status
ON policy_versions (policy_type, status, created_at);
```

### 7.22 scheduled_jobs

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    schedule_expr TEXT NOT NULL,
    status TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    job_run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    attempt INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    result_json TEXT,
    error_json TEXT
);
```

---

## 8. Pydantic Model Sketch

实现文件建议：

```text
aletheia/storage/models.py
```

核心模型示例：

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field


class GatewayStatus(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RECOVERY_MODE = "RECOVERY_MODE"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    SAFE_FREEZE = "SAFE_FREEZE"
    STOPPING = "STOPPING"


class Event(BaseModel):
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    actor_id: str
    run_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any]
    payload_hash: str
    created_at: datetime
    machine_id: str
    schema_version: str = "v1"


class ArtifactRef(BaseModel):
    path: str
    sha256: str
    content_type: str
    created_at: datetime


class OrderIntent(BaseModel):
    intent_id: str
    execution_intent_id: str | None = None
    proposal_id: str
    strategy_id: str
    mode: Literal["paper", "live"]
    venue: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type: str
    quantity: str
    limit_price: str | None = None
    reduce_only: bool = False
    status: str
    client_order_id: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class ReconciliationDiff(BaseModel):
    diff_type: str
    severity: Literal["info", "warning", "critical"]
    local_ref: dict[str, Any] | None = None
    remote_ref: dict[str, Any] | None = None
    message: str
    recommended_action: str


class ReconciliationReport(BaseModel):
    reconciliation_report_id: str
    gateway_session_id: str
    status: Literal["ok", "diff_found", "frozen", "failed"]
    local_state_hash: str | None = None
    remote_state_hash: str | None = None
    diffs: list[ReconciliationDiff]
    actions: list[dict[str, Any]]
    artifact_path: str | None = None
    created_at: datetime
```

---

## 9. EventStore 设计

实现文件：

```text
aletheia/storage/event_store.py
```

### 9.1 接口

```python
class EventStore:
    def append(self, event: Event) -> None: ...
    def append_many(self, events: list[Event]) -> None: ...
    def get(self, event_id: str) -> Event | None: ...
    def list_by_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[Event]: ...
    def list_since(self, created_at: datetime) -> list[Event]: ...
    def list_by_correlation(self, correlation_id: str) -> list[Event]: ...
```

### 9.2 写入规则

每次写入 event：

```text
1. canonicalize payload JSON
2. sha256(payload_json)
3. 检查 idempotency_key 是否已存在
4. DB transaction 写 event_log
5. 同步 append 到 events/event_log_YYYY-MM.jsonl
```

### 9.3 事件类型

最低实现以下事件：

```text
SYSTEM_STARTED
SYSTEM_STOPPING

GATEWAY_STARTED
GATEWAY_RECOVERY_STARTED
GATEWAY_RECOVERY_COMPLETED
GATEWAY_SAFE_FREEZE_ENTERED
GATEWAY_SAFE_FREEZE_EXITED
GATEWAY_STOPPING

RECONCILIATION_STARTED
RECONCILIATION_DIFF_FOUND
RECONCILIATION_COMPLETED

AGENT_RUN_STARTED
AGENT_RUN_CHECKPOINTED
AGENT_RUN_COMPLETED
AGENT_RUN_FAILED
AGENT_RUN_ORPHANED

MARKET_SNAPSHOT_CREATED
ANALYSIS_ARTIFACT_CREATED
EVIDENCE_COLLECTED
CLAIM_EXTRACTED
STRATEGY_SPEC_CREATED
VALIDATION_STARTED
VALIDATION_COMPLETED
PROPOSAL_CREATED
PROPOSAL_APPROVED
PROPOSAL_REJECTED
PROPOSAL_EXPIRED

EXECUTION_INTENT_CREATED
ORDER_INTENT_CREATED
ORDER_SUBMITTED
ORDER_ACKED
ORDER_REJECTED
ORDER_PARTIALLY_FILLED
ORDER_FILLED
ORDER_CANCEL_REQUESTED
ORDER_CANCELLED
ORDER_STATUS_UNKNOWN

POSITION_OPENED
POSITION_UPDATED
POSITION_CLOSED
ORPHAN_POSITION_IMPORTED

REVIEW_REPORT_CREATED
LESSON_CREATED
SKILL_CREATED
POLICY_VERSION_CREATED
POLICY_VERSION_ACTIVATED

EXPORT_CREATED
IMPORT_DRY_RUN_COMPLETED
IMPORT_COMPLETED
BACKUP_CREATED
BACKUP_VERIFIED
RESTORE_DRY_RUN_COMPLETED
RESTORE_COMPLETED
```

---

## 10. ArtifactStore 设计

实现文件：

```text
aletheia/storage/artifact_store.py
```

### 10.1 接口

```python
class ArtifactStore:
    def put_json(self, relative_path: str, obj: dict) -> ArtifactRef: ...
    def put_yaml(self, relative_path: str, obj: dict) -> ArtifactRef: ...
    def put_text(self, relative_path: str, text: str, content_type: str = "text/plain") -> ArtifactRef: ...
    def put_jsonl(self, relative_path: str, rows: list[dict]) -> ArtifactRef: ...
    def get_text(self, ref: ArtifactRef) -> str: ...
    def verify(self, ref: ArtifactRef) -> bool: ...
    def make_manifest(self, bundle_path: str, refs: list[ArtifactRef], metadata: dict) -> ArtifactRef: ...
```

### 10.2 Artifact 写入规则

```text
1. 路径必须在 workspace/artifacts 下。
2. 写入必须 atomic：先写 .tmp，再 rename。
3. 每个文件写入后计算 sha256。
4. bundle 必须有 manifest.yaml。
5. manifest 必须记录 object id、schema version、created_at、machine_id、hashes。
```

### 10.3 Proposal Bundle

每个 proposal 生成：

```text
artifacts/proposals/YYYY-MM-DD/{proposal_id}/
  manifest.yaml
  proposal.yaml
  strategy_spec.yaml
  evidence.jsonl
  claims.jsonl
  market_snapshots.jsonl
  analysis.md
  validation_report.json
  backtest_summary.json
  paper_run_summary.json
  risk_report.md
  approval.json
  execution_plan.yaml
  review.md
```

`manifest.yaml` 示例：

```yaml
bundle_id: bundle_prop_btc_breakout_001
proposal_id: prop_btc_breakout_001
schema_version: v1
created_at: 2026-06-13T10:00:00+08:00
created_by: dreaming_agent
machine_id: office_5880

objects:
  - type: proposal
    id: prop_btc_breakout_001
    path: proposal.yaml
  - type: strategy_spec
    id: spec_btc_breakout_001
    path: strategy_spec.yaml
  - type: validation_report
    id: val_btc_breakout_001
    path: validation_report.json

hashes:
  proposal.yaml: sha256:...
  strategy_spec.yaml: sha256:...
  evidence.jsonl: sha256:...
```

---

## 11. Gateway 持久化设计

实现文件：

```text
aletheia/gateway/state_store.py
aletheia/gateway/recovery.py
aletheia/gateway/reconciliation.py
aletheia/gateway/locks.py
```

### 11.1 OrderIntent 和 Order 分离

Agent 不能直接下单。必须走：

```text
Proposal
  ↓
ExecutionIntent
  ↓
OrderIntent
  ↓
Gateway Submit
  ↓
Venue Order
  ↓
Fill
  ↓
Position
```

### 11.2 client_order_id 生成

格式：

```text
aletheia_{env}_{proposal_id}_{leg_id}_{attempt}
```

示例：

```text
aletheia_paper_prop_btc_breakout_001_entry_01
aletheia_live_prop_eth_basis_002_exit_01
```

生成函数：

```python
def make_client_order_id(env: str, proposal_id: str, leg_id: str, attempt: int) -> str:
    raw = f"aletheia_{env}_{proposal_id}_{leg_id}_{attempt:02d}"
    return raw[:64]
```

如交易所长度限制更短，需要做 hash suffix：

```python
def make_client_order_id(env, proposal_id, leg_id, attempt, max_len=32):
    raw = f"aletheia_{env}_{proposal_id}_{leg_id}_{attempt:02d}"
    if len(raw) <= max_len:
        return raw
    suffix = sha256(raw.encode()).hexdigest()[:8]
    prefix = raw[: max_len - 9]
    return f"{prefix}_{suffix}"
```

### 11.3 GatewayStateStore 接口

```python
class GatewayStateStore:
    def create_execution_intent(...): ...
    def create_order_intent(...): ...
    def mark_order_submitted(...): ...
    def mark_order_acked(...): ...
    def mark_order_rejected(...): ...
    def mark_order_unknown(...): ...
    def record_fill(...): ...
    def update_position_from_fill(...): ...
    def close_position(...): ...
    def get_open_orders(...): ...
    def get_open_positions(...): ...
    def get_unresolved_intents(...): ...
```

### 11.4 Transaction 要求

以下动作必须在 DB transaction 中完成：

```text
创建 order_intent + 写 ORDER_INTENT_CREATED event
mark submitted + 写 ORDER_SUBMITTED event
record fill + 更新 order + 更新 position + 写 fill/position events
进入 SAFE_FREEZE + 写 gateway session + 写 event
```

---

## 12. Gateway 状态机

### 12.1 状态

```text
STOPPED
STARTING
RECOVERY_MODE
RUNNING
DEGRADED
SAFE_FREEZE
STOPPING
```

### 12.2 状态转换

```text
STOPPED -> STARTING
STARTING -> RECOVERY_MODE
RECOVERY_MODE -> RUNNING
RECOVERY_MODE -> SAFE_FREEZE
RUNNING -> DEGRADED
RUNNING -> SAFE_FREEZE
DEGRADED -> RUNNING
DEGRADED -> SAFE_FREEZE
SAFE_FREEZE -> RECOVERY_MODE
RUNNING -> STOPPING
STOPPING -> STOPPED
```

### 12.3 SAFE_FREEZE 动作权限

| 动作 | 是否允许 |
|---|---:|
| 查询账户 | 是 |
| 查询订单 | 是 |
| 查询持仓 | 是 |
| 同步成交 | 是 |
| 更新本地状态 | 是 |
| 撤销挂单 | 是 |
| 人工确认后平仓降风险 | 是 |
| 新开仓 | 否 |
| 增加仓位 | 否 |
| Agent 自主创建 live intent | 否 |
| 修改硬风控 | 否 |
| 修改 live unlock | 否 |

---

## 13. Gateway Recovery Protocol

### 13.1 启动流程

```text
Gateway process start
  ↓
load config
  ↓
connect metadata DB
  ↓
connect event store
  ↓
connect artifact store
  ↓
create gateway_session(status=STARTING)
  ↓
write GATEWAY_STARTED
  ↓
acquire gateway lock if mode=live
  ↓
enter RECOVERY_MODE
  ↓
write GATEWAY_RECOVERY_STARTED
  ↓
fetch exchange open orders
  ↓
fetch exchange positions
  ↓
fetch recent fills since last_seen_fill_ts
  ↓
reconcile fills
  ↓
reconcile orders
  ↓
reconcile positions
  ↓
create reconciliation_report artifact
  ↓
if unresolved diff:
    enter SAFE_FREEZE
else:
    enter RUNNING
```

### 13.2 RecoveryService 伪代码

```python
async def recover_gateway_state(gateway_id: str, mode: str) -> RecoveryResult:
    session = session_store.start(gateway_id=gateway_id, mode=mode)
    event_store.append(make_event("GATEWAY_RECOVERY_STARTED", session))

    if mode == "live":
        lock_ok = await lock_service.acquire("live_gateway", gateway_id)
        if not lock_ok:
            return enter_safe_freeze(session, reason="live_gateway_lock_unavailable")

    local_open_orders = state_store.get_open_orders(mode=mode)
    local_open_positions = state_store.get_open_positions(mode=mode)
    local_unresolved_intents = state_store.get_unresolved_intents(mode=mode)

    remote_open_orders = await exchange.fetch_open_orders()
    remote_positions = await exchange.fetch_positions()
    remote_recent_fills = await exchange.fetch_recent_fills(since=state_store.last_seen_fill_ts())

    fill_diffs = await reconcile_fills(remote_recent_fills)
    order_diffs = await reconcile_orders(local_open_orders, remote_open_orders, local_unresolved_intents)
    position_diffs = await reconcile_positions(local_open_positions, remote_positions)

    report = build_reconciliation_report(
        session=session,
        diffs=fill_diffs + order_diffs + position_diffs,
    )
    artifact_ref = artifact_store.put_json(
        f"reconciliation_reports/{today()}/{report.reconciliation_report_id}.json",
        report.model_dump(mode="json"),
    )
    state_store.save_reconciliation_report(report, artifact_ref)

    if report_has_unresolved_critical_diff(report):
        return enter_safe_freeze(session, reason="reconciliation_diff", report=report)

    session_store.mark_running(session.gateway_session_id)
    event_store.append(make_event("GATEWAY_RECOVERY_COMPLETED", session))
    return RecoveryResult(status="ok", report=report)
```

---

## 14. Reconciliation 规则

### 14.1 订单对账

| 本地状态 | 远端状态 | 处理方式 |
|---|---|---|
| 本地 open order | 远端 open order 存在 | 更新本地状态为 open / acked |
| 本地 open order | 远端 filled | 补写 fill，更新 position |
| 本地 open order | 远端 cancelled | 标记 cancelled，写 event |
| 本地 open order | 远端查不到 | 查 recent fills；仍查不到则 `ORDER_STATUS_UNKNOWN` + freeze |
| 本地 submitted 未 ack | 远端按 client_order_id 找到 | 补 ack，继续对账 |
| 本地 submitted 未 ack | 远端找不到 | 禁止盲目重发；标记 unknown + freeze |
| 本地无订单 | 远端有 Aletheia client_order_id | 导入 recovered order |
| 本地无订单 | 远端有非 Aletheia 订单 | 标记 external order，默认 freeze 或忽略，取决于配置 |

### 14.2 成交对账

| 情况 | 处理方式 |
|---|---|
| 远端 fill 本地已存在 | 跳过 |
| 远端 fill 本地不存在，但 client_order_id 可识别 | 补写 fill，更新 order 和 position |
| 远端 fill 本地不存在，订单也不存在 | 导入为 orphan fill，freeze |
| fill 数量超过订单数量 | critical diff，freeze |
| fee 缺失 | warning，允许继续 |

### 14.3 持仓对账

| 本地状态 | 远端状态 | 处理方式 |
|---|---|---|
| 本地 open position | 远端相同 | 更新 unrealized pnl |
| 本地 open position | 远端无持仓 | 查 fills；能解释则 close，本地补事件；不能解释则 freeze |
| 本地无 position | 远端有 Aletheia 相关持仓 | 导入 orphan position，freeze |
| 本地数量与远端不一致 | 尝试用 recent fills 修复；不能修复则 freeze |
| 本地方向与远端相反 | critical diff，freeze |

### 14.4 ReconciliationReport 示例

```json
{
  "reconciliation_report_id": "recon_20260613_001",
  "gateway_session_id": "gw_session_001",
  "status": "frozen",
  "diffs": [
    {
      "diff_type": "LOCAL_ORDER_REMOTE_MISSING",
      "severity": "critical",
      "local_ref": {
        "order_id": "ord_001",
        "client_order_id": "aletheia_live_prop_btc_001_entry_01"
      },
      "remote_ref": null,
      "message": "Local order is open but exchange does not return it and no matching recent fill was found.",
      "recommended_action": "Manual inspection required. Gateway entered SAFE_FREEZE."
    }
  ],
  "actions": [
    {
      "type": "ENTER_SAFE_FREEZE",
      "reason": "reconciliation_diff"
    }
  ]
}
```

---

## 15. Gateway Leader Lock

如果有两台电脑都可能启动 live gateway，必须加 leader lock。

### 15.1 规则

```text
只有持有 live_gateway lock 的实例可以提交 live order。
lock 每 10 秒 heartbeat。
lease_until 默认 30 秒。
抢占 lock 后必须先 recovery。
```

### 15.2 伪代码

```python
async def acquire_gateway_lock(lock_name: str, owner_id: str) -> bool:
    now = utcnow()
    lock = db.get_lock(lock_name)

    if lock is None or parse_dt(lock.lease_until) < now:
        db.upsert_lock(
            lock_name=lock_name,
            owner_id=owner_id,
            lease_until=now + timedelta(seconds=30),
            heartbeat_at=now,
        )
        return True

    if lock.owner_id == owner_id:
        db.renew_lock(lock_name, owner_id, now + timedelta(seconds=30))
        return True

    return False
```

### 15.3 Lock 丢失时行为

```text
立即停止提交新订单
写 GATEWAY_LOCK_LOST event
进入 DEGRADED 或 SAFE_FREEZE
尝试撤销尚未提交的本地 intent
已提交订单只做查询和对账
```

---

## 16. Agent 长任务 Checkpoint

### 16.1 背景

Agent 分析可能持续很久，例如：

```text
扫描全市场
抓取证据
抽取 claim
生成 hypothesis
生成 strategy spec
跑验证
生成 proposal
```

重启后不应从头开始，也不应重复写入同一批 evidence / claim。

### 16.2 Checkpoint 步骤

建议标准步骤：

```text
market_data_collected
claims_extracted
hypotheses_generated
strategy_spec_created
validation_completed
proposal_created
review_created
lessons_created
```

### 16.3 执行模式

```python
async def run_with_checkpoints(run_id: str):
    if not checkpoint_store.exists(run_id, "market_data_collected"):
        result = await collect_market_data()
        checkpoint_store.save(run_id, "market_data_collected", result)

    if not checkpoint_store.exists(run_id, "claims_extracted"):
        result = await extract_claims()
        checkpoint_store.save(run_id, "claims_extracted", result)

    if not checkpoint_store.exists(run_id, "hypotheses_generated"):
        result = await generate_hypotheses()
        checkpoint_store.save(run_id, "hypotheses_generated", result)

    if not checkpoint_store.exists(run_id, "strategy_spec_created"):
        result = await create_strategy_spec()
        checkpoint_store.save(run_id, "strategy_spec_created", result)

    if not checkpoint_store.exists(run_id, "validation_completed"):
        result = await validate_strategy_spec()
        checkpoint_store.save(run_id, "validation_completed", result)

    if not checkpoint_store.exists(run_id, "proposal_created"):
        result = await create_proposal()
        checkpoint_store.save(run_id, "proposal_created", result)
```

### 16.4 Orphaned Run 检测

启动时扫描：

```text
agent_runs.status = running
AND heartbeat_at < now - orphan_timeout
```

处理：

```text
标记为 orphaned
写 AGENT_RUN_ORPHANED event
如果 run 配置 resumable=true，则创建 resumed run
从最后 checkpoint 继续
```

---

## 17. 市场分析和世界模型持久化

### 17.1 必须保存的内容

每次 Agent 做市场分析时，必须保存：

```text
输入 market snapshot ids
输入 claim ids
输入 policy version
输入 prompt template hash
使用的 model / parameters
tool trace path
输出 analysis summary
输出 opportunity ids
输出 risk notes
输出 proposal ids
```

### 17.2 AnalysisArtifact 示例

```yaml
analysis_id: analysis_20260613_001
agent_name: market_research_agent
run_id: run_20260613_001
model:
  provider: openai
  name: gpt-5.5-pro
  temperature: 0.2
prompt_template_id: catalyst_research_v3
prompt_hash: sha256:...
input_snapshot_ids:
  - snap_btc_001
input_claim_ids:
  - claim_001
  - claim_002
policy_version: scanner_policy_20260612
output_summary: "BTC breakout candidate with high liquidity but late-entry risk."
opportunity_ids:
  - opp_btc_001
risk_notes:
  - "Funding elevated."
  - "Late entry penalty medium."
artifact_path: artifacts/analyses/2026-06-13/analysis_20260613_001.yaml
created_at: 2026-06-13T10:00:00+08:00
```

### 17.3 World Model Snapshot

实现可选表：

```sql
CREATE TABLE IF NOT EXISTS world_model_snapshots (
    world_snapshot_id TEXT PRIMARY KEY,
    snapshot_ts TEXT NOT NULL,
    policy_version_id TEXT,
    market_regime_json TEXT NOT NULL,
    portfolio_state_json TEXT NOT NULL,
    active_claim_ids_json TEXT NOT NULL,
    active_opportunity_ids_json TEXT NOT NULL,
    risk_state_json TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL
);
```

用途：

```text
重启后恢复系统认知状态
复盘某个 proposal 当时的市场认知
对比策略生成前后 world model 是否漂移
```

---

## 18. Evidence / Claim 规则

### 18.1 EvidenceDoc

每条 evidence 必须有：

```text
evidence_id
source_type
source_ref
published_at
fetched_at
content_hash
quality_score
artifact_path
raw_metadata
```

### 18.2 Claim

每条 claim 必须有：

```text
claim_id
text
subject
predicate
object
confidence
price_relevant
evidence_ids
claim_hash
created_at
valid_from
valid_until
```

### 18.3 Claim 进入 Proposal 的条件

```text
必须有 evidence_id
必须有 fetched_at
若用于事件驱动交易，必须有 published_at 或明确来源时间
必须通过 claim verifier
不能引用 proposal 创建之后的 evidence
```

### 18.4 幻觉处理

| 类型 | 定义 | 系统动作 |
|---|---|---|
| supported | claim 有可验证来源 | 允许进入 proposal |
| weak_supported | 单一弱来源 | 只能作为 hypothesis |
| time_inconsistent | 引用决策后资料 | proposal invalid |
| unsupported | 找不到来源 | 标记 hallucination，冻结 proposal |

---

## 19. Lesson / Skill 设计

### 19.1 Lesson 类型

```text
market_lesson
strategy_lesson
execution_lesson
risk_lesson
system_lesson
agent_lesson
```

### 19.2 Lesson YAML 示例

```yaml
lesson_id: lesson_20260613_001
source_type: paper_run
source_id: paper_btc_breakout_001
category: execution_slippage
severity: medium
summary: "回测表现为正，但 paper 成交滑点显著高于估计。"
evidence:
  backtest_slippage_bps: 5
  paper_slippage_bps: 22
recommendation:
  type: policy_update
  target: scanners.tradability.slippage_weight
  change: "+0.10"
status: proposed
created_at: 2026-06-13T23:30:00+08:00
```

### 19.3 Skill YAML 示例

```yaml
skill_id: skill_claim_verifier_v1
name: catalyst_claim_verifier
scope: research
description: "验证事件驱动交易 claim 是否有足够证据支持。"
inputs:
  - evidence_docs
  - claims
outputs:
  - verified_claims
  - unsupported_claims
risk_level: low
allowed_layers:
  - research
  - proposal_builder
forbidden_layers:
  - live_execution
  - hard_risk
created_from_lessons:
  - lesson_20260613_001
version: 1
status: active
```

### 19.4 Skill 权限限制

Skill 不允许：

```text
直接下单
直接修改 hard risk limit
直接提高杠杆
直接绕过 proposal
直接解除 SAFE_FREEZE
```

---

## 20. Policy Versioning

### 20.1 Policy 类型

```text
scanner_policy
family_quota_policy
ttl_policy
tradability_policy
prompt_template_policy
risk_limit_policy
approval_policy
market_whitelist_policy
```

### 20.2 权限表

| Policy | Agent 可提议 | Agent 可自动应用 | 人工确认 |
|---|---:|---:|---:|
| scanner weights | 是 | paper 可 | live 建议确认 |
| family quota | 是 | paper 可 | live 建议确认 |
| TTL | 是 | paper 可 | live 建议确认 |
| prompt template | 是 | 否 | 是 |
| tradability threshold | 是 | 否 | 是 |
| market whitelist | 是 | 否 | 是 |
| risk limit | 否 | 否 | 是 |
| live unlock | 否 | 否 | 是 |
| API permission | 否 | 否 | 是 |

### 20.3 Policy 更新规则

```text
所有 policy 变更必须创建新 version。
禁止原地覆盖 active policy。
policy diff 必须可读。
policy activation 必须写 event。
从 lesson 生成的 policy 只能是 draft。
```

---

## 21. Scheduler / Job 恢复

### 21.1 Job 入库

不要只依赖系统 cron。任务本身必须入库。

```text
scheduled_jobs
job_runs
```

### 21.2 Worker 执行流程

```text
扫描 due jobs
尝试获取 lease
创建 job_run
执行任务
写 checkpoint / event
完成后更新 next_run_at
释放 lease
```

### 21.3 幂等规则

每个 job run 必须有：

```text
job_id
schedule_window
attempt
idempotency_key
```

同一时间窗口只允许成功一次。

---

## 22. Export / Import 设计

### 22.1 Export 模式

```bash
aletheia export --mode config-only --output exports/config_20260613.tar.zst
aletheia export --mode research-pack --since 2026-06-01 --output exports/research_20260613.tar.zst
aletheia export --mode proposal-pack --proposal-id prop_btc_001 --output exports/prop_btc_001.tar.zst
aletheia export --mode runtime-snapshot --output exports/runtime_20260613.tar.zst
aletheia export --mode full-backup --output exports/full_20260613.tar.zst
aletheia export --mode redacted-share --proposal-id prop_btc_001 --output exports/share_prop_btc_001.tar.zst
```

### 22.2 模式内容

| 模式 | 内容 | 排除 |
|---|---|---|
| config-only | config、policy、prompt template | secrets、runtime |
| research-pack | evidence、claims、analysis、lessons、skills | orders、positions、secrets |
| proposal-pack | 单个 proposal 完整证据包 | secrets |
| runtime-snapshot | open orders、positions、gateway state | secrets |
| full-backup | 除 secrets 外全部 | secrets |
| redacted-share | 脱敏 proposal / research | account、position、secrets、个人信息 |

### 22.3 Export Bundle 结构

```text
aletheia_export_20260613/
  manifest.yaml
  db/
    schema.sql
    data.sqlite
    event_log.jsonl
  artifacts/
    proposals/
    lessons/
    skills/
    evidence/
    claims/
    analyses/
  config/
  policies/
  checksums.txt
  README.md
```

### 22.4 manifest.yaml

```yaml
export_id: export_20260613_001
created_at: 2026-06-13T22:00:00+08:00
created_by: machine_office_5880
aletheia_version: 0.1.0
schema_version: v1
mode: full-backup

contains:
  proposals: 42
  evidence_docs: 1390
  claims: 4200
  lessons: 88
  skills: 12
  open_orders: 0
  open_positions: 0

secrets_included: false
```

### 22.5 Import 命令

```bash
aletheia import exports/full_20260613.tar.zst --dry-run
aletheia import exports/full_20260613.tar.zst --merge
aletheia import exports/full_20260613.tar.zst --merge --resolve-policy-conflicts manual
```

### 22.6 Dry-run 输出

```text
Import Preview:
  New proposals: 12
  New evidence docs: 430
  New claims: 1200
  New lessons: 18
  Duplicate objects skipped: 890
  Policy conflicts: 2
  Runtime state included: no
  Secrets included: no

Conflicts:
  policy scanner_weights_v4 differs from local scanner_weights_v4
  prompt_template catalyst_research_v2 differs by hash
```

### 22.7 冲突策略

| 对象 | 策略 |
|---|---|
| evidence | content_hash 去重 |
| claim | claim_hash 去重 |
| proposal | 同 id 同 hash 跳过；同 id 不同 hash 创建 conflict copy |
| lesson | 同 source + summary hash 去重 |
| skill | 同 name 不同 hash 创建新 version |
| policy | 必须人工选择 |
| order | 默认不导入 runtime state，除非 runtime-snapshot |
| position | 默认不导入 runtime state，除非 runtime-snapshot |
| secret | 永不导入 |

---

## 23. Backup / Restore 设计

### 23.1 备份级别

| 备份 | 频率 | 内容 |
|---|---:|---|
| hourly | 每小时 | DB、event_log、orders、positions、proposals |
| daily | 每日 | DB、events、artifacts、config、policies |
| weekly | 每周 | full backup |
| monthly | 每月 | cold archive |
| secret | 手动 | 加密密钥备份 |

### 23.2 命令

```bash
aletheia backup create --level hourly
aletheia backup create --level daily
aletheia backup verify backups/aletheia_daily_20260613.tar.zst
aletheia backup restore backups/aletheia_daily_20260613.tar.zst --dry-run
aletheia backup restore backups/aletheia_daily_20260613.tar.zst --target workspace_restored
```

### 23.3 备份校验

```text
解压到临时目录
校验 checksums.txt
校验 manifest.yaml
校验 DB schema version
校验 artifact hashes
校验 event_log payload_hash
生成 verify report
```

### 23.4 3-2-1 策略

```text
3 份副本
2 种介质
1 份异地
```

具体建议：

```text
本机 workspace
NAS / 移动硬盘
云端对象存储 / VPS / 私有服务器
```

---

## 24. 两台电脑同步策略

### 24.1 推荐方式：Home Server

```text
Home Server:
  PostgreSQL
  Artifact Store
  Scheduler
  Gateway
  Worker
  API

Laptop A:
  开发
  前端
  API client

Laptop B:
  开发
  前端
  API client
```

优点：

```text
长时间运行状态只有一个事实源
不会出现双 gateway 抢单
不会同步 SQLite 冲突
对 live 最安全
```

### 24.2 本地优先方式

如果暂时没有 Home Server：

```text
Machine A 运行 agent → export research/proposal bundle
Machine B import bundle → deduplicate → rebuild index
```

禁止：

```text
两台机器同时写同一个 SQLite 文件
两台机器同时跑同一个 live gateway
两台机器共享可交易 API key 但没有 leader lock
```

### 24.3 可同步目录

```text
artifacts/
exports/
policies/      # 通过 conflict resolution
prompt_templates/
```

### 24.4 不同步目录

```text
db/            # 不直接文件同步，除非离线 backup restore
events/        # 可以 append-only merge，但 MVP 建议通过 export/import
config/local/
config/secrets/
cache/
tmp/
```

---

## 25. CLI 设计

实现文件：

```text
aletheia/cli/main.py
```

### 25.1 workspace

```bash
aletheia workspace init ./workspace
aletheia workspace verify
aletheia workspace doctor
```

### 25.2 gateway

```bash
aletheia gateway start --mode paper
aletheia gateway start --mode live
aletheia gateway status
aletheia gateway recover
aletheia gateway freeze --reason manual
aletheia gateway unfreeze --confirm
aletheia gateway reconcile --dry-run
```

### 25.3 artifacts

```bash
aletheia artifact verify artifacts/proposals/2026-06-13/prop_btc_001/manifest.yaml
aletheia artifact rebuild-index
```

### 25.4 export / import

```bash
aletheia export --mode full-backup --output exports/full.tar.zst
aletheia import exports/full.tar.zst --dry-run
aletheia import exports/full.tar.zst --merge
```

### 25.5 backup

```bash
aletheia backup create --level daily
aletheia backup verify backups/daily.tar.zst
aletheia backup restore backups/daily.tar.zst --dry-run
```

### 25.6 policy

```bash
aletheia policy list
aletheia policy diff policy_v1 policy_v2
aletheia policy activate policy_v2
```

---

## 26. API 设计

FastAPI endpoints：

```text
GET  /health
GET  /gateway/status
POST /gateway/recover
POST /gateway/freeze
POST /gateway/unfreeze
GET  /gateway/reconciliation/latest

GET  /orders/open
GET  /positions/open
GET  /events/{aggregate_type}/{aggregate_id}

GET  /proposals
GET  /proposals/{proposal_id}
GET  /proposals/{proposal_id}/bundle

GET  /lessons
GET  /skills
GET  /policies
POST /policies/{policy_version_id}/activate

POST /exports
POST /imports/dry-run
POST /imports/merge

POST /backups
POST /backups/verify
POST /backups/restore-dry-run
```

---

## 27. Implementation Phases

## Phase 0 — Project Skeleton and Workspace Init

目标：建立目录结构、配置加载、DB 初始化、migration。

任务：

```text
T0.1 创建 aletheia/storage 包
T0.2 实现 WorkspacePaths
T0.3 实现 workspace init
T0.4 实现 SQLite connection manager
T0.5 实现 migration runner
T0.6 加入 .gitignore template
```

交付文件：

```text
aletheia/storage/workspace.py
aletheia/storage/db.py
aletheia/storage/migrations/0001_init.sql
aletheia/cli/main.py
```

验收标准：

```text
aletheia workspace init ./workspace 可以成功创建目录
aletheia workspace verify 可以检查目录和 DB
DB 中存在所有 MVP 表
重复 init 不破坏已有 workspace
```

---

## Phase 1 — Hash, Manifest, ArtifactStore

目标：所有研究产物可文件化、可校验。

任务：

```text
T1.1 实现 canonical_json
T1.2 实现 sha256_file / sha256_json
T1.3 实现 ArtifactRef
T1.4 实现 ArtifactStore.put_json / put_yaml / put_text / put_jsonl
T1.5 实现 atomic write
T1.6 实现 manifest.yaml 生成
T1.7 实现 artifact verify
```

交付文件：

```text
aletheia/storage/hashing.py
aletheia/storage/artifact_store.py
aletheia/storage/manifest.py
```

验收标准：

```text
写入 artifact 后可得到 sha256
修改文件后 verify 失败
bundle manifest 可列出全部文件和 hash
```

---

## Phase 2 — EventStore

目标：关键动作 append-only 化。

任务：

```text
T2.1 实现 Event model
T2.2 实现 EventStore.append
T2.3 实现 idempotency_key 唯一约束
T2.4 实现 DB + JSONL 双写
T2.5 实现 aggregate timeline query
T2.6 实现 correlation query
```

交付文件：

```text
aletheia/storage/event_store.py
aletheia/storage/events.py
```

验收标准：

```text
同 idempotency_key 重复 append 不产生重复 event
可以按 proposal_id 查完整事件链
JSONL event 和 DB event 数量一致
```

---

## Phase 3 — Gateway StateStore

目标：订单、成交、持仓不依赖进程内存。

任务：

```text
T3.1 实现 ExecutionIntent repository
T3.2 实现 OrderIntent repository
T3.3 实现 Order repository
T3.4 实现 Fill repository
T3.5 实现 Position repository
T3.6 实现事务性 record_fill
T3.7 每个状态变化写 event
```

交付文件：

```text
aletheia/gateway/state_store.py
aletheia/gateway/models.py
```

验收标准：

```text
创建 order_intent 后进程重启仍可读取
record_fill 会更新 order 和 position
重复 fill 不会重复记账
get_open_orders / get_open_positions 正确
```

---

## Phase 4 — Gateway Lock and Session

目标：防止双 live gateway。

任务：

```text
T4.1 实现 GatewaySessionStore
T4.2 实现 GatewayLockService
T4.3 实现 heartbeat
T4.4 lock lost 时进入 DEGRADED / SAFE_FREEZE
```

交付文件：

```text
aletheia/gateway/session.py
aletheia/gateway/locks.py
```

验收标准：

```text
两个 gateway_id 同时申请 live_gateway lock 只有一个成功
lease 过期后另一个可以接管
接管后必须触发 recovery
```

---

## Phase 5 — Recovery / Reconciliation

目标：gateway 重启后自动恢复或冻结。

任务：

```text
T5.1 定义 ExchangeAdapter interface
T5.2 实现 mock exchange adapter
T5.3 实现 reconcile_fills
T5.4 实现 reconcile_orders
T5.5 实现 reconcile_positions
T5.6 实现 ReconciliationReport artifact
T5.7 实现 GatewayRecoveryService
T5.8 实现 SAFE_FREEZE 状态
```

交付文件：

```text
aletheia/gateway/exchange_adapter.py
aletheia/gateway/reconciliation.py
aletheia/gateway/recovery.py
aletheia/gateway/freeze.py
```

验收场景：

```text
S5.1 无 open order / position，启动成功进入 RUNNING
S5.2 本地 open order，远端 open order，恢复成功
S5.3 本地 open order，远端已成交，补写 fill
S5.4 本地 submitted 未 ack，远端按 client_order_id 找到，补 ack
S5.5 本地 open order，远端查不到，进入 SAFE_FREEZE
S5.6 远端有持仓，本地没有，导入 orphan position 并 SAFE_FREEZE
```

---

## Phase 6 — AgentRun and Checkpoint

目标：长时间 Agent 分析任务可恢复。

任务：

```text
T6.1 实现 AgentRunStore
T6.2 实现 CheckpointStore
T6.3 实现 heartbeat
T6.4 实现 orphaned run detector
T6.5 实现 resumable run wrapper
```

交付文件：

```text
aletheia/agents/run_store.py
aletheia/agents/checkpoints.py
aletheia/agents/resumable.py
```

验收标准：

```text
kill agent 后重启，running run 标记 orphaned
resumable task 从最后 checkpoint 继续
不会重复写入同一 claim_hash
```

---

## Phase 7 — Research Persistence

目标：市场分析、证据、claim、proposal 可复盘。

任务：

```text
T7.1 实现 EvidenceDoc repository
T7.2 实现 Claim repository
T7.3 实现 MarketSnapshot repository
T7.4 实现 AnalysisArtifact repository
T7.5 实现 StrategySpec repository
T7.6 实现 ValidationReport repository
T7.7 实现 Proposal repository
T7.8 实现 ProposalBundleBuilder
```

交付文件：

```text
aletheia/research/repositories.py
aletheia/proposals/bundle_builder.py
```

验收标准：

```text
每个 proposal 都有完整 bundle
bundle manifest verify 通过
可以从 proposal 追溯 evidence、claim、market snapshot、validation report
```

---

## Phase 8 — Lesson / Skill / PolicyVersion

目标：经验教训和可复用技能可以沉淀、迁移、版本化。

任务：

```text
T8.1 实现 Lesson repository
T8.2 实现 Skill repository
T8.3 实现 PolicyVersion repository
T8.4 实现 policy diff
T8.5 lesson -> draft policy proposal
T8.6 skill permission validator
```

交付文件：

```text
aletheia/learning/lessons.py
aletheia/learning/skills.py
aletheia/policies/store.py
aletheia/policies/diff.py
```

验收标准：

```text
review 可以生成 lesson
lesson 可以生成 draft policy
policy activation 必须创建新 version
skill forbidden_layers 生效
```

---

## Phase 9 — Export / Import

目标：两台电脑之间迁移研究资产和 proposal。

任务：

```text
T9.1 实现 ExportService
T9.2 实现 ImportDryRunService
T9.3 实现 ImportMergeService
T9.4 实现 dedup by hash
T9.5 实现 policy conflict detection
T9.6 实现 redacted-share
T9.7 CLI 命令接入
```

交付文件：

```text
aletheia/sync/exporter.py
aletheia/sync/importer.py
aletheia/sync/conflicts.py
```

验收标准：

```text
Machine A 导出 proposal-pack，Machine B 导入后可打开完整 proposal
重复 import 不产生重复 evidence / claim
policy 冲突会提示
secret 默认不导出
```

---

## Phase 10 — Backup / Restore

目标：灾备可用。

任务：

```text
T10.1 实现 BackupService
T10.2 实现 backup manifest
T10.3 实现 checksum generation
T10.4 实现 backup verify
T10.5 实现 restore dry-run
T10.6 实现 restore to new workspace
```

交付文件：

```text
aletheia/backup/service.py
aletheia/backup/verify.py
aletheia/backup/restore.py
```

验收标准：

```text
从 daily backup 可恢复一个新 workspace
恢复后 artifact hash 全部通过
恢复后 proposal bundle 可打开
恢复后 DB index 可重建
```

---

## Phase 11 — API and UI Hooks

目标：为前端展示和人工处理提供接口。

任务：

```text
T11.1 gateway status API
T11.2 reconciliation report API
T11.3 event timeline API
T11.4 proposal bundle API
T11.5 import dry-run API
T11.6 backup verify API
```

验收标准：

```text
前端能展示 gateway 状态
能展示 SAFE_FREEZE 原因
能查看 proposal / order / position 的事件链
能查看 import 冲突
```

---

## 28. Test Plan

### 28.1 Unit Tests

```text
test_hashing.py
test_artifact_store.py
test_event_store.py
test_gateway_state_store.py
test_reconciliation.py
test_checkpoint_store.py
test_export_import.py
test_backup_restore.py
test_policy_diff.py
```

### 28.2 Integration Tests

#### Test A: Gateway 正常重启

```text
1. 创建 order_intent
2. mark submitted
3. mock exchange 返回 open order
4. kill gateway
5. restart gateway
6. recovery 后进入 RUNNING
7. open order 仍存在
```

#### Test B: 重启前订单成交

```text
1. 本地有 open order
2. gateway 关闭
3. mock exchange 返回 recent fill
4. restart gateway
5. recovery 补写 fill
6. position 更新
7. 进入 RUNNING
```

#### Test C: 远端状态缺失

```text
1. 本地有 open order
2. mock exchange 查不到 order，也无 fill
3. restart gateway
4. recovery 生成 critical diff
5. 进入 SAFE_FREEZE
```

#### Test D: Orphan position

```text
1. 本地无 position
2. mock exchange 返回 Aletheia 相关 position
3. recovery 导入 orphan position
4. 进入 SAFE_FREEZE
```

#### Test E: Agent checkpoint

```text
1. 启动 agent run
2. 完成 claims_extracted checkpoint
3. kill process
4. restart
5. run 标记 orphaned
6. resume 从 hypotheses_generated 开始
```

#### Test F: Export / Import

```text
1. Machine A 创建 proposal bundle
2. export proposal-pack
3. Machine B import dry-run
4. Machine B merge
5. 验证 evidence / claim / proposal / validation 全部可查
6. 重复 import 不重复
```

#### Test G: Backup / Restore

```text
1. 创建 full backup
2. verify 通过
3. restore 到新 workspace
4. workspace doctor 通过
5. proposal bundle verify 通过
```

---

## 29. Observability and Alerts

### 29.1 必须记录的日志

```text
gateway state transition
lock acquire / renew / lost
order submit / ack / fill / cancel
reconciliation diff
SAFE_FREEZE reason
agent run start / checkpoint / complete / failed
export / import result
backup / verify result
```

### 29.2 指标

```text
gateway_status
open_orders_count
open_positions_count
reconciliation_diff_count
safe_freeze_count
agent_orphaned_runs_count
event_append_latency_ms
artifact_write_latency_ms
backup_age_hours
last_successful_recovery_ts
```

### 29.3 告警

```text
Gateway enters SAFE_FREEZE
Live gateway lock lost
Reconciliation critical diff found
Backup failed
DB migration failed
Artifact hash verification failed
Order status unknown
Agent run orphaned for too long
```

---

## 30. Security Requirements

### 30.1 Secret Store

MVP：

```text
.env + 文件权限限制
```

建议升级：

```text
OS Keychain
1Password CLI
Bitwarden CLI
Vault
cloud secret manager
```

### 30.2 最小权限

```text
paper key 和 live key 分离
只给交易子账户权限
withdrawal 权限永远关闭
不同环境不同 API key
```

### 30.3 Export Redaction

`redacted-share` 必须移除：

```text
account id
API key
wallet address 可选脱敏
position quantity 可选脱敏
PnL 可选脱敏
personal notes 可选脱敏
raw tool trace 中的 secret
```

---

## 31. Migration Strategy

### 31.1 SQLite -> PostgreSQL

Repository 接口必须不暴露 SQLite 细节。

禁止：

```text
业务代码直接 sqlite3.execute
```

允许：

```text
Repository / UnitOfWork / SQLAlchemy session
```

### 31.2 ArtifactStore -> S3 / MinIO

ArtifactStore 接口保持不变：

```python
put_json
put_yaml
put_text
put_jsonl
verify
make_manifest
```

本地实现和 S3 实现可替换。

---

## 32. Definition of Done

整个持久化与恢复系统完成时，必须满足：

```text
1. Gateway 可以被 kill -9 后重启。
2. 重启后不会丢 open order。
3. 重启后不会丢 position。
4. 重启前已成交但本地未记录的 fill 可以通过交易所补写。
5. 状态无法解释时自动 SAFE_FREEZE。
6. Agent 长任务可以从 checkpoint 继续。
7. Proposal 有完整 artifact bundle。
8. Lesson / Skill 可以导出导入。
9. Policy 变更可版本化、可 diff、可回滚。
10. Export / Import 支持 dry-run 和冲突检测。
11. Full backup 可以恢复新 workspace。
12. Secret 默认不会进入 export / backup。
13. 两台电脑不会同时无锁提交 live order。
14. 所有关键动作都能在 event timeline 中追溯。
```

---

## 33. 给实现 Agent 的完整执行提示词

将下面提示词交给代码实现 Agent：

```text
You are implementing the Aletheia Persistence, Sync, Recovery, and Export/Import subsystem.

Core goal:
The trading gateway, agents, and workers must be restart-safe. No open order, position, fill, proposal, market analysis, claim, lesson, skill, policy version, or important runtime record should be lost when the process restarts.

Implement the system according to this plan.

Non-negotiable rules:
1. Process memory is not a source of truth.
2. Gateway startup must enter RECOVERY_MODE before RUNNING.
3. Live gateway must hold live_gateway leader lock before submitting live orders.
4. All orders must use deterministic client_order_id and idempotency_key.
5. Unknown order or position mismatch must trigger SAFE_FREEZE.
6. Agent skills and lessons may update research-layer policies only, never hard live risk controls.
7. Secrets must never be exported by default.
8. Runtime state is never trusted alone; exchange reconciliation is required.
9. All critical state changes must write append-only events.
10. All important research outputs must be artifactized with manifest and sha256.

Implementation order:
Phase 0: workspace init, DB schema, migrations.
Phase 1: hashing, manifest, ArtifactStore.
Phase 2: EventStore.
Phase 3: GatewayStateStore.
Phase 4: GatewaySession and GatewayLock.
Phase 5: Recovery and Reconciliation with mock exchange tests.
Phase 6: AgentRun and Checkpoint.
Phase 7: Research persistence and ProposalBundleBuilder.
Phase 8: Lesson, Skill, PolicyVersion.
Phase 9: Export / Import.
Phase 10: Backup / Restore.
Phase 11: API hooks.

Use SQLite for MVP but keep repository interfaces compatible with PostgreSQL.
Use local filesystem for ArtifactStore but keep interface compatible with S3 / MinIO.
Add unit tests and integration tests for recovery, reconciliation, export/import, and backup/restore.
Do not implement live order submission until gateway lock, idempotency, recovery, and SAFE_FREEZE are implemented.
```

---

## 34. Agent 执行时的优先级

如果实现资源有限，按以下顺序做：

```text
P0. Workspace init + DB schema
P0. EventStore
P0. ArtifactStore
P0. GatewayStateStore
P0. client_order_id / idempotency
P0. GatewayRecoveryService
P0. SAFE_FREEZE
P0. ProposalBundleBuilder
P1. Agent checkpoint
P1. Export / Import
P1. Backup / Restore
P1. Lesson / Skill
P2. UI / API
P2. PostgreSQL / S3 upgrade
```

第一版必须先保证：

```text
重启不丢订单
重启不丢持仓
重启后可对账
对账失败会冻结
proposal 和经验可导出
```

---

## 35. 最终架构判断

这个系统的核心不是“把数据同步到另一台电脑”，而是：

```text
每个有价值的状态都对象化。
每个对象都有 schema。
每次变化都写 event。
每个研究产物都有 artifact。
每个 artifact 都有 hash。
每次重启都能 recovery。
每次不一致都能 freeze。
每次迁移都能 dry-run。
每次备份都能 verify。
```

这样 Aletheia 就不是一个依赖单个进程记忆的交易 bot，而是一个可恢复、可迁移、可审计、可复盘的 Agentic Trading Workspace。
