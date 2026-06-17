# Self-Evolution Mechanism — 设计文档

> 状态：Tier 1 实施中 | Tier 2/3 待实施
> 更新：2026-06-17

---

## 背景

Aletheia 的 observer pipeline 已有完整的 forward outcome 追踪（`outcome_recorder.py`），
以及完整的知识库写入/检索路径（`knowledge/manager.py` + FTS5）。
但两者之间没有自动连接——结果数据从未反哺规则，是一个已确认的空白。

自进化机制的目标是关闭这个循环：
```
scanner 选出成员 → register_pending()
  → settle_due() 结算实际表现
    → [Tier 1] 差表现 → guard_candidate 自动生成
    → [Tier 2] 周期性 batch → LLM 归纳规律 → research_note / guard_candidate
    → [Tier 3] 高频好模式 → 提炼为确定性 scanner rule（节省 token）
```

---

## Tier 1：自动 guard_candidate 生成（已实施）

### 目标
`settle_due()` 结算后，对明确差表现的 outcome 自动生成候选规则，供人工确认后升为正式 guard。

### 触发条件（阈值）
| 条件 | 值 | 说明 |
|------|----|------|
| horizon | `4h` | 15m/1h 噪声大；24h 过长；4h 是信号质量最佳的时间窗 |
| forward_return_pct | `< -3.0%` | 方向对齐 long 时：return < -3%；short 时：return > +3% |
| 去重窗口 | 7 天 | 同 symbol + scanner_family 在 7 天内不重复生成 |

### 生成的 guard_candidate 内容结构
```markdown
---
type: guard_candidate
scanner_family: <momentum|mean_reversion|volatility|derivatives>
symbol: BTC/USDT
horizon: 4h
forward_return_pct: -5.23
entry_ts: 2026-06-17T10:00:00Z
outcome_id: abc123def456
confidence: medium_confidence
status: active
tags: [auto_generated, tier1_evolution]
---

## 观察

scanner_family=momentum 选出 BTC/USDT，方向 long，
4h 实际收益 -5.23%（阈值 -3.0%）。

## 信号上下文（结算时复原）

- entry_price_usd: 67234.0
- direction: long
- scanner_family: momentum
- snapshot_id: <id>

## 候选规则（待人工确认）

当 momentum scanner 选出 long 信号，且实际 4h 收益 < -3%，
说明本次入场时机或动量判断存在问题。建议在下次类似形态出现时，
提高确认门槛或跳过入场。

（此条目为自动生成的 guard_candidate，需人工审核后才升为 trading_guard）
```

### 实施位置
`aletheia/universe/outcome_recorder.py`：在 `settle_due()` 末尾调用
新增函数 `_maybe_generate_guard_candidates(settled_rows)`。

### 去重机制
- DB 层：`knowledge_items` 表的 `content_hash`（SHA-256 前 16 位）已有
- 额外保护：查 7 天内是否已有 `item_type=guard_candidate`、title 包含同 symbol+family 的条目

### 数据流图
```
settle_due()
  ├─ settle_forward_outcome() × N        [已有]
  └─ _maybe_generate_guard_candidates()  [新增 Tier 1]
       ├─ 筛选 settled rows：horizon=4h, return < threshold
       ├─ 去重检查（7d 窗口）
       ├─ 构建 guard_candidate content（纯文本，无 LLM）
       └─ knowledge.manager.create_guard_candidate()
            ├─ files/markdown.write_guard_candidate()  → data/knowledge/guard_candidates/
            └─ storage/repositories.insert_knowledge_item()  → app.db
```

### 关键约束
- **无 LLM**：内容由程序构建，不调 LLM，不增加 token 消耗
- **不阻塞结算**：生成失败只 log warning，不影响 `settle_due()` 返回值
- **medium_confidence**：guard_candidate 不可被 FTS 检索（`_index()` 会跳过），
  只落文件 + DB，等待人工确认
- **不改变现有行为**：不修改任何 scanner、observer、paper_trader 逻辑

---

## Tier 2：周期性 LLM 规律归纳（待实施，建议积累 2 周数据后）

### 目标
夜间/低频周期任务：对过去 7 天 settled outcomes 按 scanner_family 分组，
用 LLM 归纳量能/动量/多 TF K 线的规律，生成 `research_note` 或 `guard_candidate`。

### 触发方式
新增 `EvolutionSynthesizerAgent`，APScheduler 每天 UTC 03:00 触发一次。

### 核心逻辑（伪代码）
```python
# 1. 查近 7 天 settled outcomes，按 scanner_family 分组
groups = group_by_family(settled_outcomes_last_7d())

# 2. 每组构建统计摘要
for family, rows in groups.items():
    win_rate = calc_win_rate(rows)          # return > 0 的比例（方向对齐）
    median_return = median(row.return for row in rows)
    bad_examples = [r for r in rows if r.return < -3.0][:5]

    # 3. 让 LLM 从数据中归纳规律
    note = llm_synthesize(family, win_rate, bad_examples)

    # 4. 写入 KB
    if win_rate < 0.4:  # 连续低胜率 → 候选规则
        create_guard_candidate(title=f"{family}-low-winrate-{week}", content=note)
    else:
        create_research_note(title=f"{family}-weekly-{week}", content=note)
```

### 依赖前提
- 至少 50 条 settled outcomes（约 2 周运行）才有统计意义
- 需要 LLM 可用（与 market_observer 共用同一 provider config）

---

## Tier 3：确定性 scanner rule 提炼（待实施，需充足数据）

### 目标
对频繁出现且 outcome 好的 LLM observation pattern，提炼为 `scanners.py` 的确定性评分逻辑，
让 `signal_emitter` 可以跳过 LLM 直接出信号，节省 token。

### 识别路径
```
market_observer 的 key_observations（已存 thesis 字段）
  → 高频短语/条件提取（regex or NLP）
  → 与 4h settled outcome 关联（同 symbol + 时间窗口）
  → 胜率 > 65% 且样本 >= 20 → 候选规则
  → 人工确认后加入 scanners.py 的权重表
```

### 风险
- 样本量要求高（每个 pattern 至少 20 个 settled 样本）
- 需要严格防止过拟合（OOS 验证）
- 改 scanners.py 是改变实际交易逻辑的行为 → 必须人工确认

### 预计实施时间：积累 1-2 个月数据后

---

## 总体演进路线图

```
现在          2周后          1个月后         2个月后
  |              |               |               |
Tier 1        Tier 2         Tier 2         Tier 3
(无LLM)      (LLM归纳)      (验证效果)     (确定性化)
guard_cand   research_note  调整阈值       scanner rules
自动生成     周期生成       迭代优化       节省token
```

---

## 人工确认工作流（Tier 1 + 2 产出的 guard_candidate）

1. `doctor` / 前端 KB 页面查看最新 `guard_candidate` 条目
2. 打开 `data/knowledge/guard_candidates/<title>.md` 审阅内容
3. 确认有效 → 调用 `create_trading_guard()` 升级（或 CLI 工具，待 Tier 2 加）
4. 无效 → `archive_item()` 归档

**当前人工确认入口**：
```bash
# 查看最新候选规则
uv run python -c "
from aletheia.storage.repositories import list_knowledge_items
items = list_knowledge_items(item_type='guard_candidate', limit=10)
for i in items: print(i['id'], i['title'], i['created_at'])
"
```
