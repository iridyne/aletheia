# Self-Evolution Mechanism — 实施计划

> 状态：**Tier 1 已实施**（commit `1a75dc7`，2026-06-17）| Tier 2 计划 2026-07-01 | Tier 3 计划 2026-08-17
> 更新：2026-06-17
> 跟踪入口：ROADMAP.md § Cross-Cutting Track: Self-Evolution

---

## 一句话目标

让 Aletheia 的 observer pipeline 能够从自身运行结果中积累经验，自动生成候选规则并最终内化为确定性分析能力——无需人工手写规则。

---

## 背景与动机

系统已有两条成熟路径，但它们之间没有连接：

| 已有 | 缺失 |
|------|------|
| `universe_forward_outcomes` 追踪每个 scanner 信号的实际 4h 表现 | 差表现从未反哺规则库 |
| `knowledge/manager.py` + FTS5 支持完整的知识写入/检索 | 只有外部（人工/dreaming agent）才会写入 |
| `guards.py` 支持文件驱动的确定性规则扩展 | `guard_candidate` 类型存在但从未被自动填充 |

目标是关闭这个循环：

```
scanner 选出成员 → register_pending()
  → settle_due() 结算实际表现
    → [Tier 1] 差表现 → guard_candidate 自动生成（无 LLM）
    → [Tier 2] 批量统计 → LLM 归纳规律 → research_note / guard_candidate
    → [Tier 3] 高频好模式 → 提炼为确定性 scanner rule（节省 token）
```

---

## Tier 1：自动 guard_candidate 生成

**状态**：✅ 已实施（2026-06-17，commit `1a75dc7`）

### 目标
`settle_due()` 结算后，对差表现的 4h outcome 自动生成候选规则文件，供人工确认后升为正式 guard。零 LLM 调用，零额外 token。

### 触发阈值
| 条件 | 值 | 说明 |
|------|----|------|
| horizon | `4h` | 15m/1h 噪声大；24h 过长；4h 是信号质量最佳的时间窗 |
| long 亏损 | `return < -3.0%` | |
| short 亏损 | `return > +3.0%` | short 方向：上涨为亏损 |
| 去重窗口 | 7 天 | 同 symbol + scanner_family 在 7 天内不重复生成 |

### 实施文件
- `aletheia/universe/outcome_recorder.py`：`_maybe_generate_guard_candidates()` + `_build_guard_candidate_content()`
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent()`
- `tests/test_self_evolution_tier1.py`：10 个单元测试

### 数据流
```
settle_due()
  ├─ settle_forward_outcome() × N        [已有]
  └─ _maybe_generate_guard_candidates()  [Tier 1 新增]
       ├─ 筛选 settled rows：horizon=4h，return 越阈
       ├─ 去重检查（7d 窗口 via repositories）
       ├─ _build_guard_candidate_content()：纯文本，无 LLM
       └─ knowledge.manager.create_guard_candidate()
            ├─ files/markdown → data/knowledge/guard_candidates/
            └─ repositories.insert_knowledge_item() → app.db
```

### 关键约束
- **无 LLM**：内容程序构建，不调 LLM
- **不阻塞结算**：失败只 log warning，不影响 `settle_due()` 返回值
- **medium_confidence**：不可被 FTS 检索，只等待人工确认
- **不改现有行为**：不修改 scanner / observer / paper_trader

---

## Tier 2：周期性 LLM 规律归纳

**状态**：📅 计划实施日期 **2026-07-01**（依赖：≥50 条 settled outcomes，约 2 周数据积累）

### 目标
夜间低频周期任务：对过去 7 天 settled outcomes 按 scanner_family 分组，用 LLM 归纳量能、动量、多 TF K 线的规律，自动生成 `research_note` 或 `guard_candidate`。

### 触发方式
新增 `EvolutionSynthesizerAgent`，APScheduler 每天 UTC 03:00 触发一次。

### 核心逻辑（伪代码）
```python
# 1. 查近 7 天 settled outcomes，按 scanner_family 分组
groups = group_by_family(settled_outcomes_last_7d())

