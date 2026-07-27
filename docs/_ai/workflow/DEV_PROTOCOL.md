# Production-grade 多实例与 Qualification 开发协议

> 所有启动实例都是同一种 Production-grade Runtime；用途和运行时长是 owner 意图，
> 不是不同的软件模式。

本文是跨机器开发、多实例运行、部署与观察窗口的权威协议。Worktree 细节见
`GIT_WORKTREE_PLAYBOOK.md`，实例运行合同见
`docs/operations/development/MULTI_INSTANCE_RUNTIME.md`，交易实验冻结规则见
`docs/product/experiments/EXPERIMENT_PROTOCOL.md`。

## 1. 要同时满足的两个需求

Aletheia 同时存在两种合理节奏：

1. 高速开发：多个 Agent、Worktree 和 PR 持续推动 `master`；
2. 稳定运行：一个或多个固定 SHA 的实例长期积累可比较的 paper、Learning 和稳定性证据。

高速开发不能反复重启长期实例、改变它们的证据对象；长期实例也不能要求 `master` 停止前进。
需要即时确认功能时，应从当前 `master HEAD`、PR SHA 或 Worktree 启动另一个完整实例，
使用独立身份和资源进行短期验证。

```text
fast-moving Git history
        |
        +--> instance A: long_running, pinned SHA, continuous evidence
        |
        +--> instance B: feature_validation, current master/PR SHA, short-lived
        |
        +--> instance C: another independent goal, short- or long-lived
```

这里没有 Preview Runtime。A、B、C 使用同一种 Runtime；差异只来自 owner 配置的用途、
代码身份、数据、端口、权限和生命周期。

## 2. Production-grade 的含义

一个实例只有同时满足以下条件，才能称为 Production-grade：

- 使用正常 release/runtime 启动路径，而不是降级的 API-only 或 shadow-only 代码分支；
- 拥有独立且可审计的 instance identity、exact commit、配置、数据根和日志；
- 使用正常 Gateway、scheduler、storage、health、doctor 和 lifecycle 合同；
- 启停、异常恢复、状态显示和审计不依赖另一个实例；
- 通过与长期实例相同的配置验证、数据库迁移和 focused safety checks；
- 权限和执行目标来自该实例的显式配置，不从“短期测试”目的推导；
- 停止或失败时，不影响同机其他实例。

Production-grade 不等于 live-authorized。一个完整实例可以处于 shadow、`paper_internal`、
Demo 或 Live；Risk approval、live unlock、credentials 和账户所有权仍按原合同生效。

## 3. 运行意图不是 Runtime 类型

每个实例记录一个 owner intent，用于解释为什么它在运行，但不改变软件能力：

| intent | 典型时长 | 代码策略 | 证据用途 |
|---|---|---|---|
| `long_running` | 天、周或更久 | 固定 exact SHA | 稳定性、paper、Learning、目标验证 |
| `feature_validation` | 分钟到小时 | 当前 master/PR exact SHA | 验证一个功能是否符合预期 |
| `qualification` | 明确 observation window | 固定 exact SHA + policy epoch | 可用于晋级判决的受控证据 |

这些 intent 可以显式修改。`feature_validation` 实例如果值得保留，可以由 owner 改为
`long_running` 或 `qualification`，无需更换 Runtime 类型；但：

- 必须记录转换时间、exact commit、config/policy identity；
- Qualification window 从显式指定时开始；
- 转换前的测试数据保留为历史，不能默认并入新 cohort；
- 不允许因为进程“已经跑了一会儿”就隐式获得长期或 Qualification 身份。

## 4. 每个实例的最小身份合同

机器本地的 instance manifest 至少包含：

| 字段 | 合同 |
|---|---|
| `instance_id` | 全局唯一、创建后不可变；人类名称只是 alias |
| `intent` | owner 对运行目的的描述，不授予权限 |
| `code_commit` | 启动时解析的 exact SHA，不能只写 `master` |
| `artifact_or_checkout` | 实际启动的 release artifact 或 checkout |
| `data_root` | 绝对路径，只属于该实例 |
| `public_port` / `backend_port` | 主机内唯一的端口对 |
| `execution_mode` | shadow、paper_internal、Demo 或 Live |
| `account_scope` | 外部账户/环境/模式标识；无外部账户时为空 |
| `lifecycle_owner` | 有权启停和变更该实例的 owner |
| `created_at` / `started_at` | 本地运行事实 |
| `qualification_started_at` | 仅 qualification intent 使用 |

manifest、PID、数据库、配置、凭证、当前健康和 observation 进度都是机器本地运行事实，
不得作为“当前状态”写进 Git。Git 只保存实例合同、实现、测试和历史决策。

## 5. 资源隔离合同

### 5.1 数据

