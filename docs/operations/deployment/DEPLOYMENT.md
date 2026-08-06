# Deployment Guide

> Gateway 生命周期管理 + 看门狗 + 监控。本文描述**单机本地部署**（Windows 11）。

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
```

## 数据库迁移边界

- 每个 Migration 使用稳定 ID、不可变 checksum，并在 `aletheia_schema_migrations` 中留下成功账本；同一 ID 不会因重启而重跑。
- `gateway start/restart` 在创建 supervisor/frontdoor/backend **之前**执行一次 pending-migration 检查和必要升级；完成后才启动 15 秒 readiness 计时，因此大型首次升级不会伪装成 Gateway readiness 超时。
- supervisor 的 Runtime 读取、frontdoor/backend 启动上下文、FastAPI lifespan、API 路由和 scheduler 都只有只读验证权限，不能触发升级。
- 新空库或没有账本的旧库会执行一次冻结的 legacy baseline。checksum 漂移、数据库含当前代码未知的 migration ID、或升级异常都会 fail closed，并保留明确的 migrate/doctor/log 指针。
- 受控 `update apply` 会在激活候选版本时执行同一 migration apply/SQLite validation；普通重启在账本已 current 时仅有毫秒级核对。

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
