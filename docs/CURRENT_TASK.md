# Current Task

## Status: Phase 0 基础设施已就绪，观察门卡在人工凭证输入（2026-06-10）

> **本 checkout 从未运行过**（见 AI_HANDOFF 2026-06-10 顶部条目）。下方 06-09 起的所有"观察期运行中 / demo 真单"叙事是**原作者机器**状态，不是这里。本机经本会话修复后：`aletheia/data/` 包已重建（515 测试通过）、DNS resolver 已修、boot-prove 通过（15 job 构建、实时行情拉通、observer 无 LLM 优雅降级）。
>
> **要推进 Phase 0 观察门，必须先由人提供两样 AI 不能伪造的东西**：(1) LLM provider + API key；(2) Bitget demo API 凭证。然后 `permission_mode=Paper` → 启动 gateway → 跑 72h。详见 AI_HANDOFF 顶部 + memory `checkout-state-never-ran`。分支 `fix/restore-data-package` 待合并。

---

## (历史) Status: 切换到 Bitget demo 真实模拟盘，观察期重置（2026-06-09 cutover）

**这次 commit 为何打破"观察期内不 commit"铁律**：因为**观察形态本身变了**——paper 交易从「本地 `paper_internal` 撮合 + 现货 spot」整体切到「Bitget demo 真实模拟盘下单 + USDT 永续合约」。旧观察基线（现货 / 本地撮合）随之作废：不是被 drift 噪音污染而重算，而是被一次**主动的、更高保真度的形态升级**取代。所以此处 commit 是正当的——它让 HEAD 前进 → 网关重启时启用 demo 代码 → 观察期在新形态下重新计时。**升级落地后，新观察窗口内仍守"不 commit"铁律。**

**为什么切 demo**（架构原则，用户确立）：paper trading 应优先用交易所/券商自己的 demo/模拟盘接口（真实 API、交易所侧有记录），只有平台不提供时才退回本地模拟撮合。Bitget 提供 demo（`paptrading:1` header，仅 USDT 永续），所以 paper 默认走 `bitget_demo` 真实下单；`paper_internal` 保留为 demo 不可用时的秒切 fallback。详见 `docs/DECISIONS.md` 的 demo wiring 三节。

**已验证**（2026-06-09，用户授权手动一笔真单）：真实 demo 开仓单 `1448153278595629057`（@62846.5，价取交易所确认 priceAvg）+ hedge-mode 平仓单 `1448153285251989505`（@62841.7），交易所走平、本地账本一致。三个被 mock 掩盖的潜伏 bug 同时修掉（mix body 缺 marginCoin/tradeSide；canonical→Bitget symbol 未转换；记的是请求价而非成交价）。

**无人值守保障已全部就位**（沿用上一轮）：autostart 看门狗 enabled（登录自起 + PT5M 复活死掉的 supervisor）；self_heal_enabled（drift/stale 自动重启、端口清理）；scheduler 运行；permission_mode=Paper（live 禁用）；kill_switch 今晨被日损自动触发，本次 cutover 第 2 步已清。

**起点基线 @ cutover**：旧 spot/local 基线作废。网关在 HEAD（本次 commit）+ demo-futures 形态下重启后，以重启后首个 health 周期的计数为新起点（opportunities/positions/orders/fills 重新计）。

**观察门（72h 后对照，全部需通过）**——形态为 demo-futures，判据沿用 fill 口径：
- [ ] 网关 72h+ 无崩（health=healthy/degraded，非 dead）**且窗口内无 `code_drift` 自愈重启**（有则说明有人 commit 了，计时作废）
- [ ] market_observer 每 15min 产 opportunity（>70% 非 error）
- [ ] paper_trader 累计 **fill ≥ 5**（按项目约定：每个 15min 周期开仓/加仓 = 一次开仓动作；`positions` 表把同标的同向合并成 1 行只是展示口径，所以**计 fill 不计 position 行**）
- [ ] position_manager 累计平仓 ≥ 2（demo 平仓走真实 hedge-mode 反向单，交易所须同步走平）
- [ ] post_trade_reviewer + risk_critic 有产出（knowledge_items 持续增长）
- [ ] **demo 真单在 Bitget 侧可见**：`get_open_orders`/`get_positions` 能查到 paper_trader 下的真实 demo 单，exchange_sync 反映 demo 权益/仓位

> **观察项（非判据）**：持仓集中在单一标的（paper_trader 每周期挑最优 opportunity，对同标的同向加仓）。**Phase 0 视为正常**——门只证"闭环在跑"。多标的分散是 Phase 2A 的事，不作稳定性门。
> **判据性质**：以上衡量"闭环在运转"，**不衡量盈利**——盈利是 Phase 1+ 的独立问题（见 ROADMAP "False confidence" 失败模式）。

