# Demo / Live Risk Policy 完全分离实施计划

> 状态：Implemented locally — 已获批准并完成本地实现与聚焦验证，尚未提交、部署或重启网关
>
> 日期：2026-07-23
>
> 分类：B 类行为变更。实施后必须关闭旧观察窗口、创建新 policy epoch，
> 不得把变更前后的仓位、拒绝率、回撤或收益结果合并比较。
>
> 当前工作区说明：经 owner 批准后，既有 Demo 高默认值被纳入 v2 Demo policy；
> 所有实现仍保留为未提交工作区改动，等待 owner 审查。

## 1. 决策摘要

将 Demo 与 Live 的**风险策略参数**拆成两份完整、独立、版本化的 policy，
允许人工执行显式的比较、复制、重置和整体替换。

系统仍然只保留：

- 一个不可绕过的 `evaluate_proposal()` Risk Engine；
- 一个 Risk approval / RiskDecision 持久化机制；
- 一条 proposal → risk decision → order → fill → position 因果链；
- 一个 Execution Gateway 下单入口；
- 一个全局紧急 Kill Switch。

Risk Engine 必须从 Proposal 已持久化、hash-covered 的 `execution_mode`
解析目标 mode，再只读取该 mode 的 policy。调用方、前端、Agent 或请求 body
不得直接指定“使用哪份 policy”，以免让 Demo proposal 借用 Live policy 或反向绕过。

目标结构：

```text
TradeProposal.execution_mode
          │
          ▼
resolve_risk_mode(execution_mode)  ── fail closed on unknown mode
          │
          ├── demo ──► RiskConfig.demo: ModeRiskPolicy
          └── live ──► RiskConfig.live: ModeRiskPolicy + LiveEligibilityPolicy
                              │
                              ▼
                  one Risk Engine / one RiskDecision chain
```

## 2. 修改边界

### 2.1 会修改

- `aletheia/config/schema.py`：新增嵌套 policy schema 和兼容迁移。
- `aletheia/config/loader.py`：一次性、确定性、可验证的旧配置迁移与备份。
- `aletheia/risk/engine.py`：集中解析 mode policy；所有 mode-sensitive 检查改读
  resolved policy。
- `aletheia/risk/kill_switch.py`：在全局紧急刹车之外引入 mode-scoped brake。
- `aletheia/portfolio/position_manager.py`：按 position execution mode 读取运行时保护参数。
- `aletheia/trading/instruments.py`：按 mode policy 读取杠杆边界。
- `aletheia/trading/sizing_policy.py`：按 Demo policy 读取累计 symbol cap。
- `aletheia/server/routes/settings.py`、`risk.py`：policy CRUD、比较、复制、替换和 brake API。
- `aletheia/cli/`、`doctor.py`、Telegram、scheduler 和 health：正确呈现三层 brake。
- Settings 与 Risk & Controls 页面：双栏 policy 编辑、diff、复制、重置和确认。
- RiskDecision provenance：持久化实际使用的 policy identity/hash。
- 设计契约、API、数据模型、实验协议和 focused specs。

### 2.2 明确不修改

- 不改变 live 默认关闭、live unlock 或 permission 语义。
- 不允许 Agent、LLM tool、scheduler 自动复制或放宽 policy。
- 不复制 credentials、signer、exchange mode、execution mode 或 live authority。
- 不允许关闭 `require_risk_check_for_all_orders`。
- 不创建第二套 Risk Engine、第二套 Execution Gateway 或第二条审计链。
- 不修改 hard-coded 输入完整性、spot short 禁止、因果 ID、市场权威、
  account-mode match、订单幂等和 fail-closed 内部错误检查。
- 不自动改写当前 `~/.aletheia/config.yaml` 中的有效风险数值。
- 不在本 worktree 重启全局 gateway。

## 3. 为什么不是简单增加 `_demo` / `_live` 后缀

继续扩展平铺字段会导致：

