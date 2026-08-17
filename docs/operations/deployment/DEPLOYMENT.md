# Deployment Guide

> Gateway 生命周期管理 + 看门狗 + 监控。本文描述 Windows 11 与当前 Fedora source-checkout 运行实例。

---

## Gateway 三进程架构

```
Task Scheduler 看门狗（每5min）
    ↓
supervisor (lifecycle.py)
    ├── frontdoor :8787  ← 用户入口（反向代理 + SSE）
    └── backend   :8788  ← FastAPI + APScheduler + SQLite
```

**不要手动 kill 进程**——用 CLI 统一管理，否则看门狗会干扰。

---

## 常用命令

```powershell
# 启动
uv run aletheia gateway start --no-browser

# 停止
uv run aletheia gateway stop

# 重启（代码改动后必须）
uv run aletheia gateway restart --no-browser

# 查看状态
uv run aletheia gateway status

# 查看日志（多日志尾部，已脱敏）
uv run aletheia gateway logs

# 显式查看并应用待执行数据库迁移（部署/恢复窗口）
uv run aletheia data migrate

# 只读查看容量、最大对象和最近 30 次夜间样本
uv run aletheia data capacity --deep --history 30
```

## Fedora 运行实例：自动部署与恢复 SOP

Fedora 是当前长期运行实例；macOS 只用于开发和控制。正常路径保留一个
operational-need gate：owner 在确认需要更新 Runtime 后运行一次 GitHub
Actions 批处理，合并到 `master` 的 release-bearing 提交才会进入该批次：

1. `Prepare and tag releases` 以 `operational_need=true` 生成一个候选 PR；候选分支
   运行精确 SHA CI，验证通过后由 trusted auto-merge 自动合并。普通 release-bearing
   PR 不会各自产生一个产品版本。
2. 合并后的 follow-up 自动创建不可变 `vX.Y.Z` Tag 和 GitHub Release。
3. 只有明确配置为 `paper` 角色的 Fedora source runtime 才由
   `aletheia-auto-update.timer` 每 5 分钟检查稳定 Tag，执行 `aletheia update auto run`；
   它只接受严格 SemVer，并执行精确 SHA、迁移、SQLite、Smoke、Gateway 和
   Paper/live-disabled 健康门禁。qualification runtime 默认锁定，不随正式 Tag 自动改变。

候选 PR 打开后若普通 PR 又推进了 `master`，可信的 refresh workflow 会在已有候选存在时
以真实 branch merge-base 判断候选是否过期，并用自身 workflow run 身份认证后自动更新并
重新验证同一个候选；没有 owner 先前的 `operational_need=true` 意图时不会凭空创建发布候选。

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
完成自动或手动恢复后只做以下验收：

1. Fedora `git rev-parse HEAD` 等于预期 merge SHA；
2. `/api/status` 的 `boot_commit == current_commit ==` 预期 SHA；
3. `/api/health` 为 healthy，Gateway 与 scheduler 正在运行；
4. 仍为 `paper_internal` / `local_paper`，`live_disabled`；
5. Provider、Outcome settlement 和目标 Evaluation evidence 正常继续采集。

每次部署都记录新的 Runtime provenance segment。只读审计、文档或不改变 behavior contract
的修复不要求随机 A/B；是否延续 cohort 仍由显式 comparability/admission 判断。部署会改变
选择、仓位、Risk、成本模型或实验 identity 的代码时，按
[Experiment Protocol](../../product/experiments/EXPERIMENT_PROTOCOL.md) 关闭受影响窗口并从首次
eligible exposure 开新窗口。PR merge 本身不等于部署。

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
旧 Gateway 继续服务期间完成依赖同步与前端 staging build。通过后才停止 Gateway、创建最终
Backup、fast-forward 到固定 SHA、切换完整静态资源并启动。120 秒内必须证明
`boot_commit == current_commit == tag_sha`、产品版本、migration ledger、Gateway/scheduler/
provider/settlement health、`paper_internal` / `local_paper` 及 `live_disabled`。成功会写入
runtime provenance/audit，scheduler 会自行继续 Observer，不需要人为重开。

每个失败路径写入 `${ALETHEIA_DATA_DIR}/update/auto-deploy.json` 和 audit，且不会记录凭证：

