---
name: aletheia-fedora-runtime
description: 连接并操作 Aletheia Fedora Paper runtime（10.2.2.66）：SSH 复用传输、常驻会话、远端日志长任务、部署/update、Gateway 与 SQLite 运行时验证。
---

# Aletheia Fedora Runtime SOP

## 固定目标

- Host alias: `fedora`（`~/.ssh/config` → `10.2.2.66`, user `admin`）
- Checkout: `~/aletheia`
- Aletheia 业务诊断一律运行仓库 CLI：`uv run aletheia ...`；不要用 Orca CLI 代替业务命令。

## 读取优先级：先用 `ssh://`，再考虑 Bash+SSH

OMP 已注册主机 `fedora`（`ssh://fedora/<path>`）。**能读就不要开命令**：

| 需求 | 首选 | 说明 |
|---|---|---|
| 读文件 / 日志 / 临时结果 | `read ssh://fedora/<path>` | 单次调用、无 shell 引用地狱、≤1 MiB |
| 列目录 | `read ssh://fedora/<dir>` | 返回条目列表 |
| 写远端文件 | `write ssh://fedora/<path>` | UTF-8 |
| 跑 CLI / 查询 / 需要退出码 | `ssh fedora '<cmd>'`（复用连接）或常驻会话 | 只有执行动作才需要 Bash |

`ssh://` 不能执行命令，所以真正的动作（`aletheia inspect`、`update`、`data venue-margins`）
仍走 Bash，但应尽量合并成一次调用，并把长输出落盘后用 `ssh://` 回读。

## 传输层：连接复用（不要每条命令重新握手）

`~/.ssh/config` 的 `Host fedora` 已配置：

```
ControlMaster auto
ControlPath ~/.ssh/cm-%r@%h:%p
ControlPersist 30m
BatchMode yes
ServerAliveInterval 15
ServerAliveCountMax 4
```

因此**直接写 `ssh fedora '<command>'`**，不要再重复 `-o ServerAliveInterval=15 -o BatchMode=yes`，也不要每次手写 `timeout 280`：

- 第一条命令建 master（~0.45s），之后每条 ~0.1s，握手/鉴权只付一次。
- 网络抖动不会让每条命令各自重连。
- 验证：`ssh -O check fedora`（应输出 `Master running`）；`ssh -G fedora` 可核对生效值。
- 仍需 `timeout` 的只有**真长任务**，且应改用下面的异步模式而不是阻塞等待。

## 常驻会话：把 shell 当服务，而不是每次一条命令

短命令、连续交互、以及需要保留 cwd/环境时，起一个常驻会话，命令通过 `proc://` 发送：

```
bash(
  command="ssh -T fedora 'echo FEDORA-SHELL-READY; while IFS= read -r line; do eval \"$line\"; printf \"<<<RC=%s>>>\\n\" \"$?\"; done'",
  name="fedora-sh",
  pty=false,
  ready={"log": "FEDORA-SHELL-READY", "timeout": 20},
)
```

- 发送：`write proc://fedora-sh`，content 为**一行** shell 命令（支持 `&&` 串联）。
- 读取：`read proc://fedora-sh`；每条命令输出后跟一行 `<<<RC=<exit code>>>` 哨兵 —— 用它判断成功/失败，不要靠猜提示符。
- **多行脚本**：本地 base64 编码，远端解码执行（避免引号/$/中文被改写）：
  `echo <base64> | base64 -d | bash`
- 为什么这样比交互式 PTY 好：无提示符猜测、无控制字符、每条命令有精确退出码。

## 长任务：远端落盘 + 只读回读，绝不阻塞前台

预期 >30s 或输出量大的操作（部署、迁移、批量取数、测试）：

```sh
(echo <base64-script> | base64 -d | bash > /tmp/<name>.log 2>&1; echo "RC=$?" >> /tmp/<name>.log) &
```

- 这里用 `&` 立刻返回 pid；远端日志与 `RC=` 是唯一状态来源。
- 回读用内建 URL，不要 `cat`/`tail` 走 shell：
  `read ssh://fedora/tmp/<name>.log`
