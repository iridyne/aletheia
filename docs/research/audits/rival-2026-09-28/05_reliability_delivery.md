# 05 · 可靠性与交付

> 口径：部署形态、release 序列、**失败语义（读不到数据时返回 0 还是 unavailable）**、静默失败计数、重试/超时/幂等、凭证处理、可观测性、kill switch / fail-closed、真实运行证据。

## 横向总表

| 维度 | Aletheia | R20/AstraQuant | OpenAlice | Vibe-Trading | nofx | tickflow |
|---|---|---|---|---|---|---|
| 部署形态 | systemd user unit（**代码生成**，`session_service.py:591`）+ PyInstaller + `install-release.sh` | Docker+compose+entrypoint+watchdog | 安装器（bash+ps1）+ Electron + guardian 守护 | Docker+compose+Electron（**非官方**） | Docker+compose(prod/stable)+Railway 一键+install.sh | Docker+PyInstaller+Inno Setup+dev 脚本 |
| Docker | 无（**自用系统，不计为差距**） | ✅ | 仅 3 个用于**测安装器**的 Dockerfile | ✅ | ✅ | ✅ |
| release tag | 19（v0.8.6，release-please，`workflow_dispatch` 攒批发布） | v8.4.0（20+ tag） | **88**（v0.94.1，含 beta 通道，2.4 天/个） | 12（全在 v0.1.x，**无 1.0**） | **0**（workflow 监听 `tags:['v*']` 但从未打过） | 49（v0.3.1） |
| 启动前置检查 | **✅ 有对等物**：`preflight.py` + `assert_preflight_or_exit`（`lifecycle.py:137,150`）+ `assert_migrations_current()`（`server/app.py:479`） | `docker-entrypoint.sh:26-37,53` | launch preflight | — | `api/launch_preflight.go` | — |
| **进程卡死可被发现** | **❌ 唯一缺口**：unit 是 `Type=notify` 但**无 `WatchdogSec`**，`READY=1` 只在启动发一次（#3612） | ✅ 心跳+`gateway_liveness.py` 四态退出码 | guardian `restart-backoff` | — | safe mode（AI 失败计数，非循环卡死） | ✅ `HealthWatchdog` 探测悬死锁 |
| 失败语义 | `net → None` 而非 0（`outcome_economics.py:95`） | `FAILURE_SEMANTICS.md` 28 行总表 + 锚点门 | **266 处 `unavailable` 判别联合**（带 reason 枚举） | fail-closed 显式标注 + 测试固化 | 三态 `ok/low/empty/unknown`，"RPC 不可达 ≠ 余额 0" | 多处显式 fail-closed，检测可疑数据返回 `null` 不编造 |
| 静默失败 | 42 处 except-pass（baseline 冻结，164→42）；**281 处真静默 broad handler 无门禁**（#3613） | — | **空 catch 仅 5 处 / 42 万行**；py `except: pass` **0** | 142 处 except-pass；919 处 `except Exception`；裸 `except:` 0 | `_ = err` **0**；`_, _ :=` **0** | 74 处 except-pass（**全为窄类型**）；539 处 `except Exception` |
| 凭证 | — | tag 硬编码用户不可见 | **AES-256-GCM sealed envelope + 0600 + 损坏隔离保留原文件** | Electron `safeStorage` allowlist（**仅 LLM 密钥，无 broker**） | `crypto.EncryptedString` RSA-4096+AES-256-GCM；**读路径 fail-open 到密文** | `secrets_store.py` 0600 权限文件 |
| kill switch | live-unlock + Risk Engine + Hard Gate | 5 拦截器 + 熔断孪生对拍门 | **7 层纵深**，默认全部指向更安全 | `live/halt.py` + 测试固化 | safe mode（AI 连续失败 3 次熔断） | `HealthWatchdog` 从真实事故反推探针 |
| 真实运行证据 | **❌ #2899 仍 open**，本机 positions/orders/fills 全 0 | 自述"实盘周期零异常"；本次见真实 gateway 事件（08:31 开仓 SUI） | `logs/workspace-sessions.log` 入库 + `docs/uta-live-testing.md` 22KB | **无**运行日志/收益矩阵；e2e 在 CI 永久 ignore | 4154 cycles 回放（非实盘业绩） | commit 历史含真实运维复盘 |

