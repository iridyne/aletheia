# Deployment — retired and superseded sections

> **History (moved 2026-10-10).** Retired or superseded operations text kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`DEPLOYMENT.md`](../../operations/deployment/DEPLOYMENT.md) and
[`CROSS_PLATFORM_SERVICE_ENVIRONMENT.md`](../../operations/deployment/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md)
when they were rewritten. Reasons: Windows is never a runtime target or fallback; remote CI is
retired; the Fedora runtime is a pinned qualification runtime that does not follow Release Tags;
the experiment-runtime retirement was a one-time cutover; the migration-boundary rules belong to
[data_model.md §6](../data/data_model.md). The `update auto *` code described below still exists
as residual code for an explicitly opted-in `paper`-role runtime.

## From DEPLOYMENT.md — header and three-process diagram

> Gateway 生命周期管理 + 看门狗 + 监控。本文描述 Windows 11 与当前 Fedora source-checkout 运行实例。

---

### Gateway 三进程架构

```
Task Scheduler 看门狗（每5min）
    ↓
supervisor (lifecycle.py)
    ├── frontdoor :8787  ← 用户入口（反向代理 + SSE）
    └── backend   :8788  ← FastAPI + APScheduler + SQLite
```

**不要手动 kill 进程**——用 CLI 统一管理，否则看门狗会干扰。

## From DEPLOYMENT.md — Release-Tag automatic deployment and remote-CI release batch

### Fedora 运行实例：自动部署与恢复 SOP

Fedora 是当前长期运行实例；macOS 只用于开发和控制。正常路径保留一个
operational-need gate：owner 在确认需要更新 Runtime 后运行一次 GitHub
Actions 批处理，合并到 `master` 的 release-bearing 提交才会进入该批次：

1. `Prepare and tag releases` 以 `operational_need=true` 生成一个候选 PR，但不自动
   启动 CI。只有 owner 在当前任务中明确要求远程 CI，才可在候选分支运行
   `Aletheia CI`；发布、部署或“完整验证”的表述本身不构成授权。**2026-09-26 起
   auto-merge 已退役（PR #3506）**，验证通过后由 owner 手工 `gh pr merge <n> --rebase`
   合并候选 PR；tag 与 Release 的自动 follow-up 保留。普通 release-bearing PR 不会
   各自产生一个产品版本。
2. 合并后的 follow-up 自动创建不可变 `vX.Y.Z` Tag 和 GitHub Release。
3. 只有明确配置为 `paper` 角色的 Fedora source runtime 才由
   `aletheia-auto-update.timer` 每 5 分钟检查稳定 Tag，执行 `aletheia update auto run`；
   它只接受严格 SemVer，并执行精确 SHA、迁移、SQLite、Smoke、Gateway 和
   Paper/live-disabled 健康门禁。qualification runtime 默认锁定，不随正式 Tag 自动改变。

Linux source 的人工 `aletheia update` 拉取配置的 `origin/<update.branch>` HEAD；timer
单独使用 `auto.run` 的稳定 Tag 事务。两条路径都保留 settlement/Observer 门禁及失败恢复。
Observer continuity 快照、preflight/postflight 证明缺口与失败恢复语义见
[Observer-aware source update continuity](../../operations/deployment/UPDATE_OBSERVER_CONTINUITY.md)。
HTTP source apply 仅返回 CLI 指引；status/progress 只读持久事实，不 fetch、不清 hold，
也不以 master 的 ahead/behind 数量宣称已部署。

一次 `aletheia update` 是分钟级事务，并且会被 settlement/Observer continuity 门禁阻断：
不要把它当成发现问题的循环。先把已知失败收集齐（聚焦测试、真实路径 smoke、runtime
preflight），按可独立复核的批次合并，再部署一次；门禁失败不会改变已部署的 commit，重试前
先读 `update status` 与门禁原因，不要因为「可能没执行」就反复重跑。

已明确配置为 `paper` 角色的 runtime（当前 shell 或已安装的 `aletheia-auto-update.service`
设置了 `ALETHEIA_RUNTIME_ROLE=paper`），只允许以下精确部署组合：
`bitget_demo` / `external_exchange`，并且必须为 `live_disabled`。
其他模式和交叉组合均拒绝。此准入同时
适用于人工更新、自动更新及重启后的验证，不改变交易模式、Risk、凭据、
健康、结算、备份、Observer continuity 或历史经济证据规则。