---

## 已完成：网关僵尸态修复 + 确定性自愈层 + DB 锁根因

## 2026-06-09 网关僵尸态修复 + 确定性自愈层

**审计发现**：声称"72h 观察期进行中"是假象。网关进程活着，但跑旧码 `1aa1feb`（落后 HEAD 7 commit），`/api/health` 已 degraded（`code_updated_pending_restart` + `market_data_stale:1114min`）11 小时无人理会；`paper_trader` 11h 未触发；294 opportunity → 0 trade。根因：`run_health_check()` 每 10min 算出信号、发告警，却不采取任何自愈动作。

**已交付**（commit `8a6e008`，501 测试通过）：
- `pidfile.supervisor_owns_pid()` — 线程上下文判定有无 supervisor 兜底
- `HealthConfig` 加 6 个 `self_heal_*` 字段（默认开；限频 30min/2hr 严于 supervisor 5/600）
- `health.run_health_check()` 加确定性自愈分支：code drift / stale → 守卫+限频重启；孤儿端口 → 清理（排除 self+supervisor）；LLM/kill_switch → 仅告警
- 12 个回归测试

**端到端验证通过**：制造空 commit → drift 被检测 → 自愈自动重启 → supervisor 重生新码 → drift 清除（audit `self_heal_restart: code_drift`）。今天那类僵尸态以后会在一个 health 周期（≤10min）内自动消除。

**端到端当场抓到并修复一个严重 bug（commit `abd7773`）**：限频计数器原是内存态，但 `os._exit(42)` 重启会清空它 → cooldown 永不触发 → 持久 issue 致每 10min 无限重启。改为从持久化 audit.jsonl 读 restart 计数。502 测试通过，已验证跨重启有效。

## DB 锁真根因 — 已修复（commit `a33dc86`）

**`database is locked`(累计 1652 次、管道实际停摆的真根因)已定位并修复**：`storage/repositories.py` 的 `_insert`/`_update`/`_delete_old_rows` 提交条件写错——Python sqlite3 写后必在事务中，旧条件 `not db.in_transaction` 恒 False → commit 从不执行 → 写事务悬挂持 WAL 锁。改为 `if not was_in_tx: commit()`。504 测试通过，含真实 file DB + 独立连接的锁释放回归测试。端到端验证市场数据恢复中。

## restart "bug" — 已澄清为误判（2026-06-09）

先前怀疑 `gateway restart` 杀链不重生。诊断后**证实是误判**：
- audit 显示会话开始那次是 `gateway_stopped (cli)`，根本没有 `gateway_restart` 事件——是 CLI `gateway restart`（= do_stop + do_start）里 do_start 静默失败，**因为当时网关跑旧代码 `1aa1feb` + 我同时跑测试抢 DB 锁**，叠加造成启动失败假象。
- 三个真修复到位后实测：CLI `gateway restart` 干净成功（`03:21:28` stop → 新 supervisor 23084 重生 → health=healthy → boot 对齐 HEAD → drift 自动清除 → 市场数据持续流动）。
- restart 命令本身无 bug。

## 系统现状（2026-06-09 收尾）

健康、自管理、数据实时。三个根因（僵尸态/自愈限频/DB锁）全部根除并端到端验证。具备开始 Phase 0 72h 观察期的条件。

---

## 旧产出（Roadmap v1，仍有效）

## 产出

- `docs/ROADMAP.md` — 12 周按周迈步开发路线图（含观测门 + 红线 + 失败模式防线）
- `docs/PIPELINE_STATUS.md` — 现状逐环节分析
- Deep research 完成：102 agent, 20 sources, 19 verified claims

## Roadmap 摘要

```
Week 1-2   ▸ 阶段 0.5：补全基础（scheduler 补全 + 开始稳跑）
Week 3-4   ▸ 阶段 1：稳跑验证 + 学习质量诊断
  ──── [观测门] 72h+ 无崩，Learning Ledger 非零 ────
Week 5-6   ▸ 阶段 2A：结构化信号 + Agent 协作流
Week 7-8   ▸ 阶段 2B：经验闭环（记忆→决策）
  ──── [观测门] activation_ratio > 30%, review coverage > 50% ────
Week 9-10  ▸ 阶段 3A：自我反思 + 策略修订（walk-forward 验证）
Week 11-12 ▸ 阶段 3B：对抗验证 + 策略退休自动化
  ──── [观测门] 策略正期望 + 修订后 OOS 改善 ────
Week 13+   ▸ 阶段 4：高级进化（远期，需另行授权）
```

## Next Action

等待用户确认 Roadmap，然后开始 Week 1 Day 1：
1. `post_trade_reviewer` 排入 scheduler
2. `risk_critic` 排入 scheduler
3. 验证测试基线