## 失败语义：这是六仓差异最大、也最值得抄的一域

交易系统最危险的 bug 是**"读不到数据被当成 0"** —— 空仓位与取数失败不可区分，会让风控基于伪造的中性状态决策。

### OpenAlice —— 判别联合 + reason 枚举（最成熟）

266 处 `unavailable` 命中（剔除 spec/vendored/protobuf），核心模式：

```ts
// src/server/cli.ts:79-81
const resolveWs = (wsId: string): { meta: WsMeta } | { error: 'unavailable' | 'unknown' } => {
  if (!svc) return { error: 'unavailable' }
```

`unavailable`（服务未就绪）与 `unknown`（对象不存在）是**不同错误码**，且用判别联合返回，调用方必须显式处理。

带 reason 的结构（`src/workspaces/conversation-control.ts:124-166`）：

```ts
return { mode: 'unavailable', reason: 'missing-session', ... }
return { mode: 'unavailable', reason: 'retired-session', ... }
return { mode: 'unavailable', reason: 'deleted-session', ... }
```

**类型级保证**（`src/core/workspace-tool-center.ts:141-145`）：用 TS `Exclude`/`Extract` 把"可用"与"不可用"两个分支在类型层面拆开，让不可用状态**无法被误用为可用**。

调度器遇到不可用工作区**直接抛错**（`src/workspaces/schedule/scanner.ts:482`），不跳过不返空。

错误阶段 → HTTP 状态码是显式映射表（`routes-trading.ts:63` `const PHASE_STATUS: Record<OrderEntryPhase, 400 | 500>`）—— stage 失败 400、commit/push 失败 500。

### Aletheia —— 同一直觉，但覆盖面不足

`core/aletheia/universe/outcome_economics.py:40-45` 的 docstring（逐字实测）：

> "Net is deliberately unavailable when the direction is unknown, a derivative has no point-in-time funding rate, or the settlement price is too far from the requested horizon. **Unknown economic inputs must not become optimistic zeros.**"

实现（`:95-102`）：`net: float | None = None`，三个前提全满足才赋值。

**这是与 OpenAlice 同级的设计**，但 Aletheia 有 **1454 处 `except Exception`**（`core` + `extensions`），说明这个纪律没有覆盖全局。`tests/audit/test_no_silent_except_pass.py` 已经把 except-pass 从 164 压到 42 并冻结棘轮，但 `except Exception` 这一大类尚无同等门禁。

### nofx —— 三态健康 + "RPC 不可达 ≠ 余额 0"

`trader/runtime_health.go:12-17` 把 AI 钱包余额分为 `ok`/`low`/`empty`/`unknown`；`:59-66` 的 `markAIWalletHealthUnknown` 专门标注 **RPC 不可达 ≠ 余额为 0**。

候选币为空时记为 `Success: true`（`trader/auto_trader_loop.go:87-89`，注释 "Not an error, just no candidate coins"）—— 正确区分"无数据"与"错误"。

### tickflow —— 检测到可疑数据返回 null，不编造替代值

`backend/app/plugins/stocksdk/provider.py:211-232`：**"把日级常量的伪分钟 open 置 null（fail-closed，不伪造 prev_close 替代）"**。

`backend/app/factors/dsl.py:559`："编译公式文本；永不抛异常，失败以 errors 表达 (fail-closed)"。

### 反面样本：tickflow-ashare-akshare 的"修了一半"

commit `7d22faf` "Return empty result for unavailable daily bars" 的实际行为（`src/collector.py:198-228`）：

