# 18b · Audit Brief · Admission GAP-3/4/5 存活性复核（表 B5）

> 分类：**audit-only，不写代码，不建 issue**
> 建议 Agent 档次：**deepseek**（范围窄，2 个模块 3 个 GAP）
> 预算：≤1 小时
> 产出位置：`docs/research/audits/ADMISSION_GAP345_REVALIDATION_2026-09-28.md`

## 背景

`docs/archive/legacy/issues/audits/CANDIDATES_ADMISSION_GAP_AUDIT_2026-07-20.md`
定位 5 个 GAP。**已知处置**：

- GAP-1 admission defer-blind → **PR #155 已关闭**
- GAP-2 `valid_until` 未因 defer 延长 → **PR #155 已关闭**
- 契约文档补 admission slot invariant → **PR #155 已关闭**（`decision_lifecycle_contract.md §2.3b`）
- **GAP-3** batch/queue cap 对齐 → 已开 [#157](https://github.com/iridyne/aletheia/issues/157)，**未做**
- **GAP-4** defer 时未检测 `defer_until > valid_until` → **未立卡**
- **GAP-5** `signal_cooldown` / `operator_already_evaluated` 隐性作用域 → **未立卡**

## 你要做的

对 **GAP-3、GAP-4、GAP-5** 三条：

1. 在 master@HEAD 复查原报告的证据锚点（原报告 §3.3/§3.4/§3.5 有精确 file:line）
2. 判定：
   - **GAP-3**：#157 body 是否覆盖当前代码状态？还是需要重写？
   - **GAP-4**：defer 路径当前是否会检测 `defer_until > valid_until`？
   - **GAP-5**：`signal_cooldown` 与 `operator_already_evaluated` 的作用域现在如何？
3. 每条判定归属：**STILL_HOLDS / PARTIALLY_HOLDS / FIXED / COVERED / REFUTED_ON_REREAD**

## 产出结构

```markdown
# Admission GAP-3/4/5 Revalidation · 2026-09-28

## 汇总

| GAP | 原严重度 | 现状 | 已有 issue | 建议 |
|---|---|---|---|---|
| GAP-3 | MEDIUM | STILL_HOLDS / ... | #157 | update #157 body / close #157 / ... |
| GAP-4 | LOW | ... | 无 | 新建 issue 骨架 / 归档 |
| GAP-5 | LOW | ... | 无 | 新建 issue 骨架 / 归档 |

## 逐条

### GAP-3
- 原证据：<file:line from §3.3>
- master@HEAD 现状：<你看到的>
- #157 body 是否仍准确：<Y/N + 依据>
- 判定：<STILL_HOLDS/...>
- 建议动作：<update body / close / new issue>

### GAP-4
（同上）

### GAP-5
（同上）
```

## 不做的事

- ❌ 不写代码
- ❌ 不 `gh issue create` / `gh issue edit`（只给建议）
- ❌ 不动 PR #155 已关的部分

## 参考

- 原 audit：`docs/archive/legacy/issues/audits/CANDIDATES_ADMISSION_GAP_AUDIT_2026-07-20.md`
- 已有 issue：[#157](https://github.com/iridyne/aletheia/issues/157)
- 契约：`docs/design/contracts/decision_lifecycle_contract.md §2.3b`
- 记忆：[[project-candidates-admission-gap-2026-07-20]]