- 每新增一个检查都要同步 schema、route、UI、Risk Controls 和多个消费者；
- “共享还是独立”由字段命名隐含决定，容易再次出现本次漂移；
- 复制 policy 需要维护一份手工字段白名单；
- RiskDecision 无法自然记录一份完整 policy identity；
- 旧字段可能被部分消费者继续读取，形成 silent split-brain。

因此正式目标必须是嵌套、类型化 policy；平铺字段只允许作为迁移输入，
不得继续作为运行时权威。

## 4. 目标配置模型

```yaml
risk:
  schema_version: 2

  shared:
    require_risk_check_for_all_orders: true
    allowed_exchange: bitget
    symbol_filter_mode: blacklist
    symbol_list: []

  demo:
    policy_id: demo-default-v2
    revision: 1

    capital:
      base_usdt: 100
      max_position_notional_usdt: 100000
      max_per_symbol_notional_usdt: 400
      max_total_exposure_pct: 100

    loss:
      max_single_trade_risk_pct: 20
      max_daily_loss_pct: 20

    positions:
      max_open_positions: 200
      allow_short: true

    leverage:
      futures_cap: 125
      major_cap: 20
      mid_cap: 10
      alt_cap: 5
      account_cap: 10

    margin:
      warning_ratio: 0.70
      block_ratio: 0.85
      min_entry_liquidation_buffer_pct: 5
      runtime_liquidation_buffer_pct: 3

    correlation:
      enabled: true
      max_sector_concentration_pct: 60
      max_portfolio_beta: 3

    exits:
      catastrophic_stop_enabled: true
      catastrophic_stop_pct: 10
      catastrophic_stop_pct_serenity: 15
      horizon_hard_backstop_mult: 3

    auto_trip_mode_brake_on_daily_loss: false

  live:
    policy_id: live-default-v2
    revision: 1

    capital:
      base_usdt: 100
      max_position_notional_usdt: 50
      max_per_symbol_notional_usdt: 400
      max_total_exposure_pct: 5

    loss:
      max_single_trade_risk_pct: 0.25
      max_daily_loss_pct: 1

    positions:
      max_open_positions: 3
      allow_short: true

    leverage:
      futures_cap: 2
      major_cap: 3
      mid_cap: 2
      alt_cap: 1
      account_cap: 1.5

    margin:
      warning_ratio: 0.70
      block_ratio: 0.85
      min_entry_liquidation_buffer_pct: 5
      runtime_liquidation_buffer_pct: 3

    correlation:
      enabled: true
      max_sector_concentration_pct: 60
      max_portfolio_beta: 3

    exits:
      catastrophic_stop_enabled: true
      catastrophic_stop_pct: 10
      catastrophic_stop_pct_serenity: 15
      horizon_hard_backstop_mult: 3

    eligibility:
      family_min_samples: 30
      family_min_win_rate_pct: 50

    auto_trip_mode_brake_on_daily_loss: true
```

数值仅表达迁移目标的初始语义，不构成在本计划阶段批准修改当前运行配置。

## 5. 字段归属矩阵

