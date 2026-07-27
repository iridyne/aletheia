# 双机、多实例与 Qualification 开发协议

> 意图进 Git，运行事实不进 Git；开发可以高速变化，Qualification 必须保持静止。
>
> 本文是跨机器开发、实例运行、部署与观察窗口的权威协议。Worktree 细节见
> `GIT_WORKTREE_PLAYBOOK.md`，开发预览命令见
> `docs/operations/development/DEVELOPMENT_PREVIEW.md`，交易实验冻结规则见
> `docs/product/experiments/EXPERIMENT_PROTOCOL.md`。

## 1. 解决什么问题

Aletheia 同时存在两种节奏：

1. 开发节奏：多个 AI Agent、多个 Worktree、频繁 PR 与快速反馈；
2. 验证节奏：固定代码身份、固定策略/成本/outcome contract，连续积累可比较证据。

如果同一 Runtime 同时承担这两种职责，每次部署都会改变验证对象，旧 cohort 不能授权新
build，观察窗口会反复重置。正确做法不是降低开发速度，而是把开发与 Qualification 分到
不同实例和不同部署节奏中。

## 2. 事实与意图的边界

Git 同步的是机器无关的意图：代码、设计、规范、决策、测试和历史变更。

以下内容是每台机器的本地运行事实，不得作为“当前状态”写入 tracked 文档：

| 运行事实 | 权威来源 | 是否进 Git |
|---|---|---|
| Gateway/Preview 是否在运行 | OS 进程、`aletheia gateway status`、`doctor` | 否 |
| 当前部署 commit | Runtime provenance、`doctor`、`/_infra/status` | 否 |
| `app.db`、订单、仓位、outcome | 对应实例的 data dir | 否 |
| `config.yaml`、kill switch、permission | 对应实例的 data dir | 否 |
| 凭证与账号状态 | Keyring/加密 secrets、私有 API preflight | 否 |
| Observation window 已运行多久 | 当前实例数据库与 provenance 推导 | 否 |

Tracked 文档可以记录一次历史 checkpoint，但必须写清时间与 commit；任何“现在”的判断都
重新运行 `uv run aletheia doctor`，不得依据旧文档叙事。

## 3. 当前实现支持到哪里

### 已支持

- 每个用户有一个 primary Runtime data root：`~/.aletheia`，或绝对路径
  `ALETHEIA_DATA_DIR`；
- 稳定 Gateway 使用 `127.0.0.1:8787`，`8788` 同样保留给稳定 Runtime；
- 多个开发 Preview API 可以在同一台机器并行运行；
- Preview data dir 自动隔离为：
  `~/.aletheia/instances/dev-preview/<checkout-id>/<api-port>`；
- `checkout-id` 由 Worktree 绝对路径派生；
- Preview 强制 shadow、无 secrets、无 proposal execution、无 Gateway/update 控制权；
- 不同 Agent 的非平凡修改通过独立 Git Worktree 隔离。

### 尚未支持

当前没有一等公民的“多个完整稳定 Runtime”管理器。即使手工指定不同
`ALETHEIA_DATA_DIR`，稳定 Gateway 的 front door、supervisor、autostart、Tray、CLI 生命周期
和外部交易账号所有权仍按单一稳定 Runtime 设计。

因此当前合同是：

> 一台机器可以运行一个稳定/Qualification Runtime，加多个隔离 Preview；不得把多个手工
> 启动的完整 Gateway 描述成受支持的并行 paper/live 实例。

需要多个长期 paper 实验时，应先单独设计命名 Runtime、独立生命周期、端口、数据根、账号
scope 与 doctor 投影，不能仅靠复制目录或环境变量绕过。

## 4. 推荐双机拓扑

### MacBook Air：开发控制面

负责：

- ORCA、Claude Code、Codex 等外部 Agent 编排；
- 创建 Worktree、分支、提交、PR 与代码审查；
- 前端 HMR、短时 Preview API 和小范围 focused tests；
- 观察 Windows Runtime、读取 doctor/日志/数据库只读结果；
- macOS 专属打包与人工 release acceptance。

