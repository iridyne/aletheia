# Spec-Driven Development (SDD) 方法论

> 来源: [specdriven.ai](https://specdriven.ai/) · 内化于 2026-06-23
> 角色定位: 本文是 L2 层方法论参考，指导"如何使用契约文档"，不替代契约本身。

---

## 核心命题

AI 让写代码变廉价后，工程瓶颈从"写语法"转移到"定义意图"。
SDD 通过**前置精确 spec** 消除 ambiguity tax（模糊性导致的幻觉+返工循环）。

开发者角色: **Coder → Architect + Reviewer**

---

## 六阶段工作流（已适配本项目）

```
Phase           产出物                     本项目对应                    检查点
─────────────────────────────────────────────────────────────────────────────
1. Constitution 不变的项目宪法             CLAUDE.md + AGENTS.md         团队对齐
2. Specify      需求/验收标准              docs/design/*_contract.md     产品审核
3. Clarify      消歧记录(边缘/错误/空态)   docs/design/specs/ 审计产物   开发者验证
4. Plan         技术蓝图                   docs/plans/*.md               架构审核
5. Tasks        原子任务分解(1-4h/PR)      docs/_ai/CURRENT_TASK.md      依赖+范围审核
6. Implement    AI 生成 + 人验证           代码 + tests/specs/ claims    双重验证后 commit
```

**关键规则**: 实现阶段发现需要改动 → 先改 spec → 再改代码。永远不反过来。

---

## 11 条原则（精简映射）

| 原则 | 一句话 | 本项目落地 |
|------|--------|-----------|
| Spec as Truth | spec 是活契约+可执行蓝图 | `docs/design/` + `tests/specs/` |
| Upfront Clarity | 业务规则/边缘/非功能需求前置 | risk_engine_contract 16关 |
| Persistent Context | spec 跨会话持久 | AI_HANDOFF + DECISIONS + CLAUDE.md |
| Test-Driven Guardrails | 从 spec 派生测试 | `@spec_claim` → pytest |
| System Integration | 集成点写进 spec | agent_contracts + data_layer |
| Living Specs | spec 随反馈迭代 | "文档为准代码追文档" |
| Structured Organization | constitution 保证一致性 | CLAUDE.md 全局约定 |
| Systematic Decomposition | 大功能拆原子 | tasks.md / CURRENT_TASK |
| Human Orchestration | 人=编排+审核 | checkpoint 制度 |
| Explicitness | 高具体性驱动安全 | FORBIDDEN_INTENTS, RESTRICTED_TOOLS |
| Executable Intent | spec 可被 AI 直接执行 | SpecClaim decorator → conformance matrix |

---

## SDD vs Vibe Coding

| | Vibe Coding | SDD |
|--|------------|-----|
| 过程 | 即兴 prompt，快速迭代 | 分阶段，先 spec 后 code |
| AI 角色 | 主导生成，松散引导 | 执行定义好的任务，人编排 |
| 适用 | 原型/探索/小 hack | 信任关键系统/复杂项目 |
| 风险 | 幻觉/上下文丢失/脆弱 | 前置投入大 |

**本项目立场**: 信任边界（risk/execution/live）必须 SDD；周边工具代码可适度 vibe coding。

---

## 工具机制（如何让 spec 可执行）

### 初始化
扫描仓库现有接口/模式 → 参照 constitution → 生成 claims 和验收标准

### 执行
spec 产出代码/测试/文档 → spec 变更触发重新验证 → 自动一致性检查

### 本项目的技术实现

```
docs/design/*_contract.md     ← 人类可读 spec（source of truth）
        ↓ 提取 claims
tests/specs/spec_*.py         ← 可执行 spec（@spec_claim decorator）
        ↓ pytest 运行
Conformance Matrix            ← claim_id → test → pass/fail 追踪
        ↓ 失败 = drift
BACKEND_ISSUES.md             ← 记录偏差，代码追文档修复
```

---

## Clarify 阶段检查清单（新增）

在 spec 写完、code 开始前，跑一遍：

- [ ] 每个操作的**错误路径**都有定义？（失败返回什么？谁 retry？）
- [ ] **空状态**处理？（表为空/无数据/首次运行）
- [ ] **边界值**？（max 是含还是不含？零值？负值？）
- [ ] **并发**？（两个线程同时做这个操作会怎样？）
- [ ] **幂等性**？（同一请求执行两次，结果一样吗？）
- [ ] **安全**？（谁有权限？权限不足时返回什么？）
- [ ] **可观测性**？（如何知道它在工作/坏了？）

---

## 每阶段 Checkpoint 制度

| Phase | Checkpoint 形式 | 通过标准 |
|-------|----------------|---------|
| Constitution | CLAUDE.md PR review | 无矛盾/无过时条目 |
| Specify | 契约文档 diff review | 所有断言可验证 |
| Clarify | 检查清单全勾 | 无"TODO"/"TBD" |
| Plan | DECISIONS.md 追加 | 替代方案已评估 |
| Tasks | CURRENT_TASK.md | 每条 1-4h、有验收标准 |
| Implement | `uv run pytest tests/specs/ -q` 全绿 | + conformance matrix 无新 gap |