# 2. 每组构建统计摘要 + LLM 归纳
for family, rows in groups.items():
    if len(rows) < 10:
        continue  # 样本不足，跳过
    win_rate = calc_win_rate(rows)        # 方向对齐的胜率
    median_return = median(r["forward_return_pct"] for r in rows)
    bad_examples = [r for r in rows if r["forward_return_pct"] < -3.0][:5]

    note = llm_synthesize(family, win_rate, median_return, bad_examples)

    if win_rate < 0.4:
        create_guard_candidate(title=f"{family}-low-winrate-{week}", content=note)
    else:
        create_research_note(title=f"{family}-weekly-{week}", content=note,
                             confidence="medium_confidence")
```

### 前置依赖
- ≥50 条 `status=settled` 的 `universe_forward_outcomes`（约 2 周运行积累）
- LLM provider 可用（共用 market_observer 配置）
- Tier 1 已运行 ≥1 周，guard_candidate 文件有实际样本可参照

### 实施边界
- 只生成 `research_note`（`medium_confidence`）或 `guard_candidate`
- 不修改 `scanners.py` 或任何交易路径（修改留 Tier 3）
- 生成失败不影响系统运行（同 Tier 1 原则）

---

## Tier 3：确定性 scanner rule 提炼

**状态**：📅 计划实施日期 **2026-08-17**（依赖：≥1 个月数据，Tier 2 已产出有效 research_note）

### 目标
将频繁出现且 outcome 好的 LLM observation pattern 提炼为 `scanners.py` 的确定性评分参数，让 `signal_emitter` 在此类形态下可跳过 LLM 直接出信号，节省 token。

### 识别路径
```
market_observer.thesis 字段（含 key_observations）
  → 高频短语/条件统计（regex）
  → 与 4h settled outcome 时间窗口关联
  → 胜率 > 65% 且样本 ≥ 20 条 → 候选确定性规则
  → 人工确认 → 加入 scanners.py 对应族的权重表
```

### 风险与约束
- 每个 pattern 至少需要 20 个 settled 样本（防过拟合）
- 须做 OOS 验证（in-sample 期 vs hold-out 期对比）
- 修改 `scanners.py` 会改变实际交易逻辑 → **必须人工审核确认**
- 不引入 ML 框架，保持纯规则/权重形式

---

## 总体时间线

```
2026-06-17        2026-07-01        2026-08-17
     |                 |                 |
  Tier 1            Tier 2            Tier 3
  (已完成)          (LLM 归纳)       (确定性化)
  guard_cand        research_note    scanner rules
  自动生成          每日周期生成      节省 token
  无 LLM            需 LLM           需充足数据

  ↑ 2 周积累 →      ↑ 6 周积累 →
  ≥50 settled       ≥20 per pattern
```

---

## 人工确认工作流（Tier 1 + 2 产出的 guard_candidate）

```
查看候选规则 → 审阅文件内容 → 确认/归档
```

**查看命令**（在任意有 uv 的目录执行）：
```powershell
uv run python -c "
from aletheia.storage.repositories import list_knowledge_items
items = list_knowledge_items(item_type='guard_candidate', limit=10)
for i in items: print(i['id'], i['created_at'], i['title'])
"
```

**确认升级**：打开 `data/knowledge/guard_candidates/<title>.md`，确认有效后：
```python
from aletheia.knowledge.manager import create_trading_guard
# 内容复制自 guard_candidate 文件，去掉 auto_generated 标注
create_trading_guard(title="...", content="...", confidence="high_confidence")
```

**归档无效条目**：
```python
from aletheia.knowledge.manager import archive_item
archive_item("<item_id>")
```

---

## 关联文件

| 文件 | 说明 |
|------|------|
| `aletheia/universe/outcome_recorder.py` | Tier 1 实现主体 |
| `aletheia/storage/repositories.py` | `guard_candidate_exists_recent()` |
| `tests/test_self_evolution_tier1.py` | 10 个单元测试 |
| `docs/status/ROADMAP.md` | Self-Evolution 横切轨道条目（本计划的调度入口） |
| `aletheia/universe/scanners.py` | Tier 3 目标文件（确定性规则权重） |
| `aletheia/knowledge/manager.py` | guard_candidate / research_note 写入 |
| `data/knowledge/guard_candidates/` | Tier 1/2 自动生成的候选规则文件（运行时，gitignore） |