- fetch/网络/基线暂时失败保持旧版本，下一周期重试；
- 构建、Smoke 或预检失败的 Tag 被永久跳过，等待修复后的新正式 Release；
- 激活失败会恢复最终 Backup、旧 SHA、旧依赖和旧静态资源，并重启旧 Gateway；
- 数据恢复或旧 Gateway 恢复失败会进入全局 hold。完成排障后才执行
  `uv run aletheia update auto resume`。

这条 source-runtime 路径不替代 draft
[Fedora immutable Runtime updates Proposal](../../design/proposals/fedora_immutable_runtime_updates.md)：
该 Proposal 仍描述未来的签名不可变 Runtime，不是此 Release-Tag 自动部署的前置条件。

## 数据库迁移边界

- 每个 Migration 使用稳定 ID、不可变 checksum，并在 `aletheia_schema_migrations` 中留下成功账本；同一 ID 不会因重启而重跑。
- `gateway start/restart` 在创建 supervisor/frontdoor/backend **之前**执行一次 pending-migration 检查和必要升级；完成后才启动 15 秒 readiness 计时，因此大型首次升级不会伪装成 Gateway readiness 超时。
- supervisor 的 Runtime 读取、frontdoor/backend 启动上下文、FastAPI lifespan、API 路由和 scheduler 都只有只读验证权限，不能触发升级。
- 新空库或没有账本的旧库会执行一次冻结的 legacy baseline。checksum 漂移、数据库含当前代码未知的 migration ID、或升级异常都会 fail closed，并保留明确的 migrate/doctor/log 指针。
- Schema v3 会在升级阶段一次性重建 `knowledge_fts` 并建立启动/遥测热路径索引；后续普通 Gateway 启动不会再全量 reindex。需要人工修复 FTS 时使用显式 `uv run aletheia knowledge reindex`。
- pending migration 执行时 CLI 显示步骤进度、精确 elapsed 和宽范围 ETA。ETA 会标明估算依据；超过范围时只显示仍在执行，不伪造完成百分比。重定向输出仍保留最终步骤和实际耗时。
- 受控 `update` 会在激活候选版本时执行同一 migration apply/SQLite validation；普通重启在账本已 current 时仅有毫秒级核对。

## 数据库容量与空间回收

- 默认容量预算为 2048 MiB，只用于 doctor/infra 告警，不会阻止审计或 Learning 写入。
- 夜间 cleanup 后的精确物理/逻辑体积写入 `logs/database_capacity.jsonl`；先用至少
  3 个且横跨至少 7 天的样本判断初步斜率，生产容量判断仍建议观察 30 天。
  `data capacity` 使用抗单次 WAL/checkpoint 尖峰的 median pairwise slope，并明确标注
  provisional/established，而不是用两个偶然文件点给出伪精确 ETA。
- 每个应用 SQLite 连接都设置 64 MiB `journal_size_limit`。夜间 cleanup 运行一次
  非阻塞 PASSIVE checkpoint，并把 log/checkpointed/remaining frame 数量写进同一历史；
  连续 backlog 通常意味着长期 reader 或外部进程钉住了旧快照，应先诊断而不是阻塞运行时。
- 普通 DELETE 只把页放入 freelist；`auto_vacuum=NONE` 的旧库不会因
  `incremental_vacuum` 缩小文件。需要回收操作系统空间时执行：

```powershell
uv run aletheia data compact             # 只读计划
uv run aletheia gateway stop
uv run aletheia data compact --apply     # 校验、VACUUM、启用 incremental reclaim
uv run aletheia gateway start --no-browser
uv run aletheia doctor
```

`--apply` 会拒绝仍在运行的 Gateway 或不足的临时磁盘空间，并先创建、校验
`backup_pre_compact_*.tar.gz`。它持有目标数据库的跨进程 writer permit，显示各阶段
elapsed，校验 integrity/foreign keys，并在 VACUUM 前把符合预期 schema 的
`market_ohlcv` 重建为 `WITHOUT ROWID`；它不删行、不缩短保留期。该物理重建不属于
Migration，也不会在 Gateway 启动或首次部署时自动执行。

