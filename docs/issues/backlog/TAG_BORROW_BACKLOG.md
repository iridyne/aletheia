# OpenAlice TaG 借鉴清单 — 落地状态卡

> **创建**: 2026-06-29 / **类型**: backlog 索引(非 issues)
> **触发**: owner 让深度研究 https://www.openalice.ai/blog/trading-as-git-intro 并把"能做、有必要做的"全部 plan + 实施。
> **研究报告**: [`../research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`](../../research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md)（13-agent workflow / 三层 ground truth / 5 镜头 / 5 轮对抗核查）
> **决策记录**: [`../_ai/DECISIONS.md`](../../_ai/decisions/DECISIONS.md) 2026-06-29 条目（5 个设计取舍 + 拒绝清单）
> **历史 review**: [`../references/openalice_prism_review.md`](../../references/external-systems/openalice_prism_review.md)（2026-06-10 一次性分析）+ [`../references/INDEX.md §4`](../../references/INDEX.md) 借鉴清单

> **✅ RECONCILED 2026-06-30**（见 [REMAINING_WORK_2026-06-30 §3](../../status/snapshots/REMAINING_WORK_2026-06-30.md)）：
> **P0-1 + P1-2 已合并入 master**（`b8ed990` / `31eb1a9`，merge `b9a4ac5`；`git branch --no-merged master` 已无此分支）——下表"待 owner 审核 merge"为历史状态。
> **仍 OPEN（状态不变）**：P0-2 / P1-1 / P0-3 均触 trust boundary，待 owner 显式批准。

---

## 一句话核心

**TaG 真正给 Aletheia 的不是 git 比喻包装，而是两个工程纪律：reject 是一等审计记录、每笔执行后账户全量快照绑到决策记录。** 博客的"1 月崩盘预判"是回测幸存者偏差零证据值；"Trade Primitive auto-learn effect" 经源码 grep 命中零纯营销话术——不应作为对标依据。

---

## 借鉴清单状态总表

| ID | 借鉴项 | 落位 | 红线 | 状态 | 接续 |
|---|---|---|---|---|---|
| **P0-1** ⭐ 起手 | `TradeProposal` 加 `commit_hash` + `parent_hash`（16-char SHA-256 prefix，全局单链） | `storage/models.py` + `migrations.py` + `repositories.py` + `tests/specs/spec_data_model.py` | 不触 trust boundary | ✅ **已实施** `b8ed990`（2026-06-29，分支 `worktree-openalice-tag-research`） | 待 owner 审核 merge |
| **P1-2** | reconcile 持仓 audit event 链入 hash chain | `portfolio/position_reconciler.py:372-379` + `tests/specs/spec_pipeline_adopted_position.py` PL-ADOPT-09 | 改动是 audit 字段增量，不改协调逻辑 | ✅ **已实施** `31eb1a9`（2026-06-29，同分支） | 待 owner 审核 merge |
| **P0-2** | 执行后 `state_after` 账户全量快照绑到 audit event | `execution/gateway.py` 已有 audit 写入点加字段；数据来自 `get_cached_state()` | 🔴 trust boundary（`execution/`），最小改动也需 owner 显式批准 | ⏳ **待 owner 批准** | 价值最高的剩余项（复盘从"推算"变"查快照"） |
| **P1-1** | reject 与 push 同结构（三路拒绝补 hash 字段） | `execution/gateway.py:177/317` + `server/routes/data.py:653` | 🔴 trust boundary（`execution/`），需 SDD 先行 | ⏳ **待 owner 批准** | audit 可解释性闭环（"提了多少、拦了多少、过了多少"） |
| **P0-3** | Guard 责任链拆分（`risk/engine.py:77-332` 20 个 check 拆到 `risk/guards/`） | 新建 `aletheia/risk/guards/` + `Guard(Protocol)` + `GuardContext` 只读上下文 + `GUARDS: list[Guard]` 注册表 | 🔴🔴 **红线最严**（`risk/`），行为零变化前后零回归，**严禁趁机塞 market-state 检查**（北极星 §10.3） | ⏳ **待 owner 批准** | 维护性 + 可测性收益高但 ROI 比 P0-2 / P1-1 低 |

---

## 已交叉核查、明确**拒绝**的借鉴项