约束：

- 不在 Mac 上运行大规模 pytest；通过 SSH 或 CI 交给 Windows/self-hosted Runner；
- Worktree 不得 restart/stop/update 稳定 Gateway；
- 开发 checkout 不承担长期 Qualification observation window。

### Windows：Qualification 与重型验证面

负责：

- 一个固定 SHA 的稳定 `qualification-main` Runtime；
- 长时间 scheduler、paper loop、outcome settlement 与 Learning qualification；
- self-hosted CI、完整测试、stability soak；
- 必要时并行运行多个无执行权的 Preview API。

Windows 上的参考布局：

```text
qualification-main
  stable front door: 8787
  reserved backend:  8788
  data:               ~/.aletheia 或明确的绝对 ALETHEIA_DATA_DIR
  code:               固定 qualification commit
  authority:          当前明确批准的 paper/shadow 权限

dev-preview-a
  API:                 8791
  Vite:                5173
  data:                ~/.aletheia/instances/dev-preview/<checkout-a>/8791
  authority:           shadow-only；无 secrets、无 execution

dev-preview-b
  API:                 8792
  Vite:                5174
  data:                ~/.aletheia/instances/dev-preview/<checkout-b>/8792
  authority:           shadow-only；无 secrets、无 execution
```

## 5. 实例角色合同

| 角色 | 数量 | 数据根 | 代码是否可热变 | Scheduler | Secrets | Execution | 用途 |
|---|---:|---|---|---|---|---|---|
| Stable/Qualification Runtime | 每台机器最多 1 个 owner | primary data root | 否 | 是 | 可按 owner 配置 | 仅批准模式 | 长期运行与正式验收 |
| Dev Preview API | 多个 | checkout-id + API port 隔离 | 是，reload | 后台服务不启动 | Aletheia secret store 禁止 | 强制阻断 | API/UI 开发 |
| Vite Preview | 多个 | 无独立交易状态 | 是，HMR | 否 | 否 | 对稳定 API 仅只读 | 前端反馈 |
| CI/Test process | 多个临时 | tmp/test DB | 是 | 仅测试夹具 | 禁止真实凭证 | 禁止真实执行 | 回归与构建 |

### 5.1 强制边界与操作边界

“支持”不等于所有规则都已经由程序自动执行。当前边界必须按以下三类理解：

| 合同 | 当前证据 | 强制级别 |
|---|---|---|
| Preview 拒绝 `8787`、`8788` | `aletheia dev api` 参数校验 | 程序强制 |
| CLI 派生 checkout-id + API port 数据目录 | `runtime_paths.preview_instance_dir()` | 程序强制（仅标准 CLI 启动路径） |
| Preview 不使用 primary `ALETHEIA_DATA_DIR` | config loader 优先选择 Preview data dir | 程序强制 |
| Preview 不启动 scheduler/operator 等后台服务 | shadow runtime startup gate | 程序强制 |
| Proposal execution 与 paper approval 阻断 | Execution Gateway 与 approval route 的 Preview guard | 程序强制 |
| Preview API 的 Gateway/update 控制阻断 | 对应 HTTP route 的 Preview guard | 程序强制 |
| Vite 指向稳定 `:8787` 时只允许读请求 | Vite development middleware | 程序强制 |
| Worktree 不得直接运行 Gateway 生命周期 CLI | 本协议、Agent Guide 与 Worktree playbook | 操作规程；尚无通用 owner-checkout 强制器 |
| 一台主机只运行一个稳定 Runtime owner | 本协议与部署所有权 | 操作规程；尚无多 Runtime manager |
| 主机端口不冲突 | 人工/外部编排分配 | 操作规程；无动态 allocator |

因此不得把“Preview HTTP route 会拒绝 Gateway 控制”扩大解释为“任意 Worktree shell
都不可能运行 `aletheia gateway restart`”。在命名 Runtime、owner identity 和生命周期合同
没有完成设计前，不通过零散 Git/worktree 检测伪造一个不完整的 owner guard。

## 6. 不可违反的所有权规则

### 6.1 Stable Gateway 单 owner