- 回退源也空时走 `sources[symbol] = "Not available for requested range"; continue`（`:214-219`）—— symbol 不进 `rows`，但 `sources` 记了不可用状态
- `if not rows: if sources: return [], sources`（`:225-226`）—— 返回空列表 + sources 字典，不抛 502
- **但 `/v1/daily` 端点只取 `_load_many(...)[0]`（`:245`），丢弃了 `sources`**

结果：下游拿到的空 `data` 数组**与"真的无行情"在 HTTP 层完全无法区分**。避免了误报 502，却引入了新歧义；`sources` 本可解决但被端点层丢弃。

**这正是 Aletheia 反复踩的那类坑的同构形态**（`all_realized_pnl_unavailable`、derivatives 恒 0）——**区分原因的元数据在传输层被截断**。

## 凭证与私钥：三种做法，一个不对称缺陷

### OpenAlice —— 四件套（加密 + 权限 + 损坏可恢复 + 原文件保留）

`src/core/config.ts:705` 注释 `Write the accounts file sealed (AES-256-GCM envelope, see sealing.ts)`。

`src/core/config-accounts.spec.ts` 的 4 个测试名即规格：
- `:44` `it('write → sealed envelope on disk (0600), read → original records')` —— **0600 权限被断言**
- `:79` `it('quarantines an unreadable sealed store and recovers with an empty one')`
- `:94` `expect(files.some((f) => f.startsWith('accounts.json.sealed-unreadable-'))).toBe(true)` —— **原文件保留为隔离副本**

解密失败**不静默重置用户账户**（那会是灾难），而是保留原文件让人工介入。

**keyless 隐式降权**：`UnifiedTradingAccount.ts:146` `this.readOnly = options.readOnly ?? options.keyless ?? false` —— 公开数据源账号不能被误当交易账号。

### nofx —— 透明加解密类型，但读写语义不对称（缺陷）

交易所密钥用 GORM 自定义类型 `crypto.EncryptedString`（`store/exchange.go:28-40`），RSA-4096 + AES-256-GCM，`storagePrefix = "ENC:v1:"`。

- **写路径 fail-closed**：`crypto/crypto.go:461-467` 加密失败直接返回 error 中止写入，注释 "never silently persist a plaintext secret"
- **读路径 fail-open**：`crypto/crypto.go:438-441` 解密失败时 `*es = EncryptedString(str)`，**把密文原样当明文返回**，不中断不报错

**这个不对称是真实缺陷**：加密服务配置错误时，读出来的是乱码密文而不是明确错误，会静默向下游传播。

链上私钥做得好：`trader/hyperliquid/trader.go:126-129` 主动比对 `walletAddr` 与从私钥派生的 `agentAddr`，**检测用户误用主钱包私钥并打印高危警告**；`:118-124` walletAddr 为空时拒绝启动。仅是警告级别不是硬拒绝。

### Vibe-Trading —— allowlist 不含 broker 密钥（旧结论复核成立）

`desktop/electron/src/secure-credentials.ts:12-32` 的 `ENV_CREDENTIALS` 实际内容：全部是 `*_API_KEY`（OPENROUTER/OPENAI/ANTHROPIC/DEEPSEEK/... 共 19 个 LLM provider）+ 1 个数据源 `QVERIS_API_KEY`。

**无任何 BINANCE/OKX/ALPACA/IBKR/TIGER/FUTU 密钥。** `:134` `if (!ENV_CREDENTIALS.has(name))` 是写入门禁。

服务器端凭证靠门禁 (e) 强制所有 env 读取集中到 `agent/src/config/`。

### R20 —— 经纪商 tag 硬编码且用户不可见（结构性事实）

`scripts/okx_rest.py:49`（本人独立复核）：

```python
DEFAULT_OKX_BROKER_TAG = "6e2191f027c6SUDE"
```

