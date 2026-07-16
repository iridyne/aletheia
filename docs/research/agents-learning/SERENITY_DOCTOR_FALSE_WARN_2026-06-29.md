# Serenity doctor 误报诊断备忘 — 2026-06-29

> 本会话查清的结论。环境工具(Bash/Read/Grep)在排查中途严重不稳定,
> 为避免在不可验证状态下改信任敏感系统附近代码,**未动代码**,只落结论待 owner 回来执行。

## 结论:`data.serenity_pipeline` 的 WARN 是误报,不是故障

### 证据(serenity_research_runs 运行表实测)
- 6-29 `00:15 UTC` scheduled run → **status=completed,成功**,产出新 opportunities。
- 6-28 `00:15:44 UTC` scheduled run → 同样 completed。
- **每天稳定成功跑一次,周期 ≈24h,没断。**
- doctor 报 "last run 14.4h ago"(14:41 UTC 时)与 00:15 UTC 上次运行完全吻合。

### 根因:探针阈值与 job 周期不匹配
- Serenity 研究是**每天只跑一次**的 job(见 `scheduler.py:350` `dreaming_daily` cron)。
- doctor 告警线 = ">12h 没跑就 WARN"。
- 一个 24h 周期的 job,每天必然有十几个小时处在 ">12h" 状态 → **每天误报半天**,与系统健康无关。
- 撞上 owner 定的「doctor 探针诚实模式」:warn 只该留真缺陷,周期性正常行为不该 warn。

## 待确认的小疑点(不阻塞修复)
- 运行表稳定在 **UTC 00:15** 触发,而 `dreaming_daily` cron 配的是 **Asia/Shanghai 02:00 (=18:00 UTC)**,差约 6h。
- 说明 serenity 的实际调度源可能不是 `dreaming_daily`,而是另一条触发线。
- 触发点共 4 处:`scheduler.py:730`(dreaming cron)、`research_worker.py`(worker 内)、`operator_loop.py:693`(operator)、`server/routes/data.py`(API 手动)。
- **周期 24h 已由运行表数据证实**,此疑点不影响修复决策,仅供后续厘清。

## 建议修法(二选一)
1. **简单**:`_check_serenity_pipeline` 阈值 12h → ~26h(给 24h 周期留余量),恢复 GREEN。
2. **更诚实**:探针感知该 job 实际预定周期 —— 未到下次预定时间报 info,真逾期才 warn。

修后:跑 `tests/specs/spec_serenity_*.py` 相关 spec + restart gateway 跑 doctor 确认 WARN 清零。
探针代码位置:`aletheia/diagnostics/doctor.py` `_check_serenity_pipeline`(约 line 268)。

## 本会话明确未做(留给 owner)
- 未推送的 34 个 commit + spot-parity 分支去留 —— 按记忆「需 owner 单独审核」,不擅自 push/merge。
- 另一个 WARN `loop.knowledge_flood`(PI guards 窗口 0/30)是学习闭环已知老问题,非本次范围。