| 项 | 出处 | 拒绝理由 |
|---|---|---|
| LLM 写 Pandas 直接算指标 | Vibe Trading 帖 #4 | 踩 INDEX.md §4 红线（agent 生成可执行代码）；Aletheia 预算好指标喂 LLM 是更稳健设计 |
| Trade Primitive auto-learn effect | Vibe Trading 帖 #4 | 源码 grep `learn.*effect` 命中零，纯营销话术；且 Aletheia learning loop 现产噪音（memory `aletheia-learning-loop-noise-not-signal`），加新闭环雪上加霜 |
| ReAct 自由 tool 循环 + chat-driven UI + context compaction（`src/core/compaction.ts`） | OpenAlice 整体架构 | 与 trust boundary 设计冲突（agent 不可自由 tool-use）；Aletheia 是 stateless PI 决策，不需要 200k 上下文压缩 |
| JSON 文件取代 SQLite | OpenAlice `commit.json` 全量覆盖写 | 三进程并发会立即翻车（`database is locked` 根因刚修好，memory `aletheia-db-lock-root-cause`） |
| "AI 不能 push" 信任边界搬过来 | OpenAlice `routes-trading.ts` 注释 | Aletheia 是全自动 paper trading（dashboard 审核），已用 `bypass_distress_gate` + `kill_switch` + risk engine 等价机制 |
| "branches" 概念 | 博客的 "version control" 包装 | 博客其实从未明确提；代码也没有（`TradingGit.ts` 是单 `commits: GitCommit[]` 数组 + 单 `head` 指针），对交易场景无业务价值 |
| market-state 检查进 `risk/engine.py` | 任何借鉴若顺手扩 engine | INDEX.md §4 红线 4（来源 north_star.md §10.3），正确落位是 playbooks 层 |

---

## 落地后受影响的契约/spec 锚点（已同步）

| 文档 | 同步内容 | commit |
|---|---|---|
| `docs/design/data/data_model.md` | trade_proposals 表 key columns + Group K 新 blockquote 描述 hash chain 字段语义、生成规则、SDD 锚点 | `67ed379` |
| `docs/design/contracts/audit_chain_contract.md` | §1 范围扩展 / §6.3 加 position_adopted 行 / 新增 §6.4 两链对照表 + 5 不变量 + 校验命令 / 新增 §7.3 doctor 探针 gap | `67ed379` |
| `docs/governance/policies/trust_and_change_control.md` | §1.1 末尾锁定 `_compute_proposal_hash` + `_ADDITIVE_COLUMNS` + `insert_trade_proposal` hash 分支不可独立修改 | `67ed379` |
| `docs/design/README.md` | spec_data_model.py 8→18；补齐 spec_audit_chain.py / spec_pipeline_adopted_position.py 漏列 | `67ed379` |
| `tests/specs/spec_data_model.py` | 新增 DM-S08（L2a pydantic 字段）/ DM-S09（L2a migration 注册）/ DM-B06（L2b insert 自动填）/ DM-C02（L2c re-compute 确定性 + 链完整性 + status 免疫） | `b8ed990` |
| `tests/specs/spec_pipeline_adopted_position.py` | 新增 PL-ADOPT-09（L2b audit event payload 携带 commit_hash + parent_hash） | `31eb1a9` |
| Memory `aletheia-tag-borrow-plan` | 完整落地状态 + 设计取舍 + 拒绝清单 + 后续步骤 | — |

---

## 后续准入条件（owner 批准 P0-2 / P1-1 / P0-3 时的硬性要求）

参考 P0-1 / P1-2 已落地的工作模式：

1. **SDD 先行**：在对应 `spec_*.py` 写 `SpecClaim` + RED 测试 → 实现 → GREEN，**不得跳过**。
2. **行为零变化**（P0-3 Guard 拆分）：重构前后 `uv run pytest tests/specs/ -q` 必须零回归；每条 check 字面量保留逐字（`strategy_lifecycle.py` 子串匹配依赖）。
3. **最小改动**：trust boundary 文件只加字段或拆结构，不改决策逻辑。
4. **严禁趁机扩 scope**：P0-3 重构期间严禁把 market-state 检查塞进 engine（红线 4）。
5. **文档同步**：触 `data_model.md` / `audit_chain_contract.md` / `trust_and_change_control.md` / `design/README.md` 任一时，全部同步更新（CLAUDE.md SDD sync rules 强制）。
6. **memory 更新**：落地后同步 `aletheia-tag-borrow-plan` 的状态。

---

## 一句话总结

P0-1 + P1-2 已落地了 hash 基础设施 + 第一个下游消费者；剩下三项都触 trust boundary，需 owner 显式批准方可推进。**没有任何剩余项是"我直接做就行"的低风险候选**——这是 trust hardening 阶段的应有特性，不是问题。