- 需要等待完成时，把**等待本身**放后台：本地 `bash(..., async=true)` 跑一个有界轮询循环（`until ssh fedora 'grep -q "^RC=" /tmp/<name>.log'; do sleep 2; done`），结果自动投递，期间继续做别的工作。
- 中断/超时只代表状态未知：先读日志、`RC=`、进程与状态文件，再决定是否续跑；禁止凭「可能没执行」重启。

## 断线恢复

看到 `Connection to 10.2.2.66 closed` / `Broken pipe` / 提示符回到本机：

1. 直接重跑 `ssh fedora true`（会重建 master）。
2. `ssh fedora 'cd ~/aletheia && pwd'` 确认。
3. 继续只读验证；不要重复已成功的 update/restart，先查远端事实。

## 标准部署流程

### 0. 部署是稀缺资源

一次 `aletheia update` 是分钟级事务，并被 settlement/Observer continuity 门禁阻断。规则：

- **不要把它当作发现问题的循环**：先把已知失败收集齐（聚焦测试 + 真实路径 smoke + runtime preflight），按可独立复核的批次合并，再部署一次。
- 门禁失败**不会改变已部署的 commit**；重试前先读 `update status` 与门禁原文，只修因再重试。
- 要判「是不是我引入的」用部署前后的源码差异，不要用「部署前后都失败」推断。

### 1. 合并前门禁

确认 PR：

- 非 Draft；base=`master`
- 精确 head 的本地确定性验证结束且无新 finding（远程 CI 已退役，不等待）
- merge state clean

合并时固定 head：

```sh
gh pr merge <PR> --rebase --match-head-commit <HEAD_SHA>
gh pr view <PR> --json state,mergedAt,mergeCommit
```

### 2. Fedora 更新前只读检查

```sh
ssh fedora 'cd ~/aletheia && git branch --show-current && git rev-parse HEAD && git status --short'
```

必须满足：branch=`master`，tracked worktree clean。随后运行紧凑状态检查，不输出整份大型 JSON：

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia control status --json' | uv run python -c \
'import json,sys; d=json.load(sys.stdin); print(json.dumps({"deployment":d.get("deployment"),"gateway_running":d.get("gateway_running"),"runtime_session":(d.get("runtime") or {}).get("runtime_session"),"safety":d.get("safety")},indent=2))'
```

### 3. 执行更新

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia update'
```

成功输出必须明确包含：

- `Updated <old> -> <merge>` 或 `Already up to date at <merge>`
- `Gateway restarted and readiness verified.`
- `Observer continuity verified`

#### 常见门禁失败与真正原因（先分类，再动手）

| 症状关键词 | 真实原因 | 处理 |
|---|---|---|
| `No space left on device` / staging 构建中断 | `TMPDIR` 指向小容量 tmpfs（如 `/run/user/*`）；staging 前端构建需要数百 MB | 让命令在具有大容量 `TMPDIR` 的 shell 中运行；不要清用户的磁盘数据 |
| `unknown migration id` / `checksum mismatch` / `schema version ... pending` | 代码树删改了一个**已被数据库应用**的 migration；账本 fail closed | 恢复该 migration 原定义（数据层不可变），不要在运行时改账本 |
| 配置加载失败 / `no such field` / validation error on settings | 退役或改名的配置字段仍留在 `settings` 表；绑定层监督制解析整份配置 | 用新的 canonical 字段写回，或让配置层忽略退役键；不要删用户数据 |
| `Observer continuity` / `continuity drift` / `settlement` 拒绝 | worker/observer checkout 漂移或结算未收敛 | 先只读确认 observe drift、worker 身份与门禁原文，再决定重建 worker；不要盲目重跑 |
| `already in progress` / 部署锁 | 另一次 update 仍持有锁 | 读 `update status`；不要并发第二次 |

### 4. 更新后身份验证

```sh
ssh fedora 'cd ~/aletheia && git rev-parse HEAD'
```

并再次运行紧凑 `control status`。必须观察：

- `running_sha == disk_sha == cached_master_sha == recorded_sha == merge commit`
- `boot_sha == merge commit`
- `verified_live_gateway == true`
- `gateway_running == true`
- kill switch false；无 pause blocker（除非任务明确预期暂停）

## Entry Planner 专用验证

部署时间记为 `DEPLOYED_AT`，查询只看该时间之后，避免旧故障污染结论。

