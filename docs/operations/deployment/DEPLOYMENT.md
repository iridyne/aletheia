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
```

---

## 前端部署

**前端代码改动后必须重新 build**，否则用户看到旧 UI：

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

1. `git pull --ff-only`（同步最新代码）
2. 前端有改动？→ `cd frontend && npm run build && cd ..`
3. `uv run aletheia gateway restart --no-browser`
4. `uv run aletheia doctor`（确认健康）
5. 打开 `http://127.0.0.1:8787/`

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