日常 cleanup 在正常容量下每次最多回收 16 MiB freelist；达到 80% 容量水位后最多
回收 64 MiB。操作持有跨进程 writer permit，只改变空闲页分配，不删除任何保留行。

---

## 前端部署

生产 source-checkout 更新使用 `uv run aletheia update`；它总是在未服务的 staging
目录完成一次前端构建，只有 dependency lock 变化时才额外执行 `npm ci`，
Smoke 通过后才切换静态产物并重启：

```powershell
uv run aletheia update
```

构建产物先写入未服务的 staging 目录；构建、烟测或重启失败会保留/恢复旧版本，不会把半成品 UI 暴露给用户。手动维护时，前端代码改动仍必须重新 build：

```powershell
cd frontend
npm run build
# 产物写入 aletheia/dashboard_static/（gitignored）
cd ..
uv run aletheia gateway restart --no-browser
```

注意 PWA 缓存陷阱：即使 build 完成，用户浏览器若有旧 service worker，需要手动关闭浏览器或 unregister sw 才能看到新版本（服务端无法强制清除）。

---

## 健康检查

```powershell
# 完整系统诊断
uv run aletheia doctor

# HTTP 实测后端响应时间（直连，绕过 frontdoor 缓存）
Measure-Command { Invoke-WebRequest -Uri 'http://127.0.0.1:8788/api/health' -UseBasicParsing }

# 查看 doctor JSON（机器可读）
uv run aletheia doctor --json

# 交互排障：附带脱敏日志尾部、最近失败、下一步日志指针
uv run aletheia doctor --verbose

# 生成可交给 AI/人排障的脱敏诊断包
uv run aletheia doctor --bundle data/diagnostics/doctor.json
```

**铁律**：相信 `doctor` 的输出，不信文档里写的运行态描述（文档只写意图）。

---

## Task Scheduler 看门狗

系统已在 Windows Task Scheduler 注册开机自动拉起任务：

- 触发条件：用户登录（LogonTrigger）
- 频率：每 5 分钟检查
- 行为：若 gateway 不存活则自动 start

注意：Windows 任务计划程序 LogonTrigger 有显示缺陷（看起来没触发但实际在跑）——通过 `aletheia gateway status` 验证，不要信任务计划程序界面。

---

## 启动顺序（首次 / 恢复）

1. `uv run aletheia update`（拉取最新 `origin/<update.branch>` HEAD、按需安装依赖、构建前端、显式迁移/验证并重启）
2. 手动部署或数据库恢复时先运行 `uv run aletheia data migrate`；重复执行在账本 current 时是 no-op
3. `uv run aletheia doctor`（确认健康）
4. 打开 `http://127.0.0.1:8787/`

若必须手动维护，再使用 `git pull --ff-only`、前端 build 和 gateway restart 的分步流程。

---

## 常见问题

| 症状 | 原因 | 修复 |
|------|------|------|
| Web UI 黑屏 / JS 404 | `dashboard_static/` 未 build 或已被其他 worktree 污染 | `cd frontend && npm run build && cd ..` |
| `aletheia doctor` 报 paused=True | LLM provider 不可用触发自动暂停，或日损触发 kill_switch | 恢复 provider / 等 UTC 0 点 / 手动 resume |
| 前端改完用户仍看旧 UI | PWA service worker 缓存 | 用户关闭浏览器重新打开，或 DevTools → Application → Service Workers → Unregister |
| gateway 卡死但 TCP 仍在监听 | frontdoor HTTP 超时（历史 bug，已修）| `aletheia gateway restart` |
| SQLite "database is locked" | WAL 并发写冲突（现已修）| 检查是否有多个 gateway 实例在跑 |

---

## Worktree 开发注意

在 worktree 分支上开发前端时：

- worktree 的 `data/` 目录为**空**，在 worktree 里 restart gateway 会让交易系统空跑
- 前端上线必须 merge 回主仓，在主仓 restart，再做 HTTP 实测
- 详见 [`Git Worktree Playbook`](../development/GIT_WORKTREE_PLAYBOOK.md)

---

## 安全规则

- 永远不在日志/文档/提示词/git 里暴露 API key / secret
- 实盘模式默认关闭，启用需通过 Web UI 明确操作
- 风险参数变更需 owner 手动确认，禁止 agent 自动修改