- 每个实例独占一个 data root、`app.db`、config、audit log、cache 和 artifact tree；
- 两个实例不得同时读写同一个 SQLite 数据库；
- 不复制另一个实例的 `app.db`、positions 或 outcome state 来创建测试实例；
- 需要种子数据时，使用显式、只读、可追踪的 export/import 流程；
- 删除实例数据必须先确认实例已停止，并使用显式 instance ID 和确认参数。

目标布局：

```text
~/.aletheia/instances/
  <instance-id>/
    instance.yaml
    app.db
    config.yaml
    logs/
```

现有 primary `~/.aletheia` 可以在迁移期映射为默认实例，不能因此继续把 lifecycle 设计成
全局单例。

### 5.2 机器级协调面

完全隔离的数据根无法单独判断主机端口和外部账户是否已被其他实例占用。因此主机需要一个
最小、机器本地的 Runtime registry，只负责：

- instance ID 与人类 alias；
- active PID/service identity；
- public/backend port reservation；
- 本机 external account writer lease；
- lifecycle owner 与最后一次状态。

registry 不保存交易状态、Agent 任务、Learning 数据或开发编排信息。写入必须原子化并带
进程存活校验；陈旧 lease 的回收必须审计，不能只按时间静默抢占。

### 5.3 端口与进程

- 每个实例拥有独立 public/front-door port 和 backend port；
- `8787/8788` 可以保留给默认日常实例，但不代表其他实例是降级版本；
- 端口分配必须在启动前原子检查；冲突时 fail closed，不杀死已有进程；
- PID、supervisor、autostart/service name、logs 和 health target 都必须包含 instance ID；
- `stop/restart/update` 必须显式解析目标实例，不能误操作同机其他实例；
- Tray、CLI 和 WebUI 必须显示 instance ID、exact commit、intent、ports 和 data root。

### 5.4 主机容量

每个实例仍遵守 SQLite writer lock、thread-local connection 和 scheduler `max_workers <= 3`
等单实例约束。多实例带来的总 CPU、内存、磁盘、模型额度和外部 API 配额由主机容量门槛
额外限制；“每个实例都安全”不等于“无限实例同时运行也安全”。

## 6. 权限与外部账户所有权

实例用途不能授予交易权限：

- `feature_validation` 不自动等于 shadow，也不自动获得 Demo/Live；
- 短期功能验证默认推荐 `paper_internal` 或无外部执行权；
- 若测试必须访问外部账户，仍要经过正常 secrets、Risk、permission 和 execution 合同；
- 同一个 `exchange account + environment + execution mode` 同一时刻最多一个 writer；
- 第二个实例申请同一 account scope 的写权限时必须 fail closed；
- 只读 market data provider 可以共享外部数据源，但每个实例独立记录 provenance 和限流状态；
- Live 仍默认关闭，不能因为实例是 Production-grade 就跳过 live unlock。

外部账户单写者是跨实例的硬安全边界，不是操作建议。

机器级 registry 只能强制同一主机内的唯一写者，不能伪装成分布式锁。跨机器运行时，
Production-grade 支持边界要求每个可写实例使用独立 exchange subaccount/credential scope；
如果做不到，必须由外部 owner 明确指定唯一 writer，其他实例配置为无外部写权限。未来若
引入跨机器 lease，需要单独的安全设计和故障模型，不能用共享文件或最终一致状态冒充。

## 7. 多实例生命周期

目标 CLI 语义以 instance ID 为中心：

```text
aletheia instance create <id> --commit <sha> --intent <intent> ...
aletheia instance start <id>
aletheia instance status <id>
aletheia instance doctor <id>
aletheia instance logs <id>
aletheia instance stop <id>
aletheia instance set-intent <id> ...
aletheia instance remove <id> --confirm
```

具体命令尚未实现，以上是 SDD 合同，不是当前可执行说明。最终实现必须：

- 对单一默认实例保留兼容入口；
- 所有变更命令显式显示目标 instance ID；
- 读命令可枚举实例，写命令不得默认广播；
- `remove` 只接受已停止实例并保护默认/长期实例；
- update 是“选择新 artifact/SHA 给某个实例”，不是全机所有实例跟随 `master`。

外部开发编排器继续负责 Agent、Worktree、终端和临时任务；Aletheia 只管理自己的 Runtime
实例，不在仓库中实现开发任务数据库或 worker lease。

## 8. 高速开发与短期功能验证

### 8.1 开发线

1. 从最新 `master` 创建独立 Worktree；
2. focused test 先行，完成最小修改；
3. PR 和 CI 证明代码合同；
4. PR merge 不部署或重启任何已运行实例。

### 8.2 短期实例

需要观察真实启动、scheduler、API/UI 或持久化行为时：

1. 选择 current `master HEAD`、PR SHA 或 Worktree exact SHA；
2. 创建独立 instance ID、data root、port pair 和配置；
3. 通过同一 Production-grade lifecycle 启动；
4. 仅授予测试所需的最小 execution/account scope；
5. 运行预先写明的 acceptance checks；
6. 验证结束后停止；数据是否保留由 owner 明确决定。

