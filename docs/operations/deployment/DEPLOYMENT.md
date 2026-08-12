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

## Fedora 新 Agent：只读上手

Fedora 是 owner 选定的长期运行实例，macOS 只是开发/控制面。新 Agent 在判断
Gateway、Provider、配置、Risk、仓位或 scheduler 状态前，先查询 Fedora；不得用本地
macOS 的空配置或临时 Gateway 代替。稳定入口如下：

| 项目 | 稳定入口 |
| --- | --- |
| SSH alias | `fedora` |
| Source checkout | `/home/admin/aletheia` |
| Dashboard/front door | Fedora 本机 `127.0.0.1:8787` |
| Backend | Fedora 本机 `127.0.0.1:8788` |
| Runtime/data authority | Fedora 当前 Gateway、API、primary data root |

先执行这一组无副作用检查；它不启动、停止或重启 Gateway：

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 fedora '
  cd /home/admin/aletheia &&
  hostname &&
  git rev-parse HEAD &&
  git branch --show-current &&
  uv run aletheia gateway status &&
  curl -fsS --max-time 5 http://127.0.0.1:8788/_infra/readyz &&
  curl -fsS --max-time 5 http://127.0.0.1:8788/api/status &&
  curl -fsS --max-time 5 http://127.0.0.1:8788/api/health
'
```

按问题继续读取最小 API，不要直接打印 `config.yaml`、进程环境、Keyring 或 secret 文件：

| 要回答的问题 | 只读权威入口 |
| --- | --- |
| 当前部署代码是否与启动代码一致 | `git rev-parse HEAD`；`/api/status` 的 `boot_commit`、`current_commit` |
| Gateway、scheduler、Provider、execution target | `/api/status`；`uv run aletheia gateway status` |
| Exchange、启用市场、paper execution mode | `/api/settings` 的 `exchange` 字段（响应已脱敏） |
| 当前仓位及 `market_type`、`leverage`、proposal 关联 | `/api/positions` |
| Demo/live Risk policy 与杠杆上限 | `/api/settings/risk-policies` |
| 当前账户杠杆计算是否可用 | `/api/risk/account-leverage` |
| 最近错误与整体诊断 | `uv run aletheia doctor --verbose`、`uv run aletheia gateway logs`（均应脱敏） |

需要快速查看交易配置与开放仓位时，用标准库筛选响应，避免把整份配置带进 Agent
上下文：

```sh
ssh -o BatchMode=yes fedora 'python3 - <<"PY"
import json
from urllib.request import urlopen

base = "http://127.0.0.1:8788"
settings = json.load(urlopen(base + "/api/settings", timeout=5))
positions = json.load(urlopen(base + "/api/positions", timeout=5))
risk = json.load(urlopen(base + "/api/settings/risk-policies", timeout=5))

print("exchange", json.dumps(settings.get("exchange", {}), sort_keys=True))
print("risk", json.dumps(risk, sort_keys=True))
for row in positions:
    if row.get("status") == "open":
        keys = ("id", "symbol", "market_type", "direction", "leverage",
                "execution_mode", "proposal_id", "opportunity_id", "opened_at")
        print("position", json.dumps({key: row.get(key) for key in keys}, sort_keys=True))
PY'
```

仓位成因不能只看 Open Positions 表。需要解释“为什么选择这种 market/leverage”时，沿
`position.proposal_id → trade_proposals → pi_decisions` 和
`position.opportunity_id → opportunities` 查询；区分 AI 请求值、Risk cap 裁剪、来源
Opportunity 的 instrument identity 和最终 Position。优先使用 repository/API 的只读方法，
不要手写数据库更新。任何数量、commit、配置值和健康状态都是运行事实，必须现场重查，
不得把一次快照写成 tracked 文档里的“当前状态”。

只读诊断不授权 `gateway start|stop|restart`、`update apply`、配置写入、Risk 修改、数据库
修复或下单。连接失败时先报告 `SSH alias`、网络或 Gateway readiness 的哪一层不可用；不要
在 macOS 启动替代实例，也不要为了取得答案重启 Fedora Gateway。

## Fedora 运行实例：精确部署 SOP

Fedora 是当前长期运行实例；macOS 只用于开发和控制。合并 Ready PR 后，先确认并记录
owner 选定的 `origin/master` HEAD（通常就是该 PR 的 merge SHA），再从 Mac 执行现有的一键更新入口：

```sh
ssh fedora 'cd /home/admin/aletheia && uv run aletheia update apply --yes --restart'
```

这个命令已经负责 fast-forward、依赖同步、前端构建、Migration/SQLite 校验、Gateway
重启和 readiness；不要再包一层部署脚本。完成后只做以下验收：

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

## 数据库迁移边界

- 每个 Migration 使用稳定 ID、不可变 checksum，并在 `aletheia_schema_migrations` 中留下成功账本；同一 ID 不会因重启而重跑。
- `gateway start/restart` 在创建 supervisor/frontdoor/backend **之前**执行一次 pending-migration 检查和必要升级；完成后才启动 15 秒 readiness 计时，因此大型首次升级不会伪装成 Gateway readiness 超时。
- supervisor 的 Runtime 读取、frontdoor/backend 启动上下文、FastAPI lifespan、API 路由和 scheduler 都只有只读验证权限，不能触发升级。
- 新空库或没有账本的旧库会执行一次冻结的 legacy baseline。checksum 漂移、数据库含当前代码未知的 migration ID、或升级异常都会 fail closed，并保留明确的 migrate/doctor/log 指针。
- Schema v3 会在升级阶段一次性重建 `knowledge_fts` 并建立启动/遥测热路径索引；后续普通 Gateway 启动不会再全量 reindex。需要人工修复 FTS 时使用显式 `uv run aletheia knowledge reindex`。
- pending migration 执行时 CLI 显示步骤进度、精确 elapsed 和宽范围 ETA。ETA 会标明估算依据；超过范围时只显示仍在执行，不伪造完成百分比。重定向输出仍保留最终步骤和实际耗时。
- 受控 `update apply` 会在激活候选版本时执行同一 migration apply/SQLite validation；普通重启在账本已 current 时仅有毫秒级核对。

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

生产更新优先使用一键受控流程；它仅在 `frontend/` 有变动时构建静态产物，完成 smoke check 后才重启：

```powershell
uv run aletheia update apply --yes --restart
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

1. `uv run aletheia update apply --yes --restart`（拉取、按需安装依赖、构建前端、显式迁移/验证并重启）
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
