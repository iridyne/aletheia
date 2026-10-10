# Execution contract: retired narrative

> **History (moved 2026-10-10).** Verbatim excerpts removed from
> `docs/design/contracts/execution_contract.md` when it was split into
> [execution](../contracts/execution_contract.md),
> [exit and close](../contracts/exit_and_close_contract.md) and
> [outcome integrity](../contracts/outcome_integrity_contract.md) contracts.
> Evidence only; these excerpts define no current behavior.

## Former header

> **角色**:定义订单从 proposal 到交易所的完整通路,及其原子性/幂等/对账保证。
> **基线**:代码为底(file:line),意图差距标 ⚠️;冲突时文档为准。
> Last updated: 2026-08-27 · 维护者:Aletheia Dev

## Former §3.2 new-exchange onboarding note

**新交易所接入方式**（e.g. Hyperliquid）：
1. 在 `config.exchanges` 的执行模式、环境和 venue 注册表中登记 `"hyperliquid_demo": "hyperliquid"`
2. 确保 `core/aletheia/universe/registry.py` 的 `sync_registry()` 已写入 `venue="hyperliquid"` 行（非 sandbox 模式），或其 CCXT profile 提供 sandbox listing

## Former §3.4 worktree preview text

The operating rules now live in
[DEVELOPMENT_PREVIEW](../../operations/development/DEVELOPMENT_PREVIEW.md#execution-gateway-refusal).

`uv run aletheia dev api --port <879x>` 是显式、前台、隔离的开发 API，
不是 gateway 的第二实例，也不得绑定主 gateway 的 `8787/8788` 端口。它只可
使用当前 Worktree 的 `data/dev-preview/<port>`。在该模式下，Execution Gateway
在写入 Proposal、claim、RiskDecision、Order 或调用 broker **之前**拒绝
`submit_proposal` 与 `execute_existing_proposal`。

该限制覆盖所有交易所模式，且不改变正常 gateway 的
风险、执行、审计或 live 行为。凭据读取为空、凭据写入被拒绝；因此 preview
只能用于界面/API 开发，不能用作交易模拟或订单验收环境。Executable claim:
`EX-B13`。

## Former §9 strategy rule engine (deleted)

## 9. 策略规则引擎（已删除）

规则引擎已于 2026-07-02 整体删除（Position C）；交易方向不来自任何策略规则，
`strategy_id` 只是可空的历史归因标签。

## Former §10.1 notes

> 当前实现不授予 market-aware evaluator 自动执行权限。action 名称是复核建议，
> 最终行为是 durable Operator wake；在 Operator 明确作出并物化决定前，Position、
> Order 和 stop 均不变化。Settings 中仍声称自动平仓/收紧的文案属于已知产品偏差
>（Capability EX-09 / GitHub #417），不能以 evaluator 单元测试判定为已支持。

> (#105) 原优先级-4 的 `ruleset_exit` 触发器已随规则引擎(Position C)删除——`_decide_exit`
> 不再消费 `ruleset_exit_position_ids`,该触发器不再是活跃退出路径,请勿再加回。

The Settings copy was corrected later; on 2026-10-10 the Market-Aware Position Review
hint states that it wakes the System Operator and never closes or mutates stops.

## Former §10.2 Bitget demo measurement narrative

- **Bitget demo 分片平仓（2026-08-22 起）**：demo 撮合引擎对大数量 market-IOC close 有单笔
  撮合迭代上限（`trade_too_many_times_cancel`、零成交、确定性复现），因此 `close_demo_position`
  按"历史 venue 撤单次数减半"的分片数量提交（每次 match-cap 类取消减半，下限为剩余量的
  1/64；成功分片不重置计数）。物化语义同步区分"订单成交完"与"仓位平完"：分片单整单成交只把
  Position 数量核减到剩余（`is_partial=1` 记账、claim 释放、下一保护周期自动发下一片），绝不提前
  关闭仍有 venue 数量的 Position；重放已落账分片是幂等 no-op；部分成交的分片单保留
  `partially_filled` claim 继续轮询。完整平仓（订单量 = 剩余量）行为不变。
- **可成交限价回退（同日扩展）**：实测该 demo 缺陷可以是 **symbol 级**的——XRPUSDT 上任意数量
  的市价单（含 5 张诊断性入场）都被零成交取消，而限价单正常成交。因此一旦某 Position 的平仓史
  出现 match-cap 取消，下一次平仓自动改为**可成交限价**：价 = 最新价 ×（对手方向 ±1%），
  GTC 挂单穿透对手价、残余量留在簿上由既有部分成交物化收敛；取价失败则回退市价。定价方向按
  venue 侧对手方向计算（UTA 客户端会把 hedge 平仓翻转到持仓反侧）。

`close_demo_position` no longer exists; the clip and the marketable-limit fallback now
live in `core/aletheia/execution/coordinator.py` (see the exit and close contract).

## Former §10.2.4 limitation note

> **Limited limitation (narrowed 2026-09-27, #3564)**: `get_order_detail_by_client_oid`
> (`core/aletheia/execution/reconciliation.py`) previously issued a scope with no
> `start_at`, so for a venue profile with no client-order lookup parameter the read was
> *always* structurally `PARTIAL` (`private_order_history_start_required`) and such
> claims could only ever reach the terminal bound. The call site now supplies a window
> derived from the order's own `created_at` (with a one-hour skew margin,
> `_ORDER_LOOKUP_START`), so the read completes for any order inside the venue's
> private history retention. What remains is a real venue limit, not a caller bug: an
> order **older than** `private_history_retention_days` still answers `PARTIAL`, and
> such claims still reach the terminal bound for that reason. After `INV-EX-1` both
> reasons are visible in `errors.log` rather than hidden behind `RuntimeError`.

The constant is now named `_ORDER_LOOKUP_SKEW`.

## Former §10.5 known-state notes

- ⚠️ **live 对账仍受 paused**:`_run_reconciliation`(live-only,`scheduler.py:822`)仍有 paused 门,本次未改(live 默认禁用,且仅做收敛检测,优先级低)。若未来 live 常开,应一并对齐本节语义。

On 2026-10-10 `_run_reconciliation` has no paused gate and is no longer live-only.

## Former §11.2 / §11.3 status lines

状态：RG-T06 / OI-B02 的语义由 UTA `VerifiedExitReport` 和 RG-T07 编排实现；consumer 不再
构造三家 concrete private client。

状态：RG-T07 / OI-B03 已实现。`portfolio.outcome_closure` 提供 verified 与 administrative 两个不可
互换的入口；ready demo disappearance candidate 只有经过 RG-T06 三态直查后才可调用。Live 仍是
alert-only，active reconciliation incident 仍先阻断收口。

## Former §12 Unified Trading Account V1 migration boundary

Issue #2299 及已批准的唯一主线切换保留 Gateway、Risk 与本地存储权威。六种外部
execution_mode 仅使用 CCXTUTAAdapter；Bitget 只有 UTA v3 一个 profile。
按 owner 接受的 #3035，Demo/Testnet mutation 不要求 SHA-bound 本地资格证书。
账户配置不证明运行或经济资格；执行权限由现有 authoritative Risk、Gateway、exact UTA
账户绑定与 durable command/reconciliation 规则共同决定。Live 仍独立受既有 Live Trust
授权约束。没有 legacy/shadow 选择器或旧 broker fallback。外部资格 verdict 必须来自正常
执行主线的真实证据；静态实现、账户读取或健康状态不证明闭环或部署完成。

The remaining current rules of that section (identity persisted before submit, Gateway as
sole business writer, UTA never writes the DB, external calls outside the writer
transaction, ambiguous mutations never retried) are requirements of the execution contract.