实验 runtime registry/shadow/promote 控制面已退役。升级前先停 owned timer，持有部署锁，
核实实际 backend cwd/SHA；将标准 checkout 对齐同一 SHA，完成正常停止、settlement 与
联合 checkpoint/authoritative backup，再使旧版本的 current artifact 指向标准目录。
重绑 owned 服务并证明 cwd、boot SHA、frontdoor PID、健康与 runtime session 一致后，
才允许切换删线版本。离线运行 `aletheia runtime retire-experiment-schema --backup <archive>`
验证备份中的原表 schema/rows 与当前一致后退役五张实验表，再运行 `aletheia data migrate`。
有未保存改动、非主线祖先、未决实验进程或缺备份证明时必须阻断；不重置历史证据。
存量目录迁移和恢复验证是部署前提，本地测试不代替 Fedora 上线证明。

候选 PR 打开后若普通 PR 又推进了 `master`，owner 重新运行 `Prepare and tag releases`
（`operational_need=true`）即可——Release Please 会在已有候选存在时更新同一个候选而不创建
第二个发布 PR。没有 owner 明确的 `operational_need=true` 意图时不会凭空创建发布候选。
（2026-09-26 之前这一步由 `refresh-release-candidate.yml` 自动完成，已在 PR #3506 中随
auto-merge 控制面一起退役——见 `docs/design/history/modular_monolith_release_bundle.md`（已退役）的 "Retired candidate-refresh
automation" 段。）

正常发布不需要手动登录 Fedora。以下命令只用于首次启用 timer、故障恢复或诊断：

```sh
# 仅在明确的非 qualification Paper runtime 上执行一次安装
ssh fedora 'cd /home/admin/aletheia && ALETHEIA_RUNTIME_ROLE=paper uv run aletheia update auto install'
ssh fedora 'cd /home/admin/aletheia && ALETHEIA_RUNTIME_ROLE=paper uv run aletheia update auto status'
```

若自动部署连续失败，才使用 source-checkout 的应急一键更新入口：

```sh
ssh fedora 'cd /home/admin/aletheia && uv run aletheia update'
```

这个 source-checkout 命令固定执行 fast-forward、按需依赖同步、把前端完整构建到
staging 目录、Smoke、Gateway 重启以及启动期 Migration/SQLite 校验；不要再包一层部署脚本。
`update --no-restart` 仅保留 source checkout 的应急控制；packaged runtime 的同一个裸命令自动走签名发布流程。

## From DEPLOYMENT.md — unattended upgrade boundary (Release-Tag timer)

### 无人值守升级边界

Fedora Paper Candidate 只跟随正式 Release Tag；Release Please 自动创建正式
`vX.Y.Z` Tag，人不登录 Fedora 执行 `git pull`。只接受严格稳定 SemVer：
`v1.2.3` 可以部署，`v1.2.3-rc.1`、`-hold.1`、`-noauto.1` 和其他预发布 Tag
一律忽略。下一个正式 Tag 会自然越过已忽略的预发布候选。

只有明确的 operational-need 批处理启动完整 release 链路；隐藏的 `docs`、`ci`、`chore`
提交只会累积，不会凭空发布版本。只有 `ALETHEIA_RUNTIME_ROLE=paper` 写入 systemd unit 的 Paper
runtime 才安装两个用户级 systemd unit：开机约 1 分钟首次运行，之后每 5 分钟
（`Persistent=true`，随机延迟最多 30 秒，以上一次 service 完成后计时）运行的
`aletheia-auto-update.timer`，以及它
唤醒的 oneshot service。缺少该角色的 runtime 每次安全跳过部署，因此 qualification
runtime 保持固定。升级新版本前，旧版本安装的 Paper timer 也必须完成一次角色迁移；
否则 `auto run` 会明确返回 `qualification_runtime_pinned` 并保持旧版本，避免把未知角色
误当成 Paper。安装/迁移命令为：