`:57-63` `effective_broker_tag()` **完全忽略环境变量**（docstring 仍写"优先级：实参 > `OKX_BROKER_TAG` > 默认值"，是收紧前的残留）。`ee7fa14a` commit body 原话：**"所有用户都看不见这个 code，避免有人特意改掉（改掉=返佣没了）"**。

注入点 `_with_broker_tag()`（`:66-80`），覆盖下单（`:315`）、策略委托（`:536`）、平仓（`:411`）三个产单端点。

**为什么这一节属于"可靠性"而不只是商业**：`scripts/okx_rest.py:69-71` 记录了真事故——

> "2026-09 起抽成单一出口：原先这段在两处各写一遍，于是新增的 `close_position` 漏了 —— 而每一笔 OKX 市价全平都走它（**实测 34 次整仓退出**），那些成交就没带标记。"

对应的门禁是 **AST 判据**（`tests/venues/test_okx_rest.py:415-418`）：`ast.unparse(fn)` 必须含 `_with_broker_tag`，且 `:418` 反向断言"产单端点实现函数变了 ⇒ 新端点必须一并纳入本门"。另有"任何生产模块不得自拼产单请求体"（`:457-491`，白名单只有 `okx_rest.py`）。

**这个形态可以直接搬**：Aletheia 的 Execution Gateway 唯一入口就是同一类不变量——"不得绕过 Gateway 自拼订单"。R20 把它做成了 AST 门 + 白名单。

## kill switch / fail-closed 的强度排序

### 第一名：OpenAlice 的 7 层纵深

1. **交易模式 env 锁**：`packages/guardian-runtime/src/trading-mode.ts:5` `'lite' | 'readonly' | 'pro'`；`:20` 白名单校验；`:33` 无 UTA 配置 → `lite`（最低权限）。测试断言 env 优先级最高（`trading-mode.spec.ts:26`）。`OPENALICE_TRADING_MODE=readonly` 是全局 kill switch。
2. **AI 交易默认关闭**：`src/core/config.ts:249` `allowAiTrading: z.boolean().default(false)`；门禁在 `src/tool/trading.ts:824` 的 **tool execute 内部早返回**，不是 UI 隐藏 —— prompt injection 诱导 `tradingPush` 也只返回"请让用户去 Web UI 审批"。
3. **live 测试硬门**：`scripts/test-lanes.mjs:450-457` 在 **config 加载时**抛错（未设 `OPENALICE_UTA_LIVE_PAPER=1` 连测试收集都跑不起来）。
4. **账户级 loud-refuse**：`UnifiedTradingAccount.ts:557-563` `_assertCanMutateAccount` 抛 `BrokerError`；`:549-555` keyless 连提案都不许建。
5. **dispatch 前数量复检**：`:565-572` `_assertCloseQuantityWithinPosition`，注释明说防**"超大反向单穿过 flat 建立新敞口"**——这是真正懂交易的人才会写的检查。
6. **CAS 防误批**：push/reject 必须带 `expectedPendingHash`，缺失 409 `PENDING_HASH_REQUIRED`（`routes-trading.ts:510-515`），不匹配 409 `PENDING_HASH_CONFLICT`（`:519-524`）。
7. **simulator 拒绝真实券商**：`routes-simulator.ts:88` 非 MockBroker 直接 400。

**每层默认值都指向更安全**（false / lite / readonly / 抛错 / 拒绝）。一个默认安装的 OpenAlice **无法自主下单**，无论 AI 被怎样诱导。

### 第二名：R20 的容器自监管（新增，Aletheia 可直接用）

`e506da2e` 解决的失败模式被命名为 **"一切正常，就是不下单"**（commit body）：

> "Docker 的 `restart:` 策略只对**退出**生效。'进程还在、循环不转'这种死法 Docker 不会重启。**网关尤其危险：后端容器照常健康（看板能开），调度却已死。**"

机制：

