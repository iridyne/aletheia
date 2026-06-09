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

## 待诊断（独立的新发现，不属本任务）

1. **`database is locked`** — errors.log 显示 market_observer/paper_trader/exchange_sync 等 job 因 DB 锁失败，市场数据停更（疑似遗留 python 进程或测试并发抢锁）。这可能是"重启后 agent 不触发"的真因。
2. **`gateway restart` 实际走了 stop** — 早先一次 restart 的 audit 是 `gateway_stopped killed=True`，网关没起来。restart 路径在本环境可能有 bug。

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