```sh
# 新安装或迁移旧的、由 Aletheia 管理的 Paper timer
ALETHEIA_RUNTIME_ROLE=paper uv run aletheia update auto install
ALETHEIA_RUNTIME_ROLE=paper uv run aletheia update auto status
```

如果迁移命令没有同时设置 `ALETHEIA_DATA_DIR`，Aletheia 会从自己管理的旧
service unit 读取并保留原有数据根；不会静默切回 `~/.aletheia`。非 Aletheia
所有的 unit 仍按 ownership marker 拒绝覆盖。

安装不会重启或改变 Gateway；如需移除旧 timer，仍只删除自己带 ownership marker 的 unit：

```sh
uv run aletheia update auto uninstall
```

每次运行先执行 `git fetch origin master --tags`，把候选固定到 Tag commit SHA，再验证
它在 `origin/master` 历史中、代码 `__version__` 与 Tag 相同、本地 checkout 是干净且可
fast-forward 的 `master`。已观测 Tag 被移动、checkout ahead/diverged、非 Paper、Live
未关闭或当前 Gateway 基线不健康都会拒绝切换。它不会跟随 mutable `master` HEAD，也不使用
GitHub API。

候选会先以临时 worktree 在 WAL-safe Backup 的恢复副本上执行 migration/SQLite validation；
数据副本位于 checkout 和稳定运行目录之外的 `/var/tmp`（多 GB 的恢复副本放不进 tmpfs 型 `/tmp`，#3719），预检成功或失败后均清理。
Smoke 的 pytest 数据根门禁保持 fail-closed，不为候选 worktree 增加例外。
Post-pull Smoke 保留 import gate 的目标上下文，但 pytest 子进程另用自动清理的临时
数据根，不继承实际 Runtime 或候选恢复副本的 `ALETHEIA_DATA_DIR`。
旧 Gateway 继续服务期间完成依赖同步与前端 staging build。通过后才停止 Gateway、创建最终
Backup、fast-forward 到固定 SHA、切换完整静态资源并启动。120 秒内必须证明
`boot_commit == current_commit == tag_sha`、产品版本、migration ledger、Gateway/scheduler/
provider/settlement health、上述已批准的模式/target 组合及 `live_disabled`。成功会写入
runtime provenance/audit，scheduler 会自行继续 Observer，不需要人为重开。
既有 scheduler 日志提供 `scheduler_job_result job_id=... outcome=succeeded|failed`
作为 SQLite 清理后的终态证据，只含任务身份与结果，不含异常载荷。它不代表交易或
Alpha 资格；被 Learning gate 跳过的任务不会冒充 succeeded。

每个失败路径写入 `${ALETHEIA_DATA_DIR}/update/auto-deploy.json` 和 audit，且不会记录凭证：

- fetch/网络/基线暂时失败保持旧版本，下一周期重试；
- 构建、Smoke 或预检失败的 Tag 被永久跳过，等待修复后的新正式 Release；
- 激活失败会恢复最终 Backup、旧 SHA、旧依赖和旧静态资源，并重启旧 Gateway；
- 数据恢复或旧 Gateway 恢复失败会进入全局 hold。完成排障后才执行
  `uv run aletheia update auto resume`。

这条 source-runtime 路径不替代 draft
[Fedora immutable Runtime updates Proposal](../../design/proposals/fedora_immutable_runtime_updates.md)：
该 Proposal 仍描述未来的签名不可变 Runtime，不是此 Release-Tag 自动部署的前置条件。

## From DEPLOYMENT.md — database migration boundary (details)

### 数据库迁移边界

