# Aletheia Packaged Runtime Power Optimization（macOS）

更新日期：2026-07-17

## 1. 结论与判断

你这个判断是对的：`Aletheia.app`（托盘）应当尽量扮演远程遥控与状态可见性，不应承载交易主循环。

当前代码确实是“后端正常跑 + 托盘仍有高频轮询”的状态，因此你看到 `significant power` 是可以解释的：能耗来自托盘状态探测、网关监督和 Dashboard 打开态的持续刷新叠加。

1. 托盘在 2 秒周期会做 `observed_state`，这本身就会持续唤醒链路。
2. 网关 supervisor 在 Python 内部以秒级端口探测维持可靠性。
3. Dashboard 打开时前端会进行状态栏轮询、SSE 空转和更新状态轮询。

对应证据：

1. 托盘周期轮询：[native/aletheia-host/src/bin/aletheia-tray.rs:343](../../native/aletheia-host/src/bin/aletheia-tray.rs#L343)、[native/aletheia-host/src/bin/aletheia-tray.rs:399](../../native/aletheia-host/src/bin/aletheia-tray.rs#L399)
2. 观察函数细节：[native/aletheia-host/src/bin/aletheia-tray.rs:67](../../native/aletheia-host/src/bin/aletheia-tray.rs#L67)
3. 网关监督 1 秒端口检查：[aletheia/_gateway_runner.py:38](../../aletheia/_gateway_runner.py#L38)、[aletheia/_gateway_runner.py:284](../../aletheia/_gateway_runner.py#L284)
4. 前端状态栏查询：[frontend/src/App.tsx:123](../../frontend/src/App.tsx#L123)
5. SSE 事件长连空转：[aletheia/server/routes/runtime.py:214](../../aletheia/server/routes/runtime.py#L214)、[aletheia/server/routes/operator.py:969](../../aletheia/server/routes/operator.py#L969)
6. C 层落地证据（本次新增）：[frontend/src/App.tsx:117](../../frontend/src/App.tsx#L117)、[aletheia/server/routes/runtime.py:213](../../aletheia/server/routes/runtime.py#L213)、[aletheia/server/routes/operator.py:960](../../aletheia/server/routes/operator.py#L960)

## 2. macOS 设计对标后可落地方向

1. Apple 能效最佳实践强调“无用户触发尽量不轮询”。这与“托盘尽量变成遥控器”方向一致。[Energy Efficiency Best Practices](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/BestPractices.html)
2. 固定短周期 timer 需要更多容忍度，否则更容易打断节能路径；建议把周期拉长、并在用户触发动作后再短期加速。[Energy Efficiency Best Practices](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/BestPractices.html)
3. App Nap 不能作为唯一手段，它是辅助，不是主策略。[App Nap](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/AppNap.html)
4. 观察层面应以 `Activity Monitor`（Energy / Energy Impact / App Nap / Preventing Sleep）为主。[Apple Support](https://support.apple.com/en-ie/guide/activity-monitor/-actmntr43697/mac)
5. 在你们体系里，托盘职责本身应与交易语义分离，[Packaged Runtime Operations](./PACKAGED_RUNTIME.md) 已明确“托盘负责启动/健康与控制，不直接做策略决策”。

## 3. 优化切入顺序（按你的目标优先）

### A 层（先做）: 托盘“遥控器化”

1. 将 `native/aletheia-host/src/bin/aletheia-tray.rs` 的 2 秒改为可配置参数（建议默认 15 秒）。
2. 保留“更新关键路径”短周期：当 `update::has_pending_request(&paths)` 为真时，允许加速轮询；正常态执行慢周期。
3. `observed_state` 里先做 `gateway_ready` 判定，再决定是否执行 `gateway_healthy`，减少无效的健康探测。

对应文件：

1. `native/aletheia-host/src/bin/aletheia-tray.rs:343`
2. `native/aletheia-host/src/bin/aletheia-tray.rs:399`
3. `native/aletheia-host/src/bin/aletheia-tray.rs:67-75`

预期变化：

1. 托盘 poll 次数下降约 86%（2s 到 15s）。
2. 不触发用户操作时，非交互唤醒显著下降。
3. 后端控制能力不降级，更新逻辑保留实时响应。

### B 层（次要）: 网关监控参数协同降频（不改交易主循环）

1. 已将 `health.exchange_probe_interval_sec` 默认值从 30 秒提高到 60 秒（可通过配置扩展到 120 秒）。
2. 在文档和配置中标注 power mode（normal / balanced / power_save）是可选模式。
3. 强调该项是连接监控，不是交易主频/下单节拍。

对应文件：

1. `aletheia/config/schema.py:416`
2. `aletheia/agent_runtime/scheduler.py:677`

预期变化：

1. 连接探测频率下降 30%~75%。
2. 核心交易和调度任务不受影响。

### C 层（中长期）: 前端显示层按“用户观察窗口”调速

1. `frontend/src/App.tsx` 将状态栏默认频率从 10 秒提升到 15 秒；重启等待窗口为 2 秒，风险查询为 30/3 秒自适应。
2. SSE 空转逻辑已改为“有变更快无变更慢（1→2→4→8 秒）”，减少固定 1 秒死循环。
3. 更新条保留 active path 快速刷，inactive path 降到 5 秒级。

对应文件：

1. `frontend/src/App.tsx:123`
2. `aletheia/server/routes/runtime.py:214`
3. `aletheia/server/routes/operator.py:969`
4. `frontend/src/components/PackagedUpdateBanner.tsx:121`

预期变化：

1. Dashboard 打开但“用户不操作”时 CPU 和 wakeup 压力下降。
2. 用户触发时仍有可观测响应，不影响控制手感。

## 4. 整体预期结果

1. A/B 层已落地后，先验目标是先拿到 **20%~35%** 的待机端收益；C 层到位后再争取额外 **8%~18%**。
2. 配合 App Nap 与系统监测后的验证结果，逐步验证到 **30%~50%** 的总收益区间（机器/工作流差异较大）。

## 4.1 C 层落地执行记录（本次）

1. 前端：
   1. `frontend/src/App.tsx` 的 status/operator/kill-switch/agent-runs/health/infra-events 轮询改为 `restarting ? 2000ms : 15000ms`。
   2. `risk-status` 轮询改为 `restarting ? 3000ms : 30000ms`。
2. SSE 空转：
   1. `aletheia/server/routes/runtime.py` 的事件流 sleep 改为指数退避（1→2→4→8 秒）。
   2. `aletheia/server/routes/operator.py` 事件流 sleep 同步改为指数退避（1→2→4→8 秒）。
3. 结果预期：
   1. 空闲打开 Dashboard 时保持可控的低频唤醒特征。
   2. 重启/控制操作后的可见性仍保持秒级，无明显控制延迟增长。

## 5. 验收动作（你这台机器可直接执行）

1. A/B 对比基线：分别记录 5~10 分钟 `Activity Monitor`（Energy/12hr power）与 App Nap 标记。
2. 对齐窗口，观察平均与峰值 Energy Impact 与 `App Nap` 的变化。
3. 对齐后再观察前端关键操作（重启、停止、更新）的 2 秒内可见性是否满足。

## 6. 文档落地说明

1. 主分析文档：本文件。
2. 托盘行为权责说明：`docs/operations/PACKAGED_RUNTIME.md`。
3. 相关执行设计边界：`docs/plans/packaged_tray_cli_runtime.md`，其中约束了托盘只做进程/生命周期控制，不做交易语义。