- 每台机器、每个 primary data root 只能有一个稳定 Gateway owner；
- 只有 owner checkout/已安装 Runtime 可以执行 `gateway start|stop|restart|update`；
- Agent Worktree 和 Preview 不得控制稳定 Gateway；
- 端口 `8787`、`8788` 永远不得分配给 Preview。

### 6.2 外部交易账号单写者

同一个 `exchange account + environment + execution mode` 在任一时刻最多有一个有执行权限的
Runtime。禁止两个实例共享同一 Demo/Live 凭证并同时管理订单或仓位，否则会产生重复下单、
错误 adoption/reconciliation、跨实例平仓和无法归因的 PnL。

Preview 无权读取凭证，因此不参与该所有权竞争。

### 6.3 PR merge 与 Runtime deployment 分离

- 合并到 `master` 只表示代码进入主线，不表示 Qualification Runtime 自动升级；
- Qualification host 必须由 owner 显式选择 exact commit 并部署；
- 普通 PR 可以继续合并，但正在观察的 Runtime 保持 pinned；
- 不得让 autostart、update、Worktree hook 或 Agent 自动把 qualification host 推到最新 master。

## 7. 高速开发流程

### 7.1 开始一个非平凡任务

1. 从最新 `master` 创建独立 Worktree 与分支；
2. Prompt 明确 goal、allowed paths、excluded paths、acceptance checks；
3. 先做 focused reproduction/test，再做最小修改；
4. 需要 UI/API 反馈时启动自己的 Preview，不触碰 stable Gateway；
5. 完成 focused checks 后提交 PR；
6. PR/CI 是代码证明，`doctor` 是机器运行事实。

### 7.2 同一 Windows 主机运行多个 Preview

同一主机的每个 Preview 必须使用唯一 API port 和唯一 Vite port：

```powershell
# Worktree A，terminal 1
uv run aletheia dev api --port 8791

# Worktree A，terminal 2
.\scripts\dev.ps1 -UiPort 5173 -ApiPort 8791

# Worktree B，terminal 1
uv run aletheia dev api --port 8792

# Worktree B，terminal 2
.\scripts\dev.ps1 -UiPort 5174 -ApiPort 8792
```

规则：

- 同一主机不可复用已占用端口；
- 同一 Worktree 若同时开两个 API，也必须使用不同 API port；
- 不要手工设置 Preview 环境变量连接 primary data root；
- Preview 只证明 UI/API 合同，不证明 paper trading 行为或经济表现；
- 结束后停止对应前台进程；确认不再需要历史 Preview state 后才删除其 instance 目录。

### 7.3 前端只读模式

前端-only 任务优先只运行 Vite。默认目标 `http://127.0.0.1:8787`，Vite proxy 只允许
`GET/HEAD/OPTIONS`，写请求必须失败。需要 write-shaped API flow 时，改为指向隔离 Preview
API，不得为方便而向稳定 Runtime 发写请求。

## 8. Qualification 流程

### 8.1 建立 candidate

Owner 必须记录在本地运行事实中：

- exact code commit；
- Runtime role=`qualification`；
- data root；
- config/policy epoch；
- experiment/path allowlist；
- outcome contract 与成本模型版本；
- observation start time。

这些事实由 Runtime provenance、数据库和 doctor 提供，不创建 tracked 的“当前实例状态文件”。

### 8.2 冻结规则

Observation window 内：

- Qualification Runtime 不跟随 `master`；
- 不改变 decision input、filter/ranker、prompt、sizing、guard、Risk policy、outcome
  economics 或实验参数；
- 不升级依赖，不替换数据根，不混入其他 build 的 cohort；
- 文档/UI/其他代码可以继续在开发线上合并，但不部署到 qualification host；
- 只有阻断 Startup → Scheduler → Outcome → Verdict 的 P0/P1，或造成错误交易事实/安全
  风险的问题，才允许中断窗口并部署修复。

当前 admission 会把证据绑定到 code commit，因此即使 A-class 修复不改变经济语义，只要部署了
新 commit，也必须按 current-build identity 重新建立可授权窗口。未来若实现经过审计的 behavior
contract fingerprint，可另行放宽；当前不得自行假设等价。

