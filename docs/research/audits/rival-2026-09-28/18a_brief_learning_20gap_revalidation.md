# 18a · Audit Brief · Learning 20-gap 存活性复核（表 B4）

> **待核历史 brief（2026-09-28）**：尚无本项完成报告；不再外派，不按下文 Agent 档次或 Wave 自动执行。后续由主 Agent 核对当前源码与契约，产出问题解决方案。当前状态见 [分析状态](analysis_status.md)。

> 分类：**audit-only，不写代码，不建 issue**
> 建议 Agent 档次：**codex+5.6-luna**（深度 grep 调查）
> 预算：≤2 小时
> 产出位置：`docs/research/audits/LEARNING_20GAP_REVALIDATION_2026-09-28.md`

## 背景

`docs/archive/legacy/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md`
列了 Learning 模块 20 个 GAP（4 Critical / 9 High / 7 Medium），修复顺序
GAP-07 → 14 → 03 → 05 → 01。**已知变化**：

- GAP-06 已被自我 REFUTED（原报告 §5.2）
- `persona_rollout runtime_version` 类问题已由 [#3551](https://github.com/iridyne/aletheia/issues/3551) 承接
- 2026-07-19 → 2026-09-28 中间**两个月的批量 refactor**（含 DESHITIFY_BATCH_2026-09-27 的 51 commit）可能已顺带修掉部分

**不核查就立卡的风险**：撞已修，浪费外部 Agent 的 token。

## 你要做的

对 **GAP-01 至 GAP-20**（除 GAP-06 已 REFUTED）**每一条**：

1. 定位原 audit 里给的证据锚点（文件:行 / API 端点 / DB 表）
2. 在 master@HEAD 复查：
   - 代码路径是否仍存在
   - 报告描述的行为是否仍成立（grep 关键字符串、必要时读函数体）
   - 是否已被 open/closed issue 覆盖（`gh issue list --search`）
3. 判定归属：
   - **STILL_HOLDS**：仍成立，未被 issue 覆盖 → 建议新建 issue
   - **PARTIALLY_HOLDS**：部分成立 → 需要收窄 issue 范围
   - **FIXED**：已被 commit/PR 修掉 → 给 commit SHA
   - **COVERED**：已在 open issue 里 → 给 issue 号
   - **REFUTED_ON_REREAD**：原报告结论现在看不成立 → 说明证据变了

## 产出结构（严格照做）

```markdown
# Learning 20-gap Revalidation · 2026-09-28

## 汇总

| GAP | 原严重度 | 现状 | 依据 |
|---|---|---|---|
| GAP-01 | Critical | STILL_HOLDS / FIXED / ... | file:line 或 commit SHA |
| ... | ... | ... | ... |

## 逐条细节

### GAP-01 · <原标题>
- 原描述：<原报告一句话>
- 原证据：<原报告的文件:行>
- master@HEAD 核查：<你实际看到的>
- 判定：<STILL_HOLDS/...>
- 若 STILL_HOLDS：建议 issue 骨架
  - title: `[learning][gap-01] <一句话>`
  - labels: `bug`, `area/learning`, `type/gap`, `severity/<原严重度>`
  - body 骨架（3-5 段）
- 若 FIXED / COVERED：给 SHA 或 issue 号

### GAP-02 ... GAP-20
（同上）

## 建议的 issue 立卡清单（只列 STILL_HOLDS 的）

| 顺 | GAP | 建议 title | labels | 依赖 |
|---|---|---|---|---|
| 1 | GAP-07 | ... | ... | 无 |
| ... | ... | ... | ... | ... |
```

## 不做的事

- ❌ 不写任何代码
- ❌ 不 `gh issue create` —— 只在报告里给 issue 骨架，等 owner 一次性拍板后开
- ❌ 不重跑数据库运行时查询（原报告里的 Q1-Q8 假设已 stale）—— 只做静态代码 + issue 库核查
- ❌ 不推测 GAP 之间的新依赖关系 —— 沿用原修复顺序 07→14→03→05→01

## 参考

- 原 audit：`docs/archive/legacy/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md`
- 前置 audit（G1-G4 量化）：`docs/archive/legacy/issues/audits/LEARNING_CONTRIBUTION_GAP_AUDIT_2026-07-19.md`
- 契约：`docs/design/contracts/learning_contract.md`
- 记忆：[[project-learning-sdd-vs-reality-2026-07-19]]、[[project-learning-contribution-gaps-2026-07-19]]