运行时查询一律用 `aletheia inspect`，不要手写 `uv run python -c`：连接由
`readonly_db()` 以 SQLite `query_only=ON` 打开，写操作被引擎拒绝，且没有
Python 内联引号冲突问题。`--param` 传参数，`--limit` 默认 50 并显式标注截断。

### 先发现表结构，不要猜列名

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia inspect tables --like planning'
ssh fedora 'cd ~/aletheia && uv run aletheia inspect tables --table candidate_planning_generations --json'
```

### 「为什么有 Candidate 却没有 Plan」的标准三问

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia inspect query \
  "SELECT outcome_class,COUNT(*) n,MAX(created_at) last FROM candidate_planning_generations WHERE created_at >= ? GROUP BY outcome_class" \
  --param "<DEPLOYED_AT>" --limit 8'

ssh fedora 'cd ~/aletheia && uv run aletheia inspect query \
  "SELECT json_extract(result_json,\"\$.reason\") AS reason,COUNT(*) n FROM candidate_planning_generations WHERE created_at >= ? GROUP BY reason ORDER BY n DESC" \
  --param "<DEPLOYED_AT>" --limit 10'

ssh fedora 'cd ~/aletheia && uv run aletheia inspect query \
  "SELECT symbol,status,substr(result_json,1,120) AS detail,created_at FROM candidate_planning_generations ORDER BY created_at DESC" --limit 6'
```

判读要点：

- `entry_plan` 计数不等于「新编译的计划」——旧计划（无 financing 字段）在提交阶段会被
  拒（`plan_financing_incomplete` / `entry_plan_submission_leverage_unavailable`）。
  只有 `plan_json LIKE '%"execution_leverage":%'` 的才算新合同计划。
- 失败原因的主类通常是**几何门槛**而不是资金事实：`stop_distance_below_vol_target`
  表示模型给的结构止损比地板更窄；地板 = `trading.vol_target_pct`（默认 1.5%），
  已发布 regime 为 `sideways` 时 ×2（`SIDEWAYS_STOP_MULT`）。核对当前地板：
  `market_regimes` 最新一行 + `TradingConfig().vol_target_pct`。

### 新合同 Plan 是否存在（financing 冻结）

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia inspect query \
  "SELECT id,symbol,status FROM entry_plans WHERE plan_json LIKE ? ORDER BY rowid DESC" \
  --param "%\"execution_leverage\":%" --limit 5'
```

取字段时 `--json` 返回 `{columns,rows}`，要 `json.loads(rows[0]["plan_json"])` 再读
`sizing_resolution` / `leverage_resolution`。

### 结果账本 / 结算积压

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia inspect query \
  "SELECT horizon,status,COUNT(*) n,MIN(due_at) oldest FROM universe_forward_outcomes GROUP BY horizon,status"'
```

### schema 是否落后于代码

`doctor` 的 `infra.schema` 会报 pending migration 数量与 `data migrate` 修复命令。
读命令报 `no such column: ...` 时先看这一项，不要当成代码缺陷。

```sh
ssh fedora 'cd ~/aletheia && uv run aletheia doctor' 2>&1 | grep infra.schema
```

## 变更纪律（与仓库文档同源）

改动 Entry/资金路径本身时，先读仓库里的权威文本，不要在运行时现场发明规则：

- 单值单权威：新增字段前先写「谁拥有、谁只读」（`DEVELOPMENT_WORKFLOW.md` 第 7 条）。
- 已被应用过的 migration 定义不可删改（`AGENTS.md` 收敛一节 + `data_model.md` §6 + `DEPLOYMENT.md` 数据库迁移边界）。
- stub 过的接缝不算验证：CLI/事件循环/真实场馆 API 这类入口，合并前必须跑一次真实路径
  （`DEVELOPMENT_WORKFLOW.md`「Verifying a seam, not a stub」）。

## 输出与安全

- 不读取、打印或记录凭据；配置检查只输出 provider alias/model/protocol/fallback model 名称。
- 不启动 live、不改风险限额、不绕过 Risk Engine。
- 不用 `sleep` 空等前台；等待放后台（`proc://` 常驻会话或 `async=true`），期间继续做别的工作。
- 只报告实际观察：部署成功、运行 SHA、Gateway/continuity、以及部署后样本结果必须分开陈述。
