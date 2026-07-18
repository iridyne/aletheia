# Proposal-Flow 非终态 defer & FTS 数据完整性修复（2026-07-18）

> **Policy epoch**: `system_operator_skip_remediation` v1.4 → **v1.5**
> **Producer commit（修复实施时）**: `11d435cd` (base)
> **锁定 spec**: `DL-INV-2` (updated), `DL-INV-24` (new), `DL-S47..50` (new),
> `tests/specs/spec_operator_low_conviction_defer.py`

## 背景

commit `11d435cd` 上暴露的两个正交问题：

1. **Proposal flow stalled**（决策语义）
   - Opportunity/Ranker/`signal_pending` 通路正常。
   - TRUMP/USDT 2026-07-18 08:48Z：模型返回 `action=trade` + `conviction=52`；
     bull=42/bear=72 debate 使 `conviction=43 < 55`；既有代码把 `action` 覆写为
     终态 `skip`，Opportunity 被消费为 `reviewed`，无 Proposal。
   - Prompt 里 `Given my recent 7-day performance, should I be more or less
     aggressive?` 与前面的护栏（"单笔近期表现不得授权 candidate skip"）矛盾;
     判词旁路从 direct-skip 通道转移到 conviction/debate 通道，绕过 v1.4 DL-INV-21。
   - `doctor loop.profit` 用 12h 的 raw `pi_decisions.action` 计数，混入
     `producer_commit=69d9ba6d` 的 5 条旧 skip，掩盖当前 epoch 阻点。
2. **FTS coverage = 10 %**（数据完整性）
   - 682 条 `high_confidence + active` 中 611 条 file_path 指向旧 Windows/POSIX
     `data/knowledge/`，本地找不到；只有 71 条可索引。
   - `manager.create_*` 只落 file_path，不落 DB `content` —— 一旦磁盘迁移，
     索引整体丢。
   - `doctor _probe_fts_coverage` 拿 611 当分母把结果显示成红色，让人误以为
     "reindex 没跑完"。

## 目标（受契约保护，不动的东西）

- `_MIN_CONVICTION_TO_TRADE = 55` 不变（DL-C01）。
- `_DEBATE_RANGE = [40, 80]` 与 debate 权重 `0.3` 不变。
- Risk/Execution/Bitget 客户端不改。
- 不新增交换所/基础设施；不引入向量检索。

## 实施摘要（八 phase）

| Phase | 目标 | 关键代码 |
|-------|------|---------|
| 0 | 前置检查（read-only） | `git status`, `sqlite3 knowledge_fts / learning_policy_epochs` |
| D1 | scan-recovery-sources CLI（read-only） | `aletheia/knowledge/recovery.py` + `cli/knowledge_cmd.py` |
| A1 | 统一 pipeline_progress 聚合器（epoch scoped） | `aletheia/diagnostics/pipeline_progress.py` |
| A2/A3 | doctor loop.profit + fts.coverage 改用新语义 | `aletheia/diagnostics/doctor.py:_probe_profit_loop / _probe_fts_coverage` |
| A4 | Overview API 消费同一聚合 | `aletheia/analytics/opportunity_progress.py::_epoch_summary_snapshot` |
| D3/D4 | maintenance 复用 `_read_body`；reindex 先 DB 后 disk | `aletheia/knowledge/{maintenance,retrieval}.py` |
| E1/E2 | 5 个 high-confidence writer 双写 `content`；`backfill-content` CLI | `aletheia/knowledge/manager.py`, `aletheia/knowledge/maintenance.py::backfill_content_from_disk` |
| E3 | 双写 + 路径迁移单测 | `tests/test_knowledge_dual_write.py`, `tests/test_knowledge_recovery.py` |
| B | Prompt 删除矛盾指令 + 加 conviction bypass 反旁路规则 | `trading_decision.py:1868-` |
| C1 | conviction<55 → `DecisionUnavailable(operator_low_conviction \| debate_not_confirmed)` | `trading_decision.py:499- / :548-` |
| C2 | `commands.evaluate_trading_opportunity` 把两个新 stage 归入 defer；`event_kind='operator_decision_deferred_low_conviction'`；dedup_key 按 event_kind 分裂 | `agent_shell/commands.py:1707-, :1957-` |
| C3 | `skip_policy.py` version → **b1.5**（新增 4 字段 + 3 affected paths） | `aletheia/agent_shell/skip_policy.py` |
| C4 | RED spec: TRUMP 复现 + 无 debate 低 conviction + 反旁路 + dedup 分裂 | `tests/specs/spec_operator_low_conviction_defer.py` |
| F | 契约 + 决策文档 + 本 plan | `docs/design/contracts/decision_lifecycle_contract.md`, `docs/design/operator/PI_OPERATOR_DESIGN_DECISIONS.md` |

## 六桶归因（诊断口）

`aletheia/diagnostics/pipeline_progress.summarize` 把 filter_decisions + operator_evaluation_events + pi_decisions
按 **当前 `producer_commit` + 当前 `policy_epoch_id`** 分为：

