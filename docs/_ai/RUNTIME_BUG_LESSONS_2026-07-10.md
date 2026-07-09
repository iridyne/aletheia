# 运行时静默劣化问题排查经验总结

> 写于 2026-07-10，来源：IB-24-07～13 批次修复（commits `4bc3707` / `a747f19` / `96f6275` / `6729aa13`）。
> 关联：`docs/issues/RUNTIME_BEHAVIOR_ISSUES.md`

---

## 总体教训：静默劣化比崩溃更危险

本次 6 个问题的共同特征是**系统不崩溃、不报错、表面运行正常，但核心指标在后台持续退化**。
- FTS 索引：89 条而非 1,507 条——AI 历史经验几乎全盲，无任何警报
- volume_scanner：停运 2.6 天——doctor 报 stale，但没有阻断任何流程
- settlement 积压：12,827 行——doctor 只看到最近 78 条，低估 165 倍
- SHORT 压制：debate 每次低估 SHORT 信号——无错误日志，只有结果差

**检测原则**：静默劣化类 bug 只能靠定期"深度体检"发现，doctor 的健康检查覆盖的是崩溃和明显阈值，无法发现"悄悄少了 1,418 条索引"这类问题。建议每 2 周做一次全量指标核对（FTS 行数、settlement 积压、volume_ranking staleness、per-direction trade 比例）。

---

## 1. Python `in` 操作符在字符串上的陷阱

**问题**：
```python
_ALLOWED_CONFIDENCE = "high_confidence"
if row["confidence"] not in _ALLOWED_CONFIDENCE:  # 字符检查！
    continue
```
`"medium_confidence" in "high_confidence"` = `False`，因为 Python 把字符串的 `in` 解释为"子串包含"，不是集合成员检查。

**正确写法**：
```python
_ALLOWED_CONFIDENCE = ("high_confidence", "medium_confidence")  # 元组
if row["confidence"] not in _ALLOWED_CONFIDENCE:  # 成员检查
    continue
```

**教训**：凡是"常量 × `in` 操作"的模式，常量必须是 `tuple` / `set` / `list`，绝不能是 `str`。Code review 时优先检查 `_ALLOWED_*` / `_VALID_*` 类常量的类型。

---

## 2. LLM Prompt 的方向偏置：信号 note 必须跟随 direction

**问题**：`_build_bull_prompt` 和 `_build_bear_prompt` 里的信号解读 note 硬编码了 LONG 语义：
```
"Note: FR<-0.01% = crowded shorts → bullish..."
```
评估 SHORT 机会时，这段 note 误导 LLM——明明 FR 为负是 SHORT 的支持因素，却被描述为"看多信号"。

**修复模式**：
```python
def _build_bull_prompt(symbol, direction, ctx):
    _dir = direction or "long"
    if _dir == "short":
        note = "FR>+0.01% = crowded longs → supports short..."
    else:
        note = "FR<-0.01% = crowded shorts → bullish..."
```

**教训**：LLM prompt 里任何涉及方向语义的描述都要做分支。"bull/bear 是绝对方向"和"bull/bear 是相对于本次交易方向"是两种完全不同的语义，混用会造成系统性偏差，且极难从结果反推（debate 分数只是数字，看不出偏置原因）。

**次生教训（workflow fix agent 犯的错）**：Audit 报告说"prompts are direction-aware: false"，Fix agent 误以为公式要改，把 `advantage = bull - bear` 改成 SHORT 下 `advantage = bear - bull`。这是错的——公式本身没有方向依赖，依赖的是 prompt 语义。Fix 之前必须先搞清"这个 flag 说的是什么层面的 direction-awareness"。

---

## 3. 调度器共享时间预算下的执行顺序陷阱

**问题**：`_async_data_collectors()` Phase B 所有 collector 共享同一个 wall-time deadline (`_DC_DEADLINE_S=40s`)。Phase A 先消耗大部分预算，Phase B 剩余时间不足时，`_bounded()` 静默跳过排在后面的 collector。

**关键细节**：
- 没有任何错误日志——`_bounded()` 超时是正常控制流，不是异常
- volume_scanner 排在 CoinGecko 和 macro 之后，优先级最低
- 网络稍慢的 tick 就会触发，导致 volume_scanner "间歇性"不执行，日积月累变成"几乎从不执行"

**修复原则**：**重要的 collector 必须排在最前面**。如果一个 collector 对 universe 排序有直接影响（volume_scanner），它的优先级应该高于辅助数据（CoinGecko market cap、macro 指标）。

**教训**：共享时间预算 + 静默跳过 = 必须靠 staleness 监控来发现问题。每个 collector 的 `last_run` 都应该有 staleness 告警，阈值应该设为预期间隔的 2-3 倍，而不是几天。

---

## 4. 单次批次处理永远追不上持续增长的队列

