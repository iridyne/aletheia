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

## 历史交接

详细的逐次变更记录见 `docs/AI_HANDOFF.md` 与 `docs/DEVLOG.md`。本文件只保留
**当前阶段意图**;已完成的诊断/修复叙事不在此堆积。
