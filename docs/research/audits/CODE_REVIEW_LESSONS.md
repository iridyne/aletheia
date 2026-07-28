# Code Review 经验教训 — 数据流审计方法论

> 来源：2026-06-19 最小赚钱循环审计（commit e4abbfa）
> 发现 15 个问题，其中 3 个 P0 核心断裂在系统中存活了 2-4 周未被之前的 code review 发现。

---

## 为什么这些 Bug 能长期存活

### 盲区 1：静默失败（Silent Failure）

**模式**：代码不崩溃、不报错，只是返回空/None/降级值。

**典型案例**：`paper_trader_ranker.py` 中 `trading_mode = 'paper'` 查询不存在的列，
被 `try/except` 吞掉 `OperationalError`，返回 `paper_summary = None`。系统继续运行，
只是 strategy evidence 层完全失效——但没有任何可观测信号告诉你出了问题。

**检查方法**：
- 对每个 SQL 查询，grep 目标表的 CREATE TABLE 或 model 定义，确认字段名存在
- 对每个 `try/except: pass`，问"如果这里真的失败了，有人能知道吗？"
- 检查返回 None/空的降级路径是否有 warning 级日志

---

### 盲区 2：跨模块边界断裂（Cross-Module Gap）

**模式**：A 层输出了结构化数据，B 层期望接收，但两者的 schema 从未对齐。

**典型案例**：LLM 输出 `key_observations: list[str]`，但 `Opportunity` model 无此字段，
数据被拼接成文本后结构永久丢失。两侧代码各自"正确"，但数据管道断了。

**检查方法**：
- 对每个生产者的输出字段，追踪它被谁消费（grep 字段名在 storage/route/agent 中的出现）
- 如果输出字段在消费侧零命中 → 数据黑洞
- 如果消费侧期望的字段在生产侧永远为 None → 信号链断裂

---

### 盲区 3：志向性代码（Aspirational Code）

**模式**：Model 定义了字段（表达意图），但对应的写入逻辑从未实现。单元测试用 mock 
数据填充这个字段，所以测试通过——但生产中这个字段永远是 None。

**典型案例**：`oi_change_1h_pct` 在 `OpenInterestSnapshot` model 里定义了，
但 `_parse_oi()` 从来不计算它。下游 scanner 的 `if oi_delta_1h is None: skip` 
分支永远触发。

**检查方法**：
- 对 model 中每个 `Optional` / `| None` 字段，grep 其赋值点
- 如果只有 `= None`（默认值）而无真实赋值 → 志向性字段
- 检查下游是否有 `if x is None: 降级` 逻辑——如果有且永远触发，这就是死信号

---

### 盲区 4：Exception Swallowing

**模式**：`except Exception: pass` 让代码永不崩溃，但也让所有反馈信号消失。

**检查方法**：
- grep `except.*:.*pass` 或 `except.*:\n\s+pass`
- 对每个命中：这个 exception 在生产中会发生吗？如果会，结果是什么？
- 关键位置（数据写入、API 调用、DB 查询）的异常应该至少有 warning 日志

---

### 盲区 5：缺乏端到端数据流测试

**模式**：每个函数单独测试通过，但 A→B→C→D 的全链路从未被一个测试覆盖。

**检查方法**：
- 画出核心循环的数据流图（历史示例见 `docs/archive/legacy/issues/archive/audits/MINIMUM_PROFIT_LOOP_AUDIT.md`）
- 对图中每条边，确认存在一个测试验证"上游的输出能被下游正确消费"
- 重点关注跨表/跨模块的 JOIN 路径

---

### 盲区 6：增量开发的语义漂移

**模式**：字段/接口被重构改名，但某些消费者仍在用旧名字。

**检查方法**：
- 任何重命名操作后，立刻 grep 旧名字确认零命中
- 特别注意 SQL 字符串中的字段名（IDE/type checker 无法检查）
- 特别注意跨语言边界（Python SQL 字符串、前端 API 字段名）

---

## 审查方法论：数据流追踪（Data Flow Tracing）

传统 code review 聚焦"这段代码是否正确"。数据流审查聚焦"数据是否完整地从起点流到终点"。

### 步骤

1. **画循环图**：标注每个阶段的输入/输出/存储位置
2. **逐边验证**：对图中每条边，确认 producer 的输出 schema = consumer 的输入 schema
3. **字段穿透**：对关键字段（如 `scanner_family`），从写入点追踪到所有读取点
4. **None 路径分析**：找出所有可能为 None 的字段，确认 None 时的降级逻辑是否合理
5. **计数验证**：比较 producer 的产出数量 vs consumer 的消费数量（应相等或有记录的丢弃）

### 触发时机

- 每完成一个新 feature 后（检查它是否真的接入了完整循环）
- 每次重构/重命名后（检查消费者是否同步更新）
- 定期（如每月一次）对核心循环做全量扫描

---

## 本次审计的具体发现索引

历史审计实例见 `docs/archive/legacy/issues/archive/audits/MINIMUM_PROFIT_LOOP_AUDIT.md`（15 个问题的当时列表与修复快照；不作为当前状态）。