| 现有字段/机制 | 目标归属 | 复制行为 | 说明 |
|---|---|---|---|
| `max_demo/live_position_notional_usdt` | mode `capital` | 复制 | 统一字段名 |
| `starting_capital_usdt` / `max_live_limited_cap_usdt` | mode `capital.base_usdt` | 复制 | 统一公式语义 |
| `max_per_symbol_notional_usdt` | mode `capital` | 复制 | 阈值分离，pool 计算已按 mode 分离 |
| `max_demo/live_single_trade_risk_pct` | mode `loss` | 复制 | Demo 是否强制 stop 仍是 engine invariant/规则，不能由复制隐式改变 |
| `max_daily_loss_pct` | mode `loss` | 复制 | 每个 mode 独立阈值 |
| `max_total_live_exposure_pct` | mode `capital.max_total_exposure_pct` | 复制 | Demo 新增同构字段；是否启用必须有明确 check/spec |
| `max_open_positions_demo/live` | mode `positions` | 复制 | 已按 mode pool 计数 |
| `allow_short_demo/live` | mode `positions` | 复制 | spot short hard reject 永远共享 |
| futures/tier/account leverage | mode `leverage` | 复制 | target 保存时做 venue/product compatibility 校验 |
| margin warning/block ratios | mode `margin` | 条件复制 | 当前无运行时消费者；先实现诚实语义或移除 |
| `min_liquidation_buffer_pct` | mode `margin` | 复制 | entry Risk check |
| `runtime_liquidation_buffer_pct` | mode `margin` | 复制 | position manager 按 position mode 读取 |
| correlation enabled/sector/beta | mode `correlation` | 复制 | exposure pool 已按 mode 分离 |
| catastrophic stop / horizon backstop | mode `exits` | 复制 | 必须按 position 原 execution mode 解析 |
| kill-switch auto trigger flag | mode policy | 复制 | 只能触发 target mode brake |
| live family evidence thresholds | `live.eligibility` | 不复制到 Demo | Live-only promotion evidence |
| Risk Engine safety approval | shared immutable mechanism | 永不复制/关闭 | 两个 mode 都必须生成 authoritative RiskDecision |
| human confirmation / auto-continue workflow | mode-specific approval workflow | 条件复制 | 只能控制 Risk pass 后的流程，不能授予 Live authority |
| symbol filter/list | `shared`（首期） | 不复制 | 若未来需按 mode 分离，另开设计；避免一次扩大 universe 行为 |
| `allowed_exchange` | `shared` | 不复制 | execution routing，不是 mode risk profile |
| `require_risk_check_for_all_orders` | `shared` immutable | 永不复制/编辑 | 必须恒为 true |
| global kill-switch active state | runtime global | 永不复制 | 运行状态，不是 policy |
| mode brake active state | runtime mode | 永不复制 | 运行状态，不是 policy |
| live state / permission / credentials | 原模块 | 永不复制 | authority，不是 policy |

## 6. Risk approval 与 policy identity

### 6.1 不可拆分的不变量

`evaluate_proposal()` 仍是唯一入口。Risk policy 解析必须发生在
`_evaluate_proposal_locked()` 内部，并满足：

1. mode 来自已验证的 Proposal，不来自 API 参数；
2. Demo/Live mode 使用显式 allowlist；未知 execution mode fail closed，禁止沿用当前
   `_is_live()==False` 即按 Demo 处理的默认行为；
3. authoritative decision 与 policy snapshot identity 在同一事务内确定；
4. 执行重试只能重用完全相同的 Proposal hash、quantity 和 policy identity；
5. policy 在 preflight 与 authoritative 之间变化时，authoritative 必须重新评估，
   不能复用旧批准。

### 6.2 RiskDecision 必须新增的证据

- `risk_mode`: `demo|live`
- `policy_id`
- `policy_revision`
- `policy_hash`
- 可选 `policy_snapshot_json`：只包含非秘密、规范化、实际消费的 policy。

仅记录进程级 `behavior_fingerprint` 不够：当前 fingerprint 未包含 risk policy，
而且无法证明单条 RiskDecision 使用了哪份 policy。

`policy_hash` 必须参与 authoritative idempotency key。否则在批准后放宽/收紧 policy，
执行重试可能错误复用旧 decision。

### 6.3 “Risk approval 独立”的准确边界

Risk approval 必须拆成两个概念：

1. **Safety approval**：Risk Engine 对 Proposal 做不可绕过的评估，并持久化
   authoritative RiskDecision。Demo 与 Live 都必须执行，不能配置为 false。
2. **Workflow approval**：Risk pass 后是否还需要人工确认、是否允许在既有 permission
   范围内自动继续。该流程可以按 mode 独立，但不得取代 Safety approval。

首版不借此扩大自动化：