- **存活心跳**：`astra_gateway/worker.py:38` `HEARTBEAT_FILE`；`:148` **抢到锁后立刻写一次**（否则看护进程读到上一代旧时间戳，把刚起来的 worker 误判卡死而杀掉）；写入失败只记一次日志不抛异常
- **判据独立成模块**：`scripts/gateway_liveness.py`（121 行），理由是"要不要杀掉一个正在管钱的进程值得能逐状态测，故判据不进 shell：**退出码即语义**"——`EXIT_HEALTHY=0` / `EXIT_NO_PROCESS=1` / `EXIT_STALE=2` / `EXIT_NO_HEARTBEAT=3`（`:57-60`）
- **时钟回拨处理**：`:86-87` `return max(0, int(time.time()) - int(raw))`，注释"NTP 校正时 age 会是负数 —— 视为新鲜，不要因此去杀一个健康的 worker"

**判据选择的理由值得单独记**：用"循环心跳"而不是"周期产物够不够新"，因为**用户可以合法关停交易**，那时账本本就不更新，拿它当判据会误杀。

- **`.env` 挂成目录时 fail-closed 拒启动**：`deploy/docker-entrypoint.sh:26-37`（`419d1185` 修的是"静默跑成半个程序"）
- **改名迁移前置检查接进启动路径**：`deploy/docker-entrypoint.sh:53` `python3 scripts/migrate_r20_to_astra.py --check || exit 1`。理由（`2c6a183d` body）：**"硬切后拿空台账悄悄启动是这类事故里最难排查的一种——不报错，只是持仓台账空的、三所全部 NOT READY。"**

### 第三名：tickflow 的 HealthWatchdog（从真实事故反推探针）

`backend/app/watchdog.py:31-40` `default_probe()` 周期探测 **Polars collect 闸 + 仓库写锁是否被悬死线程占住** —— 代码注释直接写"正是 2026-09-07 冻结事故中被毒化的两条路径"。连续失败达阈值后调用 `exit_cb` 退出进程，交给外部进程管理器重启。

**这是 fail-fast 而非防御性空写**：探针目标不是泛泛的 health check，而是"哪条锁路径曾经真的把全站冻死"。

### nofx 的 safe mode

`consecutiveAIFailures >= 3` 触发 safe mode（`trader/auto_trader_loop.go:178-179`），safe mode 下跳过本轮而非崩溃（`:207-210`）。`safe/go.go:16-33` 的 `safe.Go` 包装 goroutine，defer+recover 记堆栈但不让 panic 拖垮进程。

## 交付形态：分发能力对 Aletheia 不构成短板（范围修正）

> **2026-09-28 Owner 裁定**：Aletheia 是**自用系统，不面向外部客户**。因此 Docker、release tag 节奏、安装器、桌面分发这些"分发能力"维度**不作为差距项**。本节保留事实表供参考，但不产生行动项。
> 真正需要关注的是**同一台机器上的 runtime 行为**：进程卡死能不能被发现、配置缺失能不能拒绝启动、失败能不能被区分。这些在下一节与 issue 清单里。

| 仓库 | 一键起 | 桌面分发 | 远程运维 |
|---|---|---|---|
| **Aletheia** | 不适用（自用） | PyInstaller（`packaging/pyinstaller/aletheia-core.spec`） | Fedora systemd user unit（**代码生成**，见下）+ `scripts/runtime/install-release.sh` |
| R20 | `docker compose up` | — | watchdog + entrypoint 契约 |
| nofx | `curl \| bash`（`install.sh`）+ **Railway 一键** | — | nginx + compose prod/stable 双通道 |
| tickflow | `docker compose up` | **PyInstaller + Inno Setup（Windows 安装包）+ icns** | `dev.sh`/`dev.ps1` |
| Vibe-Trading | `docker compose up` | Electron（**Unofficial Community Build**，已进 `package.json` name） | — |
| OpenAlice | `install`/`install.ps1`（独立域名 `download.openalice.ai`） | Electron 多平台（3 个 release workflow） | **`scripts/remote-ssh-smoke.mjs` 35.7KB + `docs/remote-access.md` 48.2KB** |

