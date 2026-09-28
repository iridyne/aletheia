# 18c · Audit Brief · 段 6（analytics + economics）top-5 屎点扫描（表 C1）

> **已由新分析接替（2026-09-28）**：见 [Core analytics/economics 分析与修复方案](core_analytics_economics_review_plan.md)。已完成结构扫描与重点调用链核验；不再外派或强凑每维五项，也不把该进度等同于逐行全审。下文保留为历史 brief。

> 分类：**audit-only，不写代码，不建 issue**
> 建议 Agent 档次：**codex+5.6-luna**（32k 行需要深度扫描）
> 预算：≤3 小时
> 产出位置：`docs/research/audits/SEGMENT6_TOP5_SCAN_2026-09-28.md`

## 背景

`project_minimum_profit_loop_shit_map_2026-09-26.md` 记录：段 6 是 7 段主线里
**最脏**的一段——32.2k 行 / 306 try/except（其他段的 1.5-3 倍）。至今没有任何
umbrella issue，一头扎进去会淹死。

**必须先审计后拆卡**，避免"外部 Agent 领了 32k 行不知道从哪起"。

## 目录范围

- `core/aletheia/analytics/`
- `core/aletheia/economics/`

**不扫**（已由其他任务覆盖）：
- `_utcnow` / `_canonical_json` / `_table_exists` → 已由 [#3523](https://github.com/iridyne/aletheia/issues/3523) 处理
- `/api/universe/planning-history` blob 投影 → 已由 [#3642](https://github.com/iridyne/aletheia/issues/3642) 处理
- `candidate_planning_generations` retention → 已由 [#3643](https://github.com/iridyne/aletheia/issues/3643) 处理

## 五维扫描

对以下每一维，找出段 6 的 **top-5**（严格 5 个，按屎值排序）：

### 维度 1 · 双主线
同一意图在段 6 内有多份实现（非 #3523 已覆盖的工具函数）。
- 判定标准：函数签名类似 + 意图相同 + 实现体不同
- 示例（非穷举）：`_round` ×5、`_finite` ×5（已知信号，可作起点）

### 维度 2 · 过度防御触及经济语义
`except Exception: return <中性值>` 且函数在**账本/经济归因/成本计算**权威路径上。
- 判定标准：函数消费方是 PnL/attribution/outcome/cost/economics 字段
- 反例（非屎，不列）：面板/日志辅助路径的宽异常

### 维度 3 · 死代码 / 死分支
- 定义死代码：函数或分支 reachable=False（`if False:` / `# TODO 下线` / 无 caller）
- 判定工具：`grep -rn` + 阅读上下文；单参照 codegraph blast radius = 0

### 维度 4 · 契约漂移
docstring 或 `docs/design/contracts/` 说 X，代码实际 Y。
- 优先扫：`outcome_economics.py`、`filter_evidence.py`、`sizing_trace.py`、
  `causal_controls.py`、`alpha_attribution.py`

### 维度 5 · 巨型函数 > 300 行
- 用 AST 脚本或 `awk` 统计函数体行数
- 只列 >300 行且**未在** `ARCHITECTURE.md#module-seams` 已 audit 的

## 产出结构

```markdown
# Segment 6 · analytics + economics · Top-5 Scan · 2026-09-28

## 汇总（每维 top-5）

### 维度 1 · 双主线
| # | 组 | 出现位置 | 建议 |
|---|---|---|---|
| 1 | `_round` × 5 | file:line × 5 | 建议 [#3523](https://github.com/iridyne/aletheia/issues/3523) 扩范围 / 单独立卡 |
| ... |

### 维度 2 · 过度防御
| # | 位点 | 语义影响 | 建议 |
|---|---|---|---|

### 维度 3-5
（同结构）

## 最脏 3 个位点的 prompt 骨架

选出全 5 维交叉后**最有害**的 3 个位点，各给一段 prompt：

### 位点 A · <file:line>
- 屎值来源：<跨了哪几维>
- 影响：<一句话>
- 修复建议 D 级：D0 / D1 / D2
- prompt 骨架（给未来的实施 Agent）：<3-5 行>

### 位点 B / C
（同）

## 建议 issue 立卡清单

| 卡 | title | labels | 依赖 | D 级 |
|---|---|---|---|---|
| 1 | ... | ... | ... | D0 |
| ... |

## 不建议立卡的（也记录，避免下次重扫）

- <一些看起来像屎但读进上下文后是合理设计的>
```

## 硬约束

- ❌ 不改任何代码
- ❌ 不 `gh issue create`
- ❌ 不复述已在 #3523/#3642/#3643 覆盖的信号
- ✅ 每一维必须给**恰好 5 个**，宁缺毋滥（"只有 3 个"就写 3 个并说明原因）
- ✅ 每个位点必须带 file:line
- ✅ 最脏 3 个位点必须给 D 级（D0 语义不变 / D1 契约变 / D2 架构变）

## 参考

- 记忆：[[project-minimum-profit-loop-shit-map-2026-09-26]]、[[project-shit-cleanup-batch1-2026-09-26]]
- 契约：`docs/design/contracts/`（尤其 outcome / economics 相关）
- batch1 已合入 PR 参考：#3512-#3522（改动模式可复用）