- 每个 Migration 使用稳定 ID、不可变 checksum，并在 `aletheia_schema_migrations` 中留下成功账本；同一 ID 不会因重启而重跑。
- `gateway start/restart` 在创建 supervisor/frontdoor/backend **之前**执行一次 pending-migration 检查和必要升级；完成后才启动 15 秒 readiness 计时，因此大型首次升级不会伪装成 Gateway readiness 超时。
- supervisor 的 Runtime 读取、frontdoor/backend 启动上下文、FastAPI lifespan、API 路由和 scheduler 都只有只读验证权限，不能触发升级。
- 新空库或没有账本的旧库会执行一次冻结的 legacy baseline。checksum 漂移、数据库含当前代码未知的 migration ID、或升级异常都会 fail closed，并保留明确的 migrate/doctor/log 指针。
- Schema v3 会在升级阶段一次性重建 `knowledge_fts` 并建立启动/遥测热路径索引；后续普通 Gateway 启动不会再全量 reindex。需要人工修复 FTS 时使用显式 `uv run aletheia knowledge reindex`。
- pending migration 执行时 CLI 显示步骤进度、精确 elapsed 和宽范围 ETA。ETA 会标明估算依据；超过范围时只显示仍在执行，不伪造完成百分比。重定向输出仍保留最终步骤和实际耗时。
- 受控 `update` 会在激活候选版本时执行同一 migration apply/SQLite validation；普通重启在账本已 current 时仅有毫秒级核对。

## From DEPLOYMENT.md — Task Scheduler watchdog

### Task Scheduler 看门狗

系统已在 Windows Task Scheduler 注册开机自动拉起任务：

- 触发条件：用户登录（LogonTrigger）
- 频率：每 5 分钟检查
- 行为：若 gateway 不存活则自动 start

注意：Windows 任务计划程序 LogonTrigger 有显示缺陷（看起来没触发但实际在跑）——通过 `aletheia gateway status` 验证，不要信任务计划程序界面。

## From DEPLOYMENT.md — historical FAQ rows

| 症状 | 原因 | 修复 |
|------|------|------|
| gateway 卡死但 TCP 仍在监听 | frontdoor HTTP 超时（历史 bug，已修）| `aletheia gateway restart` |
| SQLite "database is locked" | WAL 并发写冲突（现已修）| 检查是否有多个 gateway 实例在跑 |

## From DEPLOYMENT.md — worktree development note

### Worktree 开发注意

在 worktree 分支上开发前端时：

- worktree 的 `data/` 目录为**空**，在 worktree 里 restart gateway 会让交易系统空跑
- 前端上线必须 merge 回主仓，在主仓 restart，再做 HTTP 实测
- 详见 [`Git Worktree Playbook`](../../operations/development/JJ_WORKSPACE_PLAYBOOK.md)

## From CROSS_PLATFORM_SERVICE_ENVIRONMENT.md — Windows/macOS service framing

This project runs as a long-lived background service on both Windows and macOS.
The service manager environment is authoritative for runtime dependencies; an
interactive terminal succeeding does not prove that Task Scheduler or launchd
can run the same command.

### Rules

1. Resolve required executables with this order: explicit project override,
   service `PATH`, then stable platform-specific install locations.
2. Do not persist temporary shell-session paths. Version-manager session paths
   such as fnm multishell directories can disappear after logout or reboot.
3. Keep Windows behavior covered when adding macOS support. Platform additions
   must not replace existing PATH discovery or Windows process handling.
4. Diagnose the child capability independently from its parent process. A
   healthy gateway does not prove the Node operator shell is online.
5. Historical failures and current faults are different states. Preserve both,
   but only current or repeated faults should make `aletheia doctor` unhealthy.

The macOS fallback deliberately uses fnm's `aliases/default` path, not a
`fnm_multishells` path created for one terminal session.

### Verification checklist

- Run focused lifecycle, operator-shell supervision, and doctor tests.
- Start/restart through `uv run aletheia gateway ...`; never validate a worktree
  by starting its empty runtime directory.
- Run `uv run aletheia doctor --json --verbose` from the main checkout after
  merge and restart.
- Confirm `process.gateway`, `process.backend`, and `operator.shell` separately.
- On platform-specific changes, retain at least one test for the other platform's
  path/process convention.

### Known platform pressure points

- Service-manager environment and executable discovery
- Process-tree ownership, replacement launchers, and signal/exit-code semantics
- Path separators, quoting, case sensitivity, and executable suffixes
- File locking and atomic replace behavior
- Console encoding and Unicode-safe CLI output
- Login/reboot registration and watchdog status parsing

Add focused evidence as these areas are exercised; do not infer compatibility
from a successful interactive run.