**注意 OpenAlice 也没有 Docker**（3 个 Dockerfile 全部用于测试安装器本身），而它的交付纪律是六家最强的。**"有没有容器"与"交付是否可靠"无关**——这条印证了上面的范围修正。

### 两处需要撤回的初稿判断（本人复核后修正）

初稿把两件事写成了缺陷，逐个查证后**都不成立**：

| 初稿说法 | 复核结果 |
|---|---|
| "主 Gateway 的 systemd unit 不在仓库里，是手工装的，仓库不是真相源" | **错。** unit 由代码生成：`core/aletheia/gateway/session_service.py:591-602` `_build_systemd_unit()`，含 `Type=notify` / `Restart=on-failure` / `RestartSec=60` / `TimeoutStartSec=120` / 按机器内存缩放的 `MemoryMax`+`MemoryHigh`（`:563-588`）；`_systemd_unit_valid()`（`:605`）还会检测已安装 unit 的漂移。生成器在版本控制里，**比静态模板更强**。`aletheia-auto-update.service` 同理（`ops/update/auto.py:956`）。 |
| "启动时缺少前置检查，可能拿空库/缺配置悄悄跑起来" | **错。** `core/aletheia/gateway/preflight.py` 已有 `check_static_files`/`check_database`/`check_config` + `assert_preflight_or_exit`（`:245`），并在 `lifecycle.py:137,150` 真正接进启动路径；数据库 schema 另有 `assert_migrations_current()` 在 `server/app.py:479` 硬拦（`migrations.py:9024`，pending 即抛 `PendingMigrationsError`）。**R20 `docker-entrypoint.sh:53` 那条"启动前证明迁移完成"Aletheia 已经有对等物。** |

**方法论教训**：这两条都是"按竞品有什么反推我们缺什么"得出的错误结论。正确顺序是先在本仓找对等物，再判断是否缺失。已写入 99 域的未验证项纪律。

## 真实运行证据：只有三家有

| 仓库 | 证据 | 强度 |
|---|---|---|
| R20 | 本次审计期间 `astra_gateway.db` 有真实事件（08:00 每日简报、**08:31 真实开仓 SUI**、04:16 ADA 空单） | **最强**（第三方审计意外验证到） |
| OpenAlice | `logs/workspace-sessions.log` 入库；`docs/uta-live-testing.md` 22KB 操作手册；`release-cli-benchmark.yml` | 中 |
| nofx | 4154 cycles 决策回放写进生产常量注释 | 中（回放非实盘业绩） |
| tickflow | commit 历史含真实运维复盘（2026-09-07 冻结事故 → watchdog 探针） | 中 |
| Vibe-Trading | **无**运行日志、无收益矩阵、无 uptime；e2e 在 CI 永久 ignore | 弱 |
| **Aletheia** | **无**。本机 `positions`/`orders`/`fills` 全 0 行；#2899 仍 open | **最弱** |

## 对 Aletheia 的可提取项（已收窄到 runtime，已开 issue）

范围：**只保留同机 runtime 行为**。分发相关（Docker/release/安装器）按 Owner 裁定移出。