- Demo 保留当前 paper approval/automatic paper workflow；
- Live 继续受 `live.state`、permission、显式 unlock 和现有人工控制约束；
- Demo→Live copy 不复制任何能提升 Live authority 的 workflow 字段；
- 即使两份 policy 数值完全相同，Demo approval 也不能成为 Live approval；
- copy/replace 只改变未来评估策略，不批准任何已有 Proposal。

## 7. 三层 Brake 模型

### 7.1 状态

- `global_kill_switch_active`：全局紧急刹车，阻断 Demo 和 Live。
- `demo_brake_active`：只阻断 Demo。
- `live_brake_active`：只阻断 Live。

优先级：

```text
global active > target mode brake active > target mode policy checks
```

### 7.2 自动触发

- Demo 日损越界只可触发 Demo brake。
- Live 日损越界只可触发 Live brake。
- 任一 mode 的普通风险拒绝不得自动升级为 global brake。
- global brake 只允许人工紧急操作，或另有契约明确批准的全局完整性事故。

### 7.3 释放

- 三种 brake 都需要人类、二次确认、before/after 审计。
- 释放 mode brake 不能释放 global brake。
- 释放 global brake 不能隐式释放两个 mode brake。
- Agent、Telegram 自动化和 scheduler 不得获得 release authority。

## 8. Policy 比较、复制、替换与重置

### 8.1 API 草案

```text
GET  /api/settings/risk-policies
POST /api/settings/risk-policies/compare
POST /api/settings/risk-policies/copy
PUT  /api/settings/risk-policies/{mode}
POST /api/settings/risk-policies/{mode}/reset-defaults

GET  /api/risk/brakes
POST /api/risk/brakes/{global|demo|live}
```

旧 `POST /api/settings/risk` 在迁移期只接受旧字段并映射到明确目标；
不得同时接受旧字段与新嵌套 policy。出现混合 payload 返回 409/422，
避免“哪个值获胜”不明确。

### 8.2 Copy 请求

```json
{
  "source": "demo",
  "target": "live",
  "source_revision": 7,
  "target_revision": 3,
  "copy_scope": "common_policy_fields",
  "confirm_target_replacement": true,
  "reason": "owner-approved experiment"
}
```

必须使用 optimistic concurrency：

- source 或 target revision 已变化则返回 `409 policy_revision_conflict`；
- 不允许 last-write-wins；
- validate 与 apply 之间必须再次检查 revision；
- 持久化为一次原子 config replace。

### 8.3 Copy 语义

- 复制的是规范化公共 policy value，不是 YAML 文本。
- target-specific 字段保留：`live.eligibility` 不被 Demo source 清空。
- runtime brake、global brake、live state、permission、credentials 不参与。
- target=live 时始终显示“放宽/收紧/等价” diff，并要求二次确认。
- target=demo 也显示 diff，但可使用较轻确认。
- 每次 copy 生成 target 新 revision 和新 policy hash。
- 审计记录 source/target policy identity、before/after、diff、actor、reason。
- API 响应不得伪称部署成功；只声明 config 已持久化。运行 gateway 是否已加载新配置
  由 config reload/health 的明确事实呈现。

### 8.4 Reset 语义

- Reset 只重置目标 mode policy。
- 默认模板必须带版本，如 `demo-default-v2`。
- 不能从当前代码默认值动态拼装一个不可追踪模板。
- Reset 不修改另一个 mode、brake、live authority 或账户资金。

## 9. 配置迁移

### 9.1 原则

- 迁移必须是 values-preserving，不得借迁移机会自动采用更高 Demo 默认值。
- 现有用户配置中的每个旧值必须映射到新结构。
- 共享旧值复制到 Demo 和 Live，以保持迁移前行为。
- 新安装才使用新的 mode-specific defaults。
- 迁移前创建权限受限的本地备份；不得把配置或运行事实写入 Git。
- 迁移幂等；重复启动不能增加 revision 或改变 hash。

### 9.2 旧字段映射

示例：

