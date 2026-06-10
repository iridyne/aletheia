# Current Task

> **本文件只写意图(计划 / 待办 / 阶段目标),不写运行态事实。**
> Gateway 是否在跑、凭证是否就位、观察期跑了多久、demo 单是否落交易所——这些是
> **每台机器本地的、随时间变化的事实**,写进 git 必然在另一台机器上过期。
> **要知道真实状态,运行 `aletheia doctor`**(只读、零副作用,是事实的唯一来源)。
> 双机切换标准流程见 `docs/DEV_PROTOCOL.md`。
> **2026-06-10 当前协作分工**:权威的 Phase 0 72h 观察窗口在另一台观测机上运行;本 checkout
> 的职责是沿 `worktree-dev` 推进后续开发与文档收敛,尤其是 Operator Layer / Pi Agent 的
> 方案与实现准备。**不要用本 checkout 的进程状态推断观察窗是否达标。**

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

1. **观测主线继续**:Phase 0 观察门仍以观测机为准;凡涉及"72h 跑到哪了 / 是否达标"的判断,
   一律去那台机器用 `doctor`/gateway status 取事实。
2. **并行开发线 = `worktree-dev`**:可继续推进 post-Phase-0 能力的设计/原型,但不把这些开发
   行为当作 Phase 0 达标证据,也不提前合并回 `master`。
3. **Windows 无人值守加固**(观测机 Task 3):autostart 看门狗 schtasks 注册可靠性、
   gateway 重启/注销/整机重启后的自动复活。这是 72h 真正无人值守的前提。
4. **经验闭环质量门**(已在工作区,待提交):post_trade_reviewer / risk_critic 仅在
   LLM 真产出 + 有真实活动时升 `high_confidence`,防噪音进检索。

## 下一阶段(Phase 0 门全绿后,需显式授权才启动)

见 `docs/ROADMAP.md`。Phase 1 = 稳跑验证 + 学习质量诊断;Phase 2 = 经验闭环。
**不在 Phase 0 稳跑证明前启动任何 Phase 1+ 工作。**

---

## Pi Agent (Operator Layer Track B) — 当前设计源

> 在 worktree `worktree-dev` 开发;合并 `master` 仍等 Phase 0 门全绿 + 显式授权。
> **本地设计源已收口**:
> 1. `docs/ROADMAP.md` § Cross-Cutting Track: Operator Layer
> 2. `docs/OPERATOR_LAYER_DESIGN.md` (概念层)
> 3. `docs/superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md` (repo-specific 详细蓝图)
> 4. `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md` (参考文件覆盖矩阵)
> 5. `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-audit.md` (设计完成审计)

### 当前结论(已定)

- **最终形态是双进程**,不是把现有 `POST /api/command` 继续堆成 Python 单进程壳。
- **Python gateway 继续做交易真相层与命令执行权威**;新增 Node/TypeScript `Operator Shell`
  负责 PI 式常驻 session runtime、operator loop、任务/修复/推荐编排。
- **`Ctrl+K` 只保留为 launcher**,主表面是独立的 `Command Center` 页面。
- **权限拆两层**:现有 worker/trading 权限继续保留;新增独立 `operator.permission_mode`
  (`ReadOnly/Research/PaperAuto/AllowEditing/Auto`)。
- **现有 `aletheia/agent_shell` 不推倒重来**,而是重构定位为 Python 侧 operator substrate /
  compatibility layer。

### 当前设计阶段待完成动作

1. 以本地 dual-process spec 为准审阅设计,不再按更早的 Stage 1 bootstrap 草案推进。
2. 设计确认后,进入 implementation plan,把 Track 0-9 拆成逐文件、逐测试、逐提交任务。
3. 计划批准前,不写实现代码。

---

## 历史交接

详细的逐次变更记录见 `docs/AI_HANDOFF.md` 与 `docs/DEVLOG.md`。本文件只保留
**当前阶段意图**;已完成的诊断/修复叙事不在此堆积。