**问题**：`settle_due(limit=500)` 每 15 分钟调用一次。系统每分钟产生 N 条新的 forward outcome，单次处理速度 < 生产速度时，积压永远不会清空。

**数学**：12,827 行积压 ÷ 500/次 × 15min/次 = 6.5 小时清空窗口；但在这 6.5 小时内又新增了 6.5h × N 条，实际永远追不上。

**修复模式**：在单次调度调用内循环消费，直到队列清空或触达时间预算：
```python
async def settle_due(max_rows=2000, time_budget_sec=100.0):
    deadline = monotonic() + time_budget_sec
    while total < max_rows:
        if monotonic() >= deadline:
            break
        batch = fetch(limit=500)
        if not batch:
            break  # 队列已空
        process(batch)
```

**教训**：任何"周期性批次处理"的设计都要回答：**如果积压量 > 单次处理量，系统能自愈吗？** 如果答案是"不能"，就需要循环模式 + 时间预算保护（防止单次调用超过 scheduler 超时）。

---

## 5. 数据库 WHERE 子句遗漏字段导致的幽灵记录

**问题**：`expire_stale_opportunities()` 的 UPDATE 只检查 `created_at < 48h`，完全忽略了 `valid_until` 字段。一个 TTL=30min 的机会可以保持 `open` 状态长达 48 小时。

**影响**：漏斗统计（多少 opp 因 TTL 到期、多少进入 signal_pending）全部基于 `status='open'` 或 `status='expired'` 的计数，幽灵 open 记录会系统性高估"漏斗深度"，掩盖真实的转化率。

**教训**：设计过期逻辑时，要枚举所有可能表达"过期"的字段，不能只用兜底的 `created_at` 阈值。`valid_until` 字段如果存在就必须被尊重，否则等于给了字段却不用。

---

## 6. Doctor 监控的覆盖盲区

本次发现 doctor 对两类问题检测不足：

**盲区 1：只看最近 N 条 vs. 全量积压**
doctor 报 `78 forward outcomes past due`（查最近窗口），DB 实查 12,827 条。监控查询用了 `LIMIT` 或时间窗口，低估了 165 倍。

**改进建议**：settlement 监控应查 `COUNT(*) WHERE settled_at IS NULL AND due_at < now()`（全量），而不是"最近 X 分钟有多少条到期"。

**盲区 2：FTS 行数从不检查**
FTS 索引从 1,507 条悄悄缩水到 89 条（每次 reindex 都是全量重建，理论上不会缩水，但 `medium_confidence` bug 实际上一直存在），doctor 完全不检查 FTS 行数。

**改进建议**：`doctor` 增加 `content.knowledge_fts_count` 检查：对比 `knowledge_items WHERE status='active' AND confidence IN (...)` 的数量 vs. `SELECT COUNT(*) FROM knowledge_fts`，差距超过 10% 报警。

---

## 7. 机器迁移的知识文件路径问题

**问题**：从旧机器（`C:\Users\ollama\...`）迁移到新机器（`C:\Users\Administrator\...`）时，`knowledge_items` 表里的 `file_path` 仍然是旧机器的绝对路径。文件本身在 `aletheia_data.zip` 备份里，但因为路径不匹配，reindex 时全部被识别为"文件不存在"而跳过。

**修复**：从备份 zip 提取文件到新机器对应路径，同时更新 DB 里的 `file_path` 字段。

**教训**：`data/knowledge/` 目录和 DB 里的 `file_path` 必须一起迁移，或者 `file_path` 改用相对路径（相对于 `data/` 根目录），这样跨机器就不会失效。**后续新写入的知识条目应考虑存相对路径**，避免下次迁移再踩同一个坑。

---

## 附：本次发现问题的触发链

```
missed_alpha_suspect（LDO/USDT short 有正收益但被系统淘汰）
  → 排查 debate 机制
      → 发现信号 note 方向硬编码（IB-24-11）
  → 排查 TTL 到期（45% opps 因 TTL 到期）
      → 发现 expire_stale_opportunities 忽略 valid_until（IB-24-13）
      → 发现 neutral opp 浪费管道（IB-24-12）
  → 排查 FTS 知识检索（AI 是否有足够历史经验支撑决策）
      → 发现 _ALLOWED_CONFIDENCE 字符串 bug（IB-24-07）
      → 发现 515 条文件从旧机器未迁移
  → doctor 全面体检
      → 发现 settlement 积压 12,827 条（IB-24-08）
      → 发现 volume_scanner 停运 2.6 天（IB-24-09）
```

单个 alpha miss 事件拉出了 6 个相互独立的系统性 bug。说明：**从结果（错过机会）反推根因的方法论比从代码审查更有效**——代码审查找不到"这个系统性偏差积累了多久"，但结果数据会告诉你。