```text
max_daily_loss_pct
  -> demo.loss.max_daily_loss_pct
  -> live.loss.max_daily_loss_pct

max_per_symbol_notional_usdt
  -> demo.capital.max_per_symbol_notional_usdt
  -> live.capital.max_per_symbol_notional_usdt

futures_margin_warning_ratio
  -> demo.margin.warning_ratio
  -> live.margin.warning_ratio
```

已有 `_demo`/`_live` 字段各自进入对应 policy。

### 9.3 兼容期

建议只保留一个 release 的读取兼容：

- loader 接受 v1 flat config；
- 内存中只生成 v2 nested model；
- save 时只写 v2；
- 运行时代码禁止读取 legacy 字段；
- API GET 返回新结构，可附一个明确标记为 deprecated 的 flat projection；
- 下一 release 删除 legacy request support。

不建议让 Pydantic model 同时长期持有 flat 和 nested 两份权威值。

### 9.4 回滚

代码回滚前必须先使用显式 downgrade 工具把 v2 config 投影回 v1；
旧代码不能直接读取 v2 并猜值。downgrade 规则：

- mode-specific 原有字段恢复到对应 flat `_demo`/`_live`；
- v1 共享字段无法表达两份值时，默认采用更保守值：
  - 金额/持仓/杠杆/集中度上限取较小者；
  - 最小爆仓缓冲取较大者；
  - guard enabled 使用更严格的启用状态；
- downgrade 输出 diff 与数据损失警告，需要人工确认。

## 10. Margin 字段诚实性决策门

当前代码搜索显示：

- `futures_margin_warning_ratio`
- `futures_margin_force_close_ratio`

只有 config、Settings 和 Risk Controls 展示，没有实际运行时消费者。

实施前必须二选一：

### 方案 A（推荐）

实现具名 consumer：

- warning ratio 只产生可观察 warning，不冒充 block；
- block ratio 在明确的 pre-trade/account-margin check 中 fail closed；
- account margin 数据不可用时返回 stable unavailable reason；
- Demo/Live 各自读取同账户、同 mode 的权威缓存。

### 方案 B

从 active policy 和 UI 主风险边界中移除，迁到 deprecated/diagnostic，
明确显示 “not enforced”。

禁止仅把两个字段搬入嵌套 schema 并继续显示成有效保护。

## 11. 分阶段实施

### Phase 0 — 基线冻结与裁决

1. 裁决当前工作区 Demo 默认值改动。
2. 确认当前是否存在活动 experiment；关闭受影响窗口。
3. 记录 v1 config fixture、当前 API shape 和 RiskDecision fixture。
4. 新建专用 worktree/branch；不在共享 gateway worktree 开发或重启。

完成门：

- working tree scope 明确；
- v1 fixtures 可重放；
- policy epoch 名称、起点和受影响指标已预注册。

### Phase 1 — Schema 与纯 resolver（行为不变）

1. 新增 `ModeRiskPolicy`、`LiveEligibilityPolicy`、`SharedRiskInvariants`。
2. 实现 v1→v2 values-preserving migration。
3. 实现 `resolve_risk_policy(cfg, execution_mode)`。
4. 增加 policy canonical JSON、revision 和 hash。
5. 将 risk policy identity 纳入全局 `behavior_fingerprint` 的行为相关子集；
   单条 RiskDecision 仍保存更精确的 mode policy hash。
6. 此阶段 Risk Engine 尚不切换；用 parity tests 证明 v1/v2 等价。

完成门：

- 所有旧 fixture 迁移后数值逐字段等价；
- migration/reload/save/reload idempotent；
- unknown execution mode fail closed；
- 无运行行为改变。

### Phase 2 — Risk Engine 与消费者切换

1. `_evaluate_proposal_locked()` 只解析一次 target policy。
2. checks #5/#6/#7/#8/#10/#12/#13/#16/#17/#18/#19 改读 policy。
3. position manager、instruments、sizing、CLI/context 改读 resolver。
4. RiskDecision 写 policy identity/hash。
5. authoritative idempotency 纳入 policy hash。
6. 所有 legacy direct field reads 由 AST/rg gate 清零。