### 8.3 Bug 分类与窗口影响

- **A — 立即修复**：crash/hang、错误 persistence/accounting、身份或 outcome 失真、安全
  bypass。开发线修复；只有其阻断当前 qualification 时才部署，并明确重开窗口。
- **B — 新观察窗口**：任何可能让相同市场条件下产生不同选择、仓位、guard、quota、Risk
  或 outcome 的改动。必须关闭旧 cohort，新建 policy epoch/window。
- **C — 延后**：新 Agent、页面、市场、数据源和宽泛抽象。不得打断当前 qualification。

## 9. 运行事实交接

跨机器或跨 Agent 交接时，tracked 文档只写：

- 本次代码改了什么、为什么；
- 哪些合同/测试被更新；
- 哪些风险与后续动作仍存在。

接手机器必须自行执行：

```sh
git pull --ff-only
uv sync
uv run aletheia doctor
```

不得把另一台机器的 Gateway、凭证、数据库、窗口进度或健康状态当成本机事实。

## 10. 禁止模式

- 在 shared master checkout 中同时让多个 Agent 写文件；
- 从 Worktree restart/update 稳定 Gateway；
- 因 PR 已合并就自动升级 qualification host；
- 把 Preview 指向 `~/.aletheia` primary data root；
- 用 Preview 的 shadow 数据证明正式 paper/qualification 结果；
- 两个 Runtime 同时拥有同一个外部交易账号的执行权；
- 为了并行而复制 secrets、`app.db` 或 position state；
- 把“运行第 N 天”“当前 healthy”作为无时间边界的 Git 事实；
- 以“bug free”为无限扫描目标，持续打断已经满足入口条件的 observation window。

## 11. 当前唯一推荐形态

在命名多 Runtime 管理尚未实现前，仓库支持并推荐：

```text
一个 stable/qualification Runtime
+
多个 Worktree
+
多个隔离 Dev Preview
+
多个临时 CI/Test process
```

这套形态允许高速开发与长期验证和谐共处，同时不新增仓库内任务数据库、worker lease 或实例编排
平台。Agent/session/Worktree 的临时协调继续由 ORCA 等外部编排器负责，Git/PR/CI 和 Runtime
provenance 分别承担代码事实与运行事实。

## 12. Preview 实现验收门槛

本协议定义目标拓扑，但不能替代可执行证据。任何后续 PR 若声称“远程多 Preview 已完整
可用”，至少必须证明：

1. 两个不同 Worktree 的 Preview 可在同一主机同时启动，runtime identity、API port 与
   data dir 不相同；
2. 同一 Worktree 的两个不同 API port 不共享数据库或配置；
3. Preview data dir 必须与当前 checkout identity 和 runtime port 精确匹配，不能仅凭
   “位于 dev-preview 根目录之下”就接受另一个 Preview 的目录；
4. `/api/status` 与 `/_infra/status` 明确报告 Preview/shadow 身份、真实监听端口、
   scheduler disabled 和 execution blocked，不能把默认 `Paper` 配置显示成执行权限；
5. 未构建静态前端时，API-only Preview 的探活/就绪语义仍然诚实可用；
6. stored trading credentials 不可读写，Proposal、Order、RiskDecision、Fill、Position
   不因 Preview execution 请求发生变化；
7. Gateway/update HTTP 控制仍在任何机器级副作用之前返回稳定 `403`；
8. Windows launcher、POSIX 命令和 Vite read-only proxy 都有 focused executable tests；
9. 两个 Preview 停止后不遗留稳定 Gateway PID、autostart、front-door 或 update 状态。

截至 2026-07-27 对 `0ad6157` 的复核，双 API 并行启动、独立 CLI 派生目录、shadow
后台服务禁用及 Gateway route `403` 已通过短时验证；第 3、4、5、8 项仍是后续实现/测试
缺口。因此当前可称为“受约束的开发 Preview”，不可称为“完整的多 Runtime 平台”。
