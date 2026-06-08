# Current Task

## Status: READY — Gateway 稳定性修复完成，Roadmap v1 待确认后进入 Week 1 执行

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