完成门：

- Demo/Live 每个 mode-sensitive check 都有 approved + rejected spec；
- 同 proposal 在不同 policy 下不能复用旧 authority；
- live 与 Demo exposure/reservation 不串池；
- shared hard invariants 仍不可绕过。

### Phase 3 — Mode brakes

1. 新增 global/demo/live brake state 与审计。
2. Risk Engine 按优先级读取。
3. scheduler、doctor、health、CLI、Telegram、UI 全部更新。
4. 迁移旧 `kill_switch_active=true` 为 global active，false 为三者均 false。

完成门：

- Demo breach 不阻断 Live；
- Live breach 不阻断 Demo；
- global 始终阻断两者；
- 三种 release 都有二次确认和权限测试。

### Phase 4 — API 与 UI

1. 双栏编辑器与 target-scoped save。
2. compare/diff、copy、replace、reset。
3. optimistic concurrency 和 stale-revision UX。
4. Risk & Controls 按当前 mode 显示 headroom，同时允许并排查看两份 policy。
5. 所有未执行字段明确标记 unavailable/not enforced。

完成门：

- UI 不会一次普通 Save 同时覆盖两份 policy；
- copy to Live 必须 diff + confirmation；
- API extra fields fail closed；
- frontend build 通过。

### Phase 5 — 合约、观测窗口与发布

1. 更新 Risk/API/data model/system map/agent contracts。
2. 注册 `demo_live_risk_policy/v2` policy epoch。
3. 记录 migration、copy 和 brake audit examples。
4. 在独立 backend `:8788` 测冷/热 endpoint latency。
5. 提交、PR、self-hosted CI；owner 决定 gateway restart。

完成门：

- focused specs、failure-path E2E、migration tests、frontend build 和 CI 全绿；
- PR 明确列出负面影响与回滚方式；
- 运行时未自动修改 owner risk values。

## 12. 测试与证明矩阵

### 12.1 Schema / migration

- 空配置得到 v2 defaults。
- 每种 v1 shared/demo/live 字段完整迁移。
- shared v1 值复制到两个 mode 后行为相同。
- migration 连跑两次不改变 revision/hash。
- save/reload 不丢字段。
- malformed nested/flat mixed payload fail closed。
- downgrade 选择保守值并报告信息损失。

### 12.2 Risk Engine

对 Demo/Live 分别覆盖：

- position cap；
- per-symbol cumulative cap；
- total exposure；
- open positions；
- single-trade risk；
- daily loss；
- flat/tier/account leverage；
- entry liquidation buffer；
- correlation sector/beta；
- allow short；
- policy change invalidates authoritative reuse。

每项至少有 approved、rejected、wrong-mode isolation 三条。

### 12.3 Brakes

- global/demo/live 组合真值表；
- 日损只触发目标 mode；
- release 二次确认；
- Agent/tool 无 release authority；
- restart 后状态保持；
- audit actor/source/entity 正确。

### 12.4 Copy / replace

- Demo→Live / Live→Demo common fields；
- target-only fields preserved；
- stale source/target revision 返回 409；
- failed validation 零写入；
- config 与 audit 要么同时形成可信结果，要么显式报告 audit failure；
- credentials/live state/runtime brakes 永不出现在 diff/payload；
- concurrent copy 不出现 partial merge。

### 12.5 UI / honesty

- 两份 policy 明确标识 mode、revision、hash。
- 普通 Save 只提交当前 mode。
- copy before/after diff 完整。
- Live target 明确标红所有放宽项。
- unused margin fields 不显示为 active protection。
- active mode headroom 使用相同 resolver 语义。
- config unavailable 时显示 unavailable，不回退到另一 mode。

### 12.6 性能与并发

- async route 不直接执行阻塞 config/audit I/O。
- copy/replace 在同一 config write lock 下做 revision compare + replace。
- Risk evaluation 不在事务中执行网络/I/O。
- config resolver 是纯内存、常数时间。
- 直连 `:8788` 测量 GET、validate、compare 和 replace。

