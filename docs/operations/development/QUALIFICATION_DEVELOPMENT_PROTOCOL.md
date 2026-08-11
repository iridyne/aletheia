# 双机、多实例与 Qualification 开发协议

> 意图进 Git，运行事实不进 Git；开发可以高速变化，Qualification 必须保持静止。
>
> 本文是跨机器开发、实例运行、部署与观察窗口的权威协议。Worktree 细节见
> [`Git Worktree Playbook`](GIT_WORKTREE_PLAYBOOK.md)，开发预览命令见
> [`Development Preview`](DEVELOPMENT_PREVIEW.md)，交易实验冻结规则见
> [`Experiment Protocol`](../../product/experiments/EXPERIMENT_PROTOCOL.md)。

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
| Dev Preview API | 多个 | checkout-id + API port 隔离 | 是，reload | 可启动但不得作为交易证明 | 禁止 | 强制阻断 | API/UI 开发 |
| Vite Preview | 多个 | 无独立交易状态 | 是，HMR | 否 | 否 | 对稳定 API 仅只读 | 前端反馈 |
| CI/Test process | 多个临时 | tmp/test DB | 是 | 仅测试夹具 | 禁止真实凭证 | 禁止真实执行 | 回归与构建 |

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

#### 6.3.1 稳定实例与实验 candidate 的代码身份

- 没有正在进行的显式 Qualification 实验时，Stable Runtime 只部署受保护
  `master` 上由 owner 选定的已审查 exact SHA。“跟随 master”表示人工提升到某个
  checkpoint，不是自动追赶每一次提交。
- 未合并分支、PR head 或 composite/cherry-pick commit 只能用于显式的实验
  candidate；不得将其称为普通 Stable Runtime，也不得因分支名包含
  `runtime`、`release` 或 `qualification` 就认定已获准入。
- 部署非 `master` candidate 前，owner 必须在本地运行事实中记录：关联 PR/
  commit 集合、实验假设、允许的 paper/shadow 权限、必需 CI 状态或明确 waiver、
  observation start、成功/失败阈值与最迟结束时间。缺任一项就不部署。
- candidate 的 PR 合并、关闭、被取代或观察窗口到期后，owner 必须显式选择：
  提升对应 `master` SHA 并开启新窗口，或回滚到上一个已批准 `master` SHA。
  不得让实验分支无期限承担默认稳定服务。
- 每次切换 exact SHA 都是新的 build identity；按 §8.2 重建 Runtime/session provenance
  和 observation window，不继承旧 build 的 cohort。

### 6.4 Source checkout 的受控更新

`uv run aletheia update` 是统一的人类触发更新入口：命令本身即 owner 授权。source checkout
默认拉取配置分支的最新 GitHub HEAD、构建 dashboard、smoke 校验并重启 Gateway；packaged
runtime 自动改走签名发布与 Tray 激活链路。`update --no-restart` 仅保留 source checkout
的应急控制。该命令不是 PR merge，也不是 Qualification admission。

- 只能在 owner 的干净 source checkout 上运行，且当前分支必须等于配置的
  `update.branch`；Worktree、feature branch、detached HEAD 都必须拒绝；
- 更新仅允许 `origin/<update.branch>` 的 fast-forward。ahead、diverged 或本地 composite
  commit 不是“可更新”状态：停止 `aletheia update`，由 owner 先选择并解决 Git 身份；
- 成功输出的 commit 仍须以 Runtime 的 `boot_commit == current_commit`、Gateway status 和
  doctor/provenance 复核。一次新 build 的观察窗口从首次 eligible exposure 起算，不能把
  重启时间或旧 cohort 当作新证据；
- source 更新临时替换 dashboard static assets；只有 Gateway readiness 成功后才清理旧静态
  备份。不得把 source-checkout 临时物或 packaged-runtime rollback backup 混为一谈。

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

当前 admission 默认把证据绑定到 code commit，因此即使 A-class 修复不改变经济语义，只要部署了
新 commit，也要按 current-build identity 重新建立可授权窗口。唯一例外是受影响 measurement
adapter 已有经过审计的 behavior-contract identity，明确把 `code_commit` 仅作为 provenance，并能
证明 behavior、input lineage、outcome、metric、horizon、assignment 与 inference identity 均未改变；
当前不得由 operator 自行假设等价。

这里的“重新建立窗口”不等于“每个模块重新做 A/B”。每个窗口按
`path / hypothesis / experiment / stage` 独立登记；同一个固定 SHA 的 Qualification Runtime
可以并行采集多个互不干扰的 Shadow、paired-counterfactual 和 prospective 窗口。证据方法按
[`Experiment Protocol`](../../product/experiments/EXPERIMENT_PROTOCOL.md#21-ab-is-not-the-default-acceptance-method)
选择：完整性与会计修复用 replay/hash，存在同统计单位对照时优先 paired evidence，只有改变
未来暴露且无法构造对照的行为政策才要求 prospective A/B。

并行窗口仍共享一个执行 owner。没有预登记 factorial/interference 合同时，同一决策人群一次
只允许一个行为 treatment 声称归因；其他路径可以继续收集无 authority 的证据。不得为并行
观察手工启动第二个有执行权 Gateway、复制 primary data root，或共享同一外部账号。

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
