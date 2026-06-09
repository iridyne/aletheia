# Current Task

## Status: 自愈层已上线并端到端验证（2026-06-09）— 僵尸态可自动消除

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