## 13. 反向评估

### 13.1 安全攻击视角

**攻击：**伪造 body 指定 `policy_mode=demo` 给 Live proposal。

**结论：**API 不接受 policy mode；只能从已验证 Proposal execution mode 解析。

**攻击：**Demo→Live copy 连带关闭审批、解锁 Live 或释放 brake。

**结论：**authority/runtime 字段不属于 policy schema，Pydantic extra-forbid；
copy 使用内部规范化 allowlist。

**攻击：**先 preflight 获批，再收紧 policy，重试旧 authority。

**结论：**policy hash 进入 authoritative identity；hash 改变必须重新评估。

### 13.2 失败恢复视角

**失败：**config 已保存但 audit 写失败。

**风险：**发生无法解释的风险边界变化。

**要求：**设计阶段必须选定一致性策略。优先方案是先写 durable pending audit intent，
原子 config replace 后写 completion；doctor 对未完成 intent 报 blocker。不能声称普通 JSONL
与 YAML 天然原子。

**失败：**升级后立即回滚旧二进制。

**风险：**旧代码无法读取 nested config。

**要求：**发布包必须携带显式 downgrade 命令；无 downgrade 不允许回滚。

### 13.3 运维视角

**风险：**设置页 copy 成功，但旧 gateway 进程仍运行旧代码/旧 config cache。

**要求：**API 返回 persisted revision；health/status 返回 loaded revision。UI 只有两者一致
才能显示 “active”，否则显示 “saved, not active/unknown”。

**风险：**global/demo/live brake 让操作员误解当前阻断范围。

**要求：**所有 UI/CLI/Telegram 信息必须显示 scope，禁止只写 “Kill Switch active”。

### 13.4 产品诚实性视角

**风险：**“完全独立”被误解为 Demo 可以绕过 hard invariants。

**要求：**UI 将参数 policy 与不可配置 invariants 分区；明确 spot-short、risk approval、
causal identity、market authority 和 fail-closed checks 始终共享。

**风险：**未消费 margin 字段继续显示为 active。

**要求：**先实现消费者或降级为 not enforced。

### 13.5 实验与统计视角

**风险：**复制 policy 后仍把前后结果聚合，得到虚假改善。

**要求：**每次 replace/copy 生成新 policy revision/hash；RiskDecision、Order、Fill、
Position 保留 fingerprint；报表按 policy identity 分层。

**风险：**一次改动同时改变过多参数，无法归因。

**结论：**copy 是 owner 主动建立新 policy cohort，不应被描述为单参数实验。
若目标是科学比较，UI 应支持“只复制选中字段”，但首版 copy API 仍默认 whole common policy，
并明确标记 multi-variable intervention。

### 13.6 维护性视角

**风险：**两个引擎分叉，修复只落一边。

**结论：**否决双 Risk Engine；只允许一套 checks + 两份 data。

**风险：**resolver 被绕过，模块继续直接读 `cfg.risk.demo.foo`。

**要求：**受控模块只接收 resolved policy，增加 AST/rg fitness test 禁止 legacy/direct
mode branching 扩散。

### 13.7 数据与身份视角

**风险：**position manager 以“当前 exchange mode”选择 policy，历史 Live position
在切回 Demo UI 后使用了错误退出边界。

**要求：**所有 position/order/reservation 后续管理都按各自行上持久化的
`execution_mode` 解析，不按当前 UI 或 exchange preference。

### 13.8 人因视角

**风险：**用户把宽松 Demo policy 一键复制到 Live，未理解资金后果。

**要求：**Live target 显示逐字段 old/new、方向（wider/tighter）、估算阈值 USDT、
不可撤销影响说明和二次确认；复制不自动 unlock 或 restart。

## 14. 被否决的替代方案

### 14.1 两套独立 Risk Engine

否决：会造成检查、错误处理、审计和安全修复漂移。