短期实例有三种合法后续：

1. 测试失败：停止并继续开发；
2. 功能符合预期：停止并保留 PR/CI/测试记录；
3. 值得长期运行：显式修改 intent、固定 identity，并按新的目标开始观察。

## 9. 长期与 Qualification 实例

长期实例可以分别服务不同目标，只要身份、数据、端口和账户 scope 独立。Qualification
实例在 observation window 内必须：

- 固定 exact commit、config/policy epoch、数据根和 outcome contract；
- 不跟随 `master`，不因普通 PR merge 自动 update；
- 不改变 decision input、filter/ranker、prompt、sizing、guard、Risk policy 或 economics；
- 不混入其他 instance、commit 或 policy epoch 的 cohort；
- 只有阻断 Startup → Scheduler → Outcome → Verdict 的 A-class 问题，或错误交易事实/安全
  风险，才允许中断窗口部署修复；
- 部署任何新 commit 后，按 current-build identity 重新建立可授权窗口。

Bug 分类：

- **A — 立即修复**：crash/hang、错误 persistence/accounting、身份或 outcome 失真、安全
  bypass；
- **B — 新观察窗口**：任何可能改变选择、仓位、guard、quota、Risk 或 outcome 的改动；
- **C — 延后**：新 Agent、页面、市场、数据源和宽泛抽象。

## 10. Learning 与证据边界

所有 Learning、paper 和 outcome 记录必须携带 instance/runtime session、producer commit 和
policy identity。跨实例分析只能显式比较，不能默认合并。

- `feature_validation` 数据可以用于功能诊断，不自动用于 Learning 晋级；
- `long_running` 数据只有在合同完整时才可用于目标评估；
- `qualification` 数据只在自身冻结窗口内授权判决；
- instance intent 转换不回写历史记录的语义；
- UI/analytics 必须按实例和 epoch 显示数据来源，不能给出跨实例混合净值或贡献结论。

这使高速开发可以继续，同时保证长期 Learning 证据不会被最新 master 或短期测试污染。

## 11. 跨机器角色

### 开发控制面

- ORCA、Agent、Worktree、PR 和代码审查；
- frontend HMR、focused tests 和短期实例操作；
- 不在 Mac 上运行大规模 pytest；通过 SSH 或 self-hosted CI 执行；
- 不从开发 Worktree 隐式重启其他实例。

### Runtime/Qualification 主机

- 同时承载一个或多个 Production-grade 实例；
- 为每个实例提供独立 service、ports、data root 和 health target；
- 承载 self-hosted CI、完整测试和 stability soak；
- 不让 CI 或 `master` 更新正在观察的实例。

跨机器交接只在 Git 中记录代码和合同。接手机器重新运行 `git pull --ff-only`、`uv sync` 和
目标 instance 的 doctor，不继承另一台机器的进程、凭证或健康叙事。

## 12. 当前实现与目标差距

截至 #425：

- 当前稳定 Gateway、front door、supervisor、autostart、Tray 和 lifecycle 仍主要按单例设计；
- `aletheia dev api` 是 shadow/API 开发工具，不是本文定义的 Production-grade 实例；
- checkout-derived preview data root 和 Vite read-only proxy 可以保留为开发工具，但不能
  代表多实例目标已经实现；
- 缺少 instance manifest、实例级 lifecycle、端口对、service identity、doctor 投影和
  跨实例 account-writer guard；
- 因此 #425 是 #422 的设计后继，不宣称多 Production-grade 实例已经可用。

实现应分阶段进行：

1. instance identity、manifest、data root 和端口合同；
2. instance-scoped Gateway/supervisor/service/lifecycle；
3. status、doctor、logs、Tray 和 UI 的实例投影；
4. 跨实例外部账户单写者与 fail-closed 冲突检测；
5. migration、故障隔离和 Windows/self-hosted soak。

每一阶段都可独立停止和回滚；在对应验收完成前，不删除现有默认实例兼容路径。

## 13. 实现验收门槛

未来实现至少必须证明：

1. 同一主机两个实例走相同 Production-grade 启动路径并同时健康；
2. 两者的 identity、exact commit、ports、data、config、logs 和 PID 完全隔离；
3. 两者可独立运行 scheduler，停止/重启/更新一个不影响另一个；
4. 端口或 instance ID 冲突 fail closed；
5. 同一外部 account scope 的第二个 writer fail closed；
6. `paper_internal` 实例各自维护独立 ledger 和净值；
7. intent 转换是显式、审计化操作，不隐式改变权限；
8. Qualification evidence 按 instance + commit + policy epoch 隔离；
9. default single-instance 用户保持兼容；
10. Windows/self-hosted 完整 CI 和多实例 soak 通过。

在这些条件完成前，仓库只能声称“目标设计已定义”，不能声称“多 Production-grade 实例已支持”。
