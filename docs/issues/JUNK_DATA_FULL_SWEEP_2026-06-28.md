# 垃圾数据全量审查 — 讨论包装（scaffold，非终稿）

> 承接 owner 指令 B：对 issue #3 背后**所有数据库表 + 所有长期写入文件**做一次完整审查，确保没有更多"垃圾数据大量写入 → 最终污染 AI Agent 判断"的隐患。
> 这是**讨论起点**，不是判决。需交互式逐项确认，**先不急**。
> 前序终稿：`JUNK_DATA_REVIEW_2026-06-27.md`（A 轴 system_action 已清，无第二个）。
> 本文只记**结构与意图**（代码事实）；运行态数字（行数/体积）在对话里给，不进 git。

---

## 0. 关键框架：两条轴，别混

垃圾数据有两种,危害与处置完全不同——分轴讨论能大幅降低判断负担：

| 轴 | 是什么 | 危害 | 处置 |
|----|--------|------|------|
| **轴1 体积/成本** | 高频快照、telemetry、session 转写、jsonl 日志 | 占盘、查询变慢、VACUUM 压力 | 保留策略 + prune + VACUUM。**不污染判断**（只是原始行情/遥测） |
| **轴2 语义毒性** | 喂学习/PI 的因果链数据被幻觉/错归因污染 | **直接腐蚀 AI 决策** ← owner 的真实担忧 | 断源、修归因、加质量门。**体积小但致命** |

> 经验法则：轴1 大而无害，轴2 小而致命。Round 1 塌方的根因（池1890:16 幻觉喂 ranker、pi_decisions.opp_id 全 NULL 断归因）都在**轴2**。

---

## 1. 数据库表清单（app.db）

### 1a. 有 prune（scheduler `_prune` job，repositories.py + scheduler.py:1118-1140 已接线）
覆盖：agent_runs / run_steps / tool_calls / market_snapshots / portfolio_snapshots /
orderbook_snapshots / liquidation_events / funding_rate_snapshots / open_interest_snapshots /
macro_snapshots / coingecko_top_snapshots / universe_feature_snapshots / serenity_claims /
shadow_trades / pi_decisions(transient action) / agent_shell_heartbeats/events/messages/artifacts /
news_items。market_ohlcv 走 insert 时 rolling-window（另一机制）。
→ **prune job 确在运行**（heartbeats 稳定在 ~2 天窗即证据）。

### 1b. ⚠️ 无 prune 路径（轴1 缺口，确认）
- **`universe_forward_outcomes`** — 无 prune 函数、不在 scheduler dict。前向收益标注表，analytics 性质（非因果链），可安全 prune，但当前**无界增长**。← **缺口#1**

### 1c. 因果链/信任表（**不可 prune**，轴2 关注点）
opportunities / trade_proposals / risk_decisions / orders / fills / positions /
pi_decisions(causal) / knowledge_items / strategy_records / serenity_*。
→ 这些**不删行**，但要问：**写进去的内容本身是否是幻觉/错归因？**

---

## 2. 长期写入文件清单（data/ 下）

| 路径 | 写入者 | 轮转/清理? |
|------|--------|-----------|
| `data/logs/*.jsonl`（market_snapshots / agent_runs / audit / watchdog / errors） | 各子系统结构化日志 | 有 `retention.jsonl_max_mb` 轮转（jsonl.py）。**待确认是否对每个文件生效**（market_snapshots.jsonl 偏大） |
| `data/agent_shell/sessions/*.jsonl`（operator-main / review-* / strategy-*） | agent shell 每会话转写 | ⚠️ **无清理路径**（确认）。← **缺口#2**，目录最大 |
| `data/events/event_log_YYYY-MM.jsonl` | 事件总线 | 按月分文件，自然有界 |
| `data/knowledge/` | 知识库导出 | 配 knowledge/maintenance.py（待核保留策略） |

### 疑点
- **double-write**：`market_snapshots` 同时写 DB 表（有 prune）+ `data/logs/market_snapshots.jsonl`。jsonl 是否冗余镜像、是否轮转？
- 轴1 占盘大头不在 DB 单表，而在 **session 转写目录** + jsonl 日志。

---

## 3. 已知轴2 隐患（携带前序记忆，待逐项裁定）
- `pi_decisions.opp_id` 运行态仍**全 NULL** → signal 侧归因链仍断（代码加了字段但 app.db 实测未生效）。
- 学习闭环历史喂幻觉（池1890:16 穿过冻结 ranker）——Round 1 塌方主因，需确认修复是否运行态生效。
- knowledge_items 是否含 Round 1 幻觉残留（需抽样）。