### 14.2 长期保留平铺字段

否决：字段增长和复制 allowlist 会持续漂移。

### 14.3 Copy 整个 `risk` YAML

否决：会覆盖 shared invariant、live eligibility、routing 和未来新增安全字段。

### 14.4 Demo 日损触发 global kill switch

否决：与“模式完全独立”冲突，并可能因模拟亏损阻断 Live。

### 14.5 静默迁移到新高默认值

否决：会在升级时改变 owner 已配置的风险边界。

### 14.6 只改 Settings，不改 Risk Controls/CLI/doctor

否决：会产生多个互相矛盾的 owner-facing truth surface。

## 15. 已知负面影响

- 配置和 API 是破坏性 schema 变化，需要兼容期与 downgrade 工具。
- RiskDecision identity/idempotency 逻辑会变复杂。
- mode brake 增加操作认知负担和更多状态组合。
- policy copy 可能一次改变多个行为变量，降低实验归因能力。
- 更宽松 Demo policy 会增加模拟回撤、持仓数量、SQLite/行情/结算负载。
- 高 Demo 杠杆可能放大 funding、liquidation 和价格精度模型误差。
- 同一 symbol 的 Demo/Live 独立 cap 使总系统层面的合计暴露更难一眼理解。
- UI 双栏、diff 和 revision conflict 增加实现与测试成本。
- 旧脚本、CLI、文档和外部调用若依赖 flat API，需要迁移。
- 复制到 Live 即使不 unlock，也可能为未来 unlock 预置过宽 policy；必须清晰展示。
- 如果 margin consumer 同时在本项目落地，范围会进一步扩大；应单独 gate。

## 16. 实施前必须回答的决策

只有以下问题全部有明确答案后才能进入代码阶段：

1. `symbol_filter_mode/symbol_list` 首期保持 shared，是否接受？
2. margin warning/block 选择实现 consumer，还是降级为 not enforced？
3. mode brake 状态存 config，还是独立 runtime state store？
4. RiskDecision 保存完整 policy snapshot，还是只保存 hash + revision？
5. copy 首版只支持 whole common policy，还是同时支持 selected fields？
6. target=Live 的 copy 是否需要输入固定确认短语？
7. config + audit 跨 YAML/JSONL 的一致性采用 pending-intent 还是独立数据库事务账本？
8. v1 flat API 兼容保留一个 release 是否足够？
9. 当前未提交 Demo 高默认值改动是纳入新安装模板，还是在正式实现前撤回？
10. B 类新 epoch 的名称、起始部署点和不得混合的指标集合是什么？

## 17. Go / No-Go

### Go

- 上述 10 个决策全部批准；
- migration 与 downgrade 都有可执行测试方案；
- margin 字段语义已裁决；
- policy identity 进入 authoritative decision；
- copy 不包含 authority/runtime/secrets；
- failure-path E2E 范围得到确认；
- 有专用 worktree、PR 和 self-hosted CI。

### No-Go

出现任一情况即停止：

- 需要让 Agent 自动修改或复制 Live policy；
- 无法证明旧配置 values-preserving；
- policy 变化后仍可能复用旧 authoritative approval；
- copy 会改变 live unlock、permission、credentials 或 brake state；
- global/mode brake 语义在所有消费者中无法统一；
- margin 字段继续以“已生效”展示但没有消费者；
- 没有 downgrade 路径却要求支持旧代码回滚；
- 计划要求在共享 gateway/worktree 直接部署验证。

## 18. 建议结论

批准“**两份完全独立、可版本化、可比较和可人工复制的 Risk Policy，
共享一个不可绕过的 Risk Engine 与全局安全不变量**”这一方向。

不批准“复制两套 Risk Engine”或“复制整个 risk/runtime 配置”。

建议严格按 Phase 0→5 推进；不要在当前平铺字段上继续叠加局部 `_demo/_live`
修补。正式实施前先完成第 16 节决策，并把当前工作区的过渡改动单独裁决。
