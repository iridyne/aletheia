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

## Doctor / 运营告警修复流程（2026-07-03 事故教训）

`aletheia doctor` 告警不能直接等同于"当前代码坏了"。本项目大量探针是滚动窗口，
会把历史残留、已缓解风险和当前活跃故障混在同一份报告里。AI 如果不先分类，会陷入
"查日志 → 改一点 → 再查 → 再改"的长循环，浪费时间并扩大改动面。

### 强制顺序

1. **先建立基线**：记录 `doctor` warning 数、每条 detail、最新事件时间、涉及表/文件。
2. **先分类再修**：
   - 当前活跃故障：最新事件仍在发生，或重启/新写入后继续复现。
   - 历史残留：滚动窗口仍显示，但最新事件已经超过合理冷却期。
   - 已缓解风险：异常事实仍存在，但当前代码已有明确拦截/降级。
   - 观测口径问题：探针 severity 没区分上述状态。
3. **每条告警必须有证据源**：SQL 查询、日志行、函数路径、测试或重启后行为之一。
4. **先修真实写入/决策路径，再修 doctor 展示语义**。不要只把 warn 降成 info。
5. **每个修复都要有量化复核**：warning 数变化、最新事件年龄、重复次数、错误数、测试数。
6. **最后再判断完成**：只有 `doctor` 复跑、相关测试通过、工作区干净，才算收口。

### 反例：本次为什么耗时过久

2026-07-03 的 doctor warning 修复中，初始状态是 4 个 warning：
`loop.reconciled_ratio`、`loop.concentration`、`content.proposal_bias`、
`trust.audit_chain`。耗时过久的原因：

- 没有第一时间把 warning 分为"当前故障"和"历史窗口残留"。
- 对 audit chain 先看了历史断点，再定位跨进程 hash 缓存，顺序偏慢。
- 对 proposal bias 先看 doctor 表象，再追到机会池/候选/提案三层输入，路径较长。
- 没有一开始就写出"完成标准 = warning 数、最新事件年龄、拦截是否生效、测试覆盖"。
- 在解释和实现之间来回切换，导致 owner 感知为长时间无交付。

正确收敛方式应是：

```text
doctor baseline
→ 对每条 warning 查最新事件时间
→ 活跃故障改代码，历史残留改探针语义
→ focused tests
→ restart gateway
→ doctor final
→ commit
```

### 本次沉淀出的硬规则

- 审计链类告警：不能修改历史审计文件来"修绿"；先修未来写入路径，再让 doctor 明确显示
  历史断点与当前断点的分界。
- 滚动窗口类告警：必须显示最新事件年龄。超过冷却期且当前精确探针为 0 时，不应继续
  报当前 warning。
- 策略方向偏置：不要只改 scanner 或 doctor 文案。至少检查三层：机会池方向分布、
  pending signal 队列、proposal 写入层。必要时在 proposal 写入前加质量刹车。
- `doctor` 修复必须保持只读探针原则。写操作只能在被测试覆盖的业务路径中发生。
- 汇报 owner 时先说结果数字：warning 从多少到多少、当前还有没有活跃故障、哪些只是历史信息。

### Gateway 掉线 / 重启排查规则

前端显示 `backend unreachable` 时，不能直接判断为"后端崩了"。本系统是
frontdoor `:8787` → backend `:8788` 的两层结构，至少要先区分三类：

- **主动重启**：`audit.jsonl` 里 `gateway_restart`，尤其 `reason=git_hook` / `manual`。
- **frontdoor 断流**：`gateway_frontdoor_restart` 或 `frontdoor-errors.log` 中代理读取中断。
- **真实 backend 异常**：`errors.log` 中 FastAPI 路由 traceback、启动失败或 supervisor give-up。

强制做法：

1. 先看 `doctor` 的 `process.restart_history`，确认 24h 内重启来源和次数。
2. 若是 git hook 高频触发，先判断是否 docs/tests-only 变更；这类变更不应重启 gateway。
3. 若是 frontdoor 断流，优先检查代理降级是否返回结构化 503，避免长 traceback 误导 owner。
4. 若是 backend traceback，必须定位到具体路由/函数并补回归测试。
5. 汇报时用"哪一层断了"说人话：前门断、后端路由报错、还是开发流程主动重启。

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