- `direct_model_skip`（`operator_model_skip`）
- `conviction_gate_skip`（v1.4 `conviction_below_minimum` + v1.5 `operator_low_conviction`）
- `debate_not_confirmed`（v1.4 `debate_below_min_conviction` + v1.5 `debate_not_confirmed`）
- `operator_defer`（`operator_evaluation_events.event_kind LIKE 'operator_decision_deferred_%'`）
- `context_unavailable`
- `ranker_cooldown`（stage LIKE 'ranker%' AND reason_code LIKE '%cooldown%'）
- 补充：`direction_brake`, `operator_trade_intent`（作为分子）

Overview 通过 `analytics.opportunity_progress::_epoch_summary_snapshot` 收到同一 payload；
doctor 的 `loop.profit` 输出：

```
loop.profit  PROPOSAL FLOW STALLED: ... — epoch commit=<sha7> policy=<epoch_id>;
             operator (12h) direct_skip×N/conviction_gate×N/debate_reject×N/defer×N/
             direction_brake×N/trade_intent×N
```

## 知识数据恢复步骤

用户建议"先尝试恢复正文，暂缓降级"。执行链条：

1. `uv run aletheia knowledge scan-recovery-sources --show-candidates 20`
   - 输出：43 条 `strategy=existing`（磁盘已有文件、DB content 空 → backfill 目标）；
     611 条纯 Windows path，无候选（`unrecoverable`）。
2. `uv run aletheia knowledge backfill-content --dry-run`
   - 报告 `matched=120`（含 medium_confidence 的额外 77 条）。
3. `uv run aletheia knowledge backfill-content`
   - 把磁盘文件正文写回 `knowledge_items.content`，触发 `reindex_all()`。
4. 剩余 611 条不可恢复；由用户决定是否 `downgrade-unindexable-high-confidence`
   （已改为使用 `_read_body` 的 Windows/POSIX 迁移逻辑，不会误伤仍可迁移的记录）。

Writer 已双写：新增记录不会再次陷入"DB content 空 + file_path 指向失效路径"的困境。

## 验收（本地）

```bash
# 核心单测
uv run pytest tests/specs/spec_operator_low_conviction_defer.py \
              tests/specs/spec_decision_lifecycle.py \
              tests/test_knowledge_dual_write.py \
              tests/test_knowledge_recovery.py -q

# 诊断
uv run aletheia doctor 2>&1 | grep -E "loop.profit|fts.coverage|knowledge.unindexable"
uv run aletheia knowledge scan-recovery-sources --show-candidates 5
```

期望：新 spec 全绿；`loop.profit` 只按当前 epoch 归因；`fts.coverage` 使用可重建分母；
`knowledge.unindexable_high_confidence` 独立 warn，611 条丢失正文不再伪装成
"reindex 没跑完"。

## 与 v1.4 的差异（affected paths）

policy_epoch `system_operator_skip_remediation` 从 `b1.4` → `b1.5`，
新增 4 个 effective_values + 3 个 affected_path_ids：

- `min_conviction_terminal_skip=False`
- `debate_not_confirmed_terminal_skip=False`
- `conviction_derived_from_candidate_specific_evidence_only=True`
- `conviction_bypass_reasons_forbidden=True`
- + affected_paths: `operator_low_conviction_defer_semantics`,
  `operator_debate_not_confirmed_defer_semantics`,
  `operator_conviction_bypass_reason_ban`

## 收尾附加改动（同日追加）

### Knowledge 精确恢复
按 owner 决策：只 backfill 1 条真正有交易学习价值的 `trade_case_study`（ZEC/USDT
long stop_loss），其余 653 条（42 条本地 Risk Lesson audit stub + 611 条丢失正文）
统一 downgrade 到 `medium_confidence`，audit 行完整保留。执行后 FTS 从 72 → 30
纯净知识池（29 `operator_assessment` + 1 ZEC case study），`retrieve_lessons("ZEC
stop loss momentum long")` 命中 score 0.98。

### risk_critic 污染源修复
`aletheia/agents/risk_critic.py:59` 原本"只要有 proposal 就 high_confidence"的门槛
产生了 39 条 "1/1 approved (100%)" 模板 audit stub。改成**一律 medium_confidence**：
输入是聚合统计、输出是宏观建议，不属于 candidate-specific 检索池。真正的交易学习
通道是 `trade_case_study`（factor_key / holding_hours / pnl 结构化归因）。
测试 `test_risk_critic_promotes_substantive_lessons` → `test_risk_critic_writes_medium_confidence_only`
锁死新行为。

### Follow-up backlog（未做，写进文档）
- **Overview UI 六桶可视化**：后端 `analytics.opportunity_progress` 已返回
  `epoch_summary` 字段，前端未接入。见
  [`docs/issues/backlog/OVERVIEW_EPOCH_SUMMARY_UI_BACKLOG.md`](../../issues/backlog/OVERVIEW_EPOCH_SUMMARY_UI_BACKLOG.md)。

## Out-of-scope（不做）

- 不调阈值（55 / 40-80 / 0.3）。
- 不把 FTS 修复作为"恢复开仓"的前置——B/C 与 D/E 完全独立解锁两个问题。
- 不引入向量检索或改动 knowledge 存储 schema。
- 不动 trust boundary（Risk/Execution/Bitget/secrets/permissions/registry）。
- 不改 Overview 前端（属于 backlog，非本轮 scope）。
