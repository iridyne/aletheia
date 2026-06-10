# Current Task

> **本文件只写意图(计划 / 待办 / 阶段目标),不写运行态事实。**
> Gateway 是否在跑、凭证是否就位、观察期跑了多久、demo 单是否落交易所——这些是
> **每台机器本地的、随时间变化的事实**,写进 git 必然在另一台机器上过期。
> **要知道真实状态,运行 `aletheia doctor`**(只读、零副作用,是事实的唯一来源)。
> 双机切换标准流程见 `docs/DEV_PROTOCOL.md`。

---

## 当前阶段:Phase 0 — 跑通并稳定一个 72h 无人值守 paper 观察窗口

**目标(机器无关,长期有效)**:让闭环在 `bitget_demo`(真实模拟盘)形态下连续无人值守
运行 72h,证明"闭环在运转"(不是盈利)。

**Phase 0 观察门(72h 后逐条对照;判据稳定,事实用 `doctor` 取):**
- [ ] 网关 72h+ 无崩(health 非 dead)且窗口内无 `code_drift` 自愈重启
      (有 drift 重启 = 期间有人 commit 了 → 计时作废)
- [ ] market_observer 每 ~15min 产 opportunity(>70% 非 error)
- [ ] paper_trader 累计 **fill ≥ 5**(计 fill 不计 position 行——同标的同向合并只是展示口径)
- [ ] position_manager 累计平仓 ≥ 2
- [ ] post_trade_reviewer + risk_critic 有产出(knowledge_items 持续增长)
- [ ] demo 真单在 Bitget 侧可见(`get_open_orders`/`get_positions` 能查到)

> **判据性质**:衡量"闭环在运转",**不衡量盈利**(盈利是 Phase 1+ 的独立问题)。
> **观察项(非判据)**:持仓集中在单一标的是 Phase 0 正常现象,多标的分散是 Phase 2A。

**铁律**:观察窗口期间不 commit(任何 commit 移动 HEAD → 触发 code_drift 自愈重启 →
计时作废)。唯一例外是"观察形态本身升级"(如 paper_internal → bitget_demo),
那是主动的更高保真度替换,不是噪音污染——升级落地后新窗口内仍守此律。

## 待办(下一步可做的事 — 不含"现在状态如何")

1. **战略冻结生效**:Phase 0 观察门全绿之前,roadmap 冻结,只做稳定性 +
   `doctor` 这类"减人工"的确定性工具。地基不稳不盖楼。
2. **Windows 无人值守加固**(Task 3):autostart 看门狗 schtasks 注册可靠性、
   gateway 重启/注销/整机重启后的自动复活。这是 72h 真正无人值守的前提。
3. **经验闭环质量门**(已在工作区,待提交):post_trade_reviewer / risk_critic 仅在
   LLM 真产出 + 有真实活动时升 `high_confidence`,防噪音进检索。

## 下一阶段(Phase 0 门全绿后,需显式授权才启动)

见 `docs/ROADMAP.md`。Phase 1 = 稳跑验证 + 学习质量诊断;Phase 2 = 经验闭环。
**不在 Phase 0 稳跑证明前启动任何 Phase 1+ 工作。**

---

## Pi Agent (Operator Layer Track B) — Stage 1 实施计划

> 在 worktree `worktree-dev` 开发;合并 master 等 Phase 0 门全绿 + 显式授权。
> 完整设计:ROADMAP.md § Operator Layer + OPERATOR_LAYER_DESIGN.md + Downloads/aletheia_pi_agent_shell_integration_design.md

### 目标(§23/§24 Definition of Done)

- 用户问 "what is the system doing?" → 基于实时 DB 数据的智能回答
- 用户问 "why did this proposal fail?" → 引用具体 risk_decision 的解释
- pause/resume 命令存在但被 PermissionPolicy 拦截(默认 Read Only)
- create_research_note/diagnostic_todo 同理(需 Research 权限)
- 每个查询/命令审计
- Shell 降级不影响 Trading Core
- 无 live/凭证/硬风控访问

### 架构决策

- **Fix-in-place**: 修复 `POST /api/command` 的坏 LLM 路径(错误 import `from aletheia.agents.llm`
  + 签名不对 → 异常被吞,100% 关键词回退)。前端 `CommandPalette.tsx` 零改动。
- **2-call LLM pattern**: call-1 结构化意图分类(~200tok) → 执行命令 → call-2 合成回答(~600tok)。
  LLM 不可用 → 关键词+原始数据降级。
- **全 8 模块完整架构**(对照设计文档 §24):不砍 PermissionPolicy/SessionStore/Runtime。
  Stage 1 权限模式=ReadOnly → 非读命令被权限层拒绝(不是"代码不存在")。

### 新建 `aletheia/agent_shell/` (8 模块)

| 文件 | 对应设计模块 | 职责 |
|------|-------------|------|
| `context.py` | AppContextProvider | `build_app_context(sections)` — 按需汇总 7 个 context section(§12) |
| `runtime.py` | AgentShellRuntime | 单回合循环:context→意图→权限检查→命令→合成→审计。Stage 1=仅 request-response(无自主循环) |
| `registry.py` | CommandRegistry | 11 个命令(§24) + risk_level + 所需权限模式 |
| `permissions.py` | PermissionPolicy | 权限模式→允许命令集;Forbidden 永远硬拒(§10) |
| `sessions.py` | SessionStore | `command_session` 落 SQLite;multi-turn=Stage 2(本期 session 内只一问一答) |
| `audit.py` | CommandAudit | best-effort 落 `agent_shell_commands` 表 + JSONL |
| `intent.py` | (Runtime 子组件) | `classify_intent` — llm_structured,fallback=关键词映射 |
| `synthesis.py` | (Runtime 子组件) | `synthesize_answer` — llm_complete,fallback=格式化 JSON |
| `commands.py` | (Registry 子组件) | 11 个 handler 实现(读命令直接返回;写/操作命令走 permission gate) |

### 修改现有文件

- `aletheia/server/routes/command.py` — 坏 try 块替换为调用 `AgentShellRuntime.handle()`
- `aletheia/storage/migrations.py` — 新增 3 张表(sessions/messages/commands)

### 11 个命令(§24)

```
Read:        get_system_status, get_agent_activity, get_market_snapshot, get_positions,
             explain_agent_run, explain_proposal
Operational: pause_agents, resume_agents                        (需 Paper Auto)
Low-write:   create_research_note, create_diagnostic_todo,
             create_improvement_proposal                        (需 Research)
```

### 复用(不重建)

- `aletheia/agents/helpers.py:llm_complete/llm_structured`
- `aletheia/storage/repositories.py` 全部读函数
- `aletheia/agent_runtime/health.py:compute_health()`
- `aletheia/config/loader.py:load_config()`
- `aletheia/knowledge/manager.py:create_research_note()`

### 验证

1. `uv run pytest -q` → 515 全绿 + 新增 ~15 agent_shell tests
2. 手动 E2E:gateway 启动 → Ctrl+K → 自然语言问答 → 审计表有记录
3. 安全: pause_agents 在 ReadOnly 模式被拒;create_research_note 在 ReadOnly 被拒

---

## 历史交接

详细的逐次变更记录见 `docs/AI_HANDOFF.md` 与 `docs/DEVLOG.md`。本文件只保留
**当前阶段意图**;已完成的诊断/修复叙事不在此堆积。