| # | 来源 | 提取什么 | 状态 |
|---|---|---|---|
| 1 | R20 `scripts/gateway_liveness.py` + `astra_gateway/worker.py:148` | **卡死可被服务管理器发现**。Aletheia 的 unit 是 `Type=notify` 但无 `WatchdogSec`，`READY=1` 只在启动发一次（`_gateway_runner.py:791,802-827`）→ `Restart=on-failure` 只能在**进程退出**时生效，"进程活着、循环不转"永远不重启。判据必须用**循环推进**而非产物新鲜度（用户可合法停机） | **已开 [#3612](https://github.com/iridyne/aletheia/issues/3612)** |
| 2 | OpenAlice 266 处 `unavailable` + R20 `FAILURE_SEMANTICS.md` | **静默宽异常门禁**。现有 `test_no_silent_except_pass.py` 只管 body 字面为 `pass` 的 42 处；`except Exception: return <中性值>` 这一形态无门禁，实测 **281 处真静默**，其中 `run_logger.py:176-188` 把未知的 agent-run 成本渲染成 `event_count: 0` / `known_cost_subtotal_usd: 0.0` —— 正是本仓 `outcome_economics.py:40-45` 明令禁止的"未知变乐观零" | **已开 [#3613](https://github.com/iridyne/aletheia/issues/3613)** |
| 3 | OpenAlice `config-accounts.spec.ts:79,94` | **凭证损坏时隔离并保留原文件**（`*.sealed-unreadable-<ts>`），绝不静默重置用户账户 | 未开（需先确认 Aletheia 凭证存储的当前形态） |
| 4 | R20 `tests/venues/test_okx_rest.py:415-418,457-491` | **AST 门 + 白名单强制"唯一出口"**。Aletheia 的等价不变量是"不得绕过 Execution Gateway 自拼订单"，且**新端点必须自动纳入门禁**（R20 的 `:418` 反向断言正是这个） | 未开（与 #3613 的门禁机制同源，可合并考虑） |

**已确认不需要做的**（初稿列过，复核后撤回）：

- ~~启动前置检查~~ —— 已有 `preflight.py` + `assert_preflight_or_exit`（`lifecycle.py:137,150`）+ `assert_migrations_current()`（`server/app.py:479`），与 R20 `docker-entrypoint.sh:53` 对等。
- ~~把 Gateway unit 提交进仓库~~ —— unit 由 `session_service.py:591` 代码生成，且 `_systemd_unit_valid()`（`:605`）检测已安装 unit 漂移，比静态模板更强。
- ~~Docker / release 节奏~~ —— 自用系统，不适用。

## 必须点明的幻象

1. **nofx 的 `ENCRYPTION_README.md` 描述的部署流程是幻象**：文档列出的 `deploy_encryption.sh`、`scripts/migrate_encryption.go` 在当前 HEAD **不存在**。加密机制本身是真的，"一键部署脚本"是文档幻象。
2. **nofx 的 Docker 镜像编译了 TA-Lib C 库但代码零 cgo 绑定**：`docker/Dockerfile.backend:4-5,16-34` 专起 multi-stage 编译 TA-Lib 并设 `CGO_ENABLED=1`（`:50`），但 `grep -rln 'import "C"'` **无匹配**，所有指标是纯 Go 实现（`market/data_indicators.go`）。**构建产物膨胀无收益。**
3. **nofx 的 `crypto.EncryptedString` 读路径 fail-open**：写入严格 fail-closed（"never silently persist a plaintext secret"），但 `Scan()` 解密失败把密文当明文返回（`crypto/crypto.go:438-441`）。**读写语义不对称容易被忽略。**
4. **R20 的 `scripts/okx_rest.py:73` docstring 声称支持环境变量覆盖 broker tag，实现完全忽略**（`:57-63`）。文档是收紧前的残留。
5. **Vibe-Trading 的 Electron 桌面版是 Unofficial Community Build**，且已从 README 措辞升级为**包标识**（`package.json:2` name=`vibe-trading-desktop-unofficial-community`）。不要把它当官方分发能力。
6. **nofx 有 0 个 git tag**，但 `docker-build.yml:8-9` 监听 `tags:['v*']` —— 发布流程存在但这 57 个 commit 的历史里从未打过 tag。**"有 release workflow" ≠ "有发布节奏"。**
7. **tickflow-ashare-akshare 的 `pyproject.toml` 声明了 `testpaths = ["tests"]` 但仓库里不存在 `tests/` 目录** —— 测试配置存在，测试代码不存在。