---

## 4. 审查方法（建议）
1. **轴1**：每张表/每个文件 → 写入者、频率、保留策略（有无/是否生效）、是否喂判断。缺口补 prune + 一次性 VACUUM 回收。
2. **轴2**：对因果链表**抽样读内容**，验"写进去的是不是真信号"；重点查 NULL 归因、幻觉残留、质量门方向。
3. 验证看**运行态实测**（doctor / 直接 query），不看"文档说已修"。

---

## 5. 待 owner 决策的开放问题
1. `universe_forward_outcomes` 保留窗口定多久？（建议 30d，与同类快照一致）
2. `data/agent_shell/sessions/*.jsonl` 清理策略：按天数 or 按"会话已归档"？保留多久？
3. `market_snapshots.jsonl` double-write 是否要砍掉（DB 已是唯一真源）？
4. 轴2 抽样范围：先抽 knowledge_items 还是先修 pi_decisions.opp_id 归因？
5. VACUUM（#13）何时做——需安静窗口（停 gateway + 无并发会话）。

---

---

## 6. 决议（2026-06-28 落地）

### 轴1 缺口（solo，已全部补完 + 验证零回归）
- **缺口#1 `universe_forward_outcomes`** → 加 `prune_universe_forward_outcomes(30d)` + 进 scheduler dict（#15 GREEN）。
- **缺口#2 `data/agent_shell/sessions/*.jsonl`** → `file_artifacts.prune_session_logs(14d, 豁免 operator-main, mtime-based)` 接进 `_run_cleanup`（#16 GREEN）。
- **§5 Q3 `market_snapshots.jsonl` double-write** → **砍掉**。全仓 grep 零 reader（只 docs/plans 提及），纯 write-only 让 `data/logs/` 无界增长；DB 表才是 canonical 真源。已删写入 + 未用 import，留 NOTE。可逆 solo-safe，26 market_data 测试通过。

### 轴2 语义毒性（owner 真实担忧）
- **合成平仓污染语料(本轮新发现) → 已修(solo,非冻结)**：`reconciled_gone`/`demo_validation_cleanup` 是行政性平仓(exit price 伪造回退值),冒充真实结局喂 trade_case_study/guard。`analytics/trade_case_builder.py` 加 `_is_synthetic_closure` 按 close_reason VALUE 过滤(对账器捞回真 TPSL fill 保留 stop_loss/take_profit 照常入库)。锁 **LP-S08**(GREEN)。doctor `loop.reconciled_ratio` 实测近 50 笔 32% 是这类。
- **`pi_decisions.opp_id` 运行态 NULL（§3 + §5 Q4）→ 已查清并解决(2026-06-28)**：先前"test-vs-prod 分歧/live 链不传"的诊断**经实测全错**。真相:signal 侧透传由 **b9ad957(2026-06-27 12:31)** 加入,gateway 约 **13:10 restart** 后生效。以 13:10 为界看 app.db —— 之前 ranker 信号 100% NULL(旧代码),之后 **0/254 全带值**,链路通的。全表 ~95% NULL 是 13:10 前的陈旧 backlog(transient 行,`prune_pi_decisions(7d)` 自动删)。残留真 NULL 只有两类、且都诚实:**规则触发信号**(本无源 opportunity,且 PiDecision 无 strategy_id 锚) + **skip**(被拒候选,非决策)。**LP-S07 重定义(owner 批准 Option B)**:口径从"近 2 天全 action"收窄到"最近 200 条 ranker 来源 signal_pending,NULL<10% 才绿"——排除自删 backlog + 合法 NULL 的 skip/规则信号,实测 **0% NULL,GREEN**。从此它真正盯住"机会→决策"链是否健康,链断才报红。

### §5 仍属 owner 的项（(C) 终稿建议）
- **Q5 VACUUM（#13）**：无害,纯磁盘回收(389M 中 85M freelist,页复用已让增长有界)。需 EXCLUSIVE 锁→停 gateway。**建议**:不动,owner 挑安静窗口跑一条 `db_maintenance.py --vacuum` 即可,不值得为它停 live。
- **Q4 opp_id 运行态验收 → 已解决(2026-06-28,见上轴2)**：无需解冻——链路在 06-27 13:10 restart 后已自然修好,LP-S07 重定义后实测 GREEN。原"留到解冻当天验"的建议作废。

*状态:轴1 三项 + 轴2 合成平仓项 + opp_id 运行态(LP-S07 重定义 GREEN) = 全部落地验证完毕;仅剩 VACUUM(#13,纯磁盘回收,owner 挑安静窗口跑 `db_maintenance.py --vacuum`)。*
