# 资料数据库导出功能 — Knowledge Base & LLM 数据

**状态**: backlog
**优先级**: medium
**范围**: backend API / CLI / 数据管道

## 背景

当前 `/api/export` 已经覆盖了交易核心数据（positions / orders / fills / agent_runs / risk_decisions 等 13 张表），但 Aletheia 最有价值的**知识库**和 **LLM 运行数据**完全没有导出能力。要把系统积累的经验、研究笔记、模型成本数据迁移到其他环境分析，目前只能手动拷贝文件 + 直接读 SQLite，没有统一入口。

## 现有导出覆盖情况

### 已覆盖（`aletheia/server/routes/export.py`）

| 类别 | 表 / 数据 |
|---|---|
| 交易因果 | opportunities, pi_decisions, trade_proposals, risk_decisions, orders, fills |
| 持仓结算 | positions, capital_events, paper_account_events |
| Agent 追踪 | agent_runs, agent_run_steps, agent_tool_calls, audit_events |

### 未覆盖（本次目标）

| 类别 | 数据源 | 当前大小 |
|---|---|---|
| **知识库 (Knowledge Base)** | `~/.aletheia/knowledge/` 下所有 `.md` 文件 | ~2.4 MB |
| ↳ 研究笔记 | `research_notes/` | 832 KB, ~120 文件 |
| ↳ 每日复盘 | `daily_reviews/` | 64 KB |
| ↳ 案例研究 | `case_studies/` | 180 KB |
| ↳ 交易后回顾 | `post_trade_reviews/` | 136 KB |
| ↳ 梦境日志 | `dreaming_logs/` | 48 KB |
| ↳ Operator 记忆 | `operator_memory/` | 80 KB |
| ↳ 守卫候选 | `guard_candidates/` | 1.1 MB |
| ↳ 策略 / 证据 | `strategies/`, `evidence/` | 小 |
| **LLM 运行数据** | SQLite 表 | ~120K 行 |
| ↳ 推理调用 | `llm_inference_calls` | 784 行 |
| ↳ 成本事件 | `runtime_cost_events` | 119,064 行 |
| ↳ 定价快照 | `pricing_snapshots` | 4 行 |
| **制品 (Artifacts)** | `~/.aletheia/artifacts/` | 小 |
| ↳ lessons, proposals, reviews, market_snapshots, paper_runs, live_runs 等 | 子目录 | 小 |
| **报告 (Reports)** | `~/.aletheia/reports/` | 小 |
| ↳ 交易回顾、风险评估、系统健康、代理表现 | 子目录 | 小 |

## 需求

### 1. 新增导出数据集

在 `export.py` 的 `_DATASETS` 中追加：

```python
# LLM 数据
("llm_inference_calls",     "llm_inference_calls.csv",     {"analysis", "diagnostic"}),
("runtime_cost_events",     "runtime_cost_events.csv",      {"analysis"}),
("pricing_snapshots",       "pricing_snapshots.csv",        {"analysis", "diagnostic"}),
```

### 2. 知识库导出（新增独立端点或模式）

知识库是 **Markdown 文件**，不是 SQLite 表，需要不同的打包策略：

**选项 A — 纳入现有 ZIP 导出**

在 `/api/export` 的 `mode` 中加入 `"knowledge"` 预设，打包时把 `~/.aletheia/knowledge/` 整个目录作为子目录塞进 ZIP：

```
export_20260808_012345.zip
├── positions.csv
├── agent_runs.csv
├── ...
├── knowledge/
│   ├── research_notes/
│   │   ├── 20260711-094220-risk-lesson.md
│   │   └── ...
│   ├── daily_reviews/
│   ├── case_studies/
│   ├── post_trade_reviews/
│   ├── dreaming_logs/
│   ├── operator_memory/
│   └── ...
└── manifest.json
```

**选项 B — 独立端点 `/api/export/knowledge`**

返回 `.tar.gz` 或 `.zip`，只打包知识库目录，支持 `?type=research_notes,daily_reviews` 过滤子目录。

### 3. 制品 + 报告导出（可选）

同样纳入 ZIP 导出，目录结构：

```
export_xxx.zip
├── ...
├── artifacts/
│   ├── lessons/
│   ├── proposals/
│   ├── market_snapshots/
│   └── ...
└── reports/
    ├── trade_review/
    ├── risk/
    └── ...
```

### 4. 导出格式（用户可选）

| 格式 | 适用场景 | 备注 |
|---|---|---|
| **CSV**（现有） | 表格数据、Excel / pandas 分析 | 默认 |
| **JSONL** | 嵌套结构、日志型数据 | 可选 `?format=jsonl` |
| **Parquet** | 大数据量（runtime_cost_events 12 万行+） | 可选，需新依赖 `pyarrow` |
| **Markdown 原文** | 知识库 | 直接打包，无需转换 |

### 5. 清单文件 (manifest.json)

每次导出在 ZIP 根目录写入：

```json
{
  "version": 2,
  "exported_at": "2026-08-08T01:23:45Z",
  "preset": "analysis|diagnostic|knowledge|full",
  "datasets": ["positions", "agent_runs", "llm_inference_calls", ...],
  "knowledge_types": ["research_notes", "daily_reviews", ...],
  "row_counts": {"positions": 105, "agent_runs": 34885, ...},
  "db_schema": "aletheia-sqlite-v7",
  "git_commit": "fd6a4ae6",
  "redacted": true
}
```

## 影响边界

- **只读操作**：不修改任何运行时状态、不触发调度、不影响交易管道。
- **数据时效性**：导出是快照时间点，不锁定数据库（利用 SQLite WAL 的一致性读）。
- **安全**：config 中的 secret 引用保持 redacted，不导出密钥值。
- **性能**：runtime_cost_events 有 12 万行，导出 ZIP 应在后台任务执行，前端轮询 `/api/export/status/{id}`。

## 工作量估计

| 项 | 复杂度 |
|---|---|
| 追加 LLM 数据集到 `_DATASETS` | 低 |
| 知识库打包（文件 → ZIP） | 中 |
| artifacts / reports 打包 | 低 |
| manifest.json 生成 | 低 |
| 后台任务 + 状态轮询 | 中 |
| CLI 命令 `aletheia data export --mode=full` | 中 |
| 测试 | 中 |

## 验证

- [ ] 新预设 `knowledge` / `full` 通过 `/api/export` 能下载 ZIP
- [ ] ZIP 知识库子目录可被解压后直接阅读
- [ ] `llm_inference_calls.csv` 可被 pandas 读取，行数与 `SELECT COUNT(*)` 一致
- [ ] `manifest.json` 的 row_counts 与实际一致
- [ ] 大表导出（runtime_cost_events 12 万行）在 30s 内完成
- [ ] 导出期间 market_observer / paper_trader 行为不受影响（无锁等待）
