# [economics][budget] Hard enforcement disappears when shadow observation is off

## Why

启用 hard_block 的已耗尽、可计价预算会因 shadow=false 而完全不评估。可选观察开关不应关闭已配置的预算执行约束。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/model_provider/fallback.py:229](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/model_provider/fallback.py#L229) `_observe_would_block` 在 economics disabled、budgets_shadow_enabled=false 或 tracing disabled 时提前返回。
- 同文件 `:461` `_enforce_budget_if_configured` 将没有 observations 当作可继续；`:478` complete_with_fallback 顺序调用两者后发起推理。
- [core/aletheia/config/schema.py:2112](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/config/schema.py#L2112) 分别允许 shadow 与 enforcement_mode 配置；hard_block + shadow=false 未被禁止。
- [core/aletheia/economics/budgets.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/economics/budgets.py) 已有预算窗口查询，必须复用；本轮无 provider 调用或真实花费。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

预算计算由“是否需要 enforcement 或 shadow”决定；shadow/tracing 仅控制可选观察记录。复用现有成本估计、scope/window 和 safety caller 豁免，对可确定超限的非安全调用在任意 provider I/O 前拒绝。

明确区分未评估/不可评估与 within_budget；不要把 stale request observations 复用于新的调用。拒绝不能被 fallback 当作临时模型故障再调用另一家。现有 warn_only、fallback_to_defaults、hard_block 各自分支必须保持已批准语义。

本条授权修复确定超限情况下的 shadow/tracing 耦合；不凭空制定未知报价的新财务政策。未知成本/DB 不可用先读取现行 Contract 与既有显式处理，保留已批准映射并在结果中如实表达；若不存在确定策略而修复必须改变它，报告该具体决定，不以扩大所有未知场景政策来阻塞可独立完成的确定超限修复。关闭时必须明确未改变未知成本政策，不能宣称已解决所有预算并发/预留问题。

## Non-goals

不改 cap、时间窗口、safety caller 集合、提供商路由、预算预留算法、模型默认值、Risk 或 Live；不引入第二套计费器。

## Affected Authorities

[docs/design/contracts/llm_inference_harness_contract.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/design/contracts/llm_inference_harness_contract.md)；EconomicsConfig 字段契约与 [tests/test_economics_budget_contract.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/tests/test_economics_budget_contract.py)

## Verification — 本项验收

- 在现有 `tests/test_economics_phase2b2_hook.py`、`tests/test_economics_budgets.py`、`tests/test_llm_inference_harness.py` 中选择真实相关入口扩展；通过 complete_with_fallback 调用 provider spy。
- hard_block + shadow=false + 已耗尽且可计价预算：调用次数 0；shadow=true 同样阻断；tracing=false 仍阻断。
- warn_only + shadow=false 继续；预算未耗尽正常；显式 safety caller 保留原豁免。
- fallback_to_defaults 按当前已批准语义回归；未知报价、DB 错误和重用 request 不得伪造“已成功评估”，记录实际政策映射。
- budget denial 不触发另一 provider 请求；合成临时数据库/报价，无网络或真实计费。

## 执行授权与交付边界

Owner 于 2026-09-28 要求将已审查修复整理为可独立领取的 issue，并明确期望领取 Agent 完成修复、验证、PR 和合并到 `master`。本条记录该范围内的授权：承担本 issue 的 Agent 应完成该 D0 修复及必要测试、自审、提交、推送、review-ready PR，并在仓库适用门禁满足后合并到 master、核实实际合并结果；不需要在每个常规工程步骤重复请求批准。**Issue 本身不覆盖更高优先级约束，也不授予新的 D1/D2、部署、重启共享 Gateway、修改真实凭证、Live 解锁、历史数据重写或资格窗口重置权限。**

从领取时最新 master 创建隔离分支/工作区，先读该版本 AGENTS.md、SYSTEM_MAP 和匹配指南，核对本 issue 的函数与生产 caller。本文固定 SHA 是证据，不要求从旧 SHA 开发。若该根因已有在途 PR，复用/协调，避免两个 Agent 同时修；若当前 master 已满足，提交实际代码及验收证据说明，不制造空 PR。保留他人改动。

## 验证与合并完成条件

- 使用合成数据、临时目录/SQLite、替身 provider。不得读取真实密钥、连交易所或使用主库来复现。
- 为下文验收建立最小行为回归，证明原行为失败、修复后同一条件通过；不要只匹配源码字符串，也不要跳过或弱化失败断言。
- 首轮反馈执行 `uv run python scripts/dev_check.py`，再执行下文聚焦用例及改动所需的本地 PR 验证。具体 selector 以当前真实测试收集结果为准，零收集/skip 不算通过。不要在 macOS 跑全套；全套不是本 issue 的默认要求。
- 按 `.github/PULL_REQUEST_TEMPLATE.md` 填 PR body，执行 `uv run --no-sync python scripts/ci_preflight.py --pr-body-file <实际文件>` 和当前 `scripts/ci_local.py` 对该变更要求的本地检查；保留命令、结果、失败日志及其对应 HEAD。此处 `<实际文件>` 是领取者创建的 PR body 文件，不能原样执行。
- 远程 GitHub CI 在本次复核的 root AGENTS.md 中已退役，不能启动或等待它。遵循领取时适用规则；若分支保护仍要求不可用检查，报告具体阻塞，不绕过、不改保护、不伪造绿灯。
- 按 PR_REVIEW_LIFECYCLE 处理当前 HEAD 上的有效反馈，记录 `MERGE_READY_SHA`。合并前重新核对 PR HEAD、目标 master、测试证据及硬阻塞；若解决冲突产生新代码，重新验证受影响路径。
- 完成必须提供：修复 commit、PR URL、实际 `MERGED` 状态、merge commit SHA、更新后的 master 已包含该 merge 的证据，以及本 issue 验收结果。开启自动合并、PR 可合并、测试通过均不等于已合并。合并并核实后关闭 issue；真实门禁阻塞时保持未完成并报告，不能提前关闭。

Runtime / Economic Verification：本条交付是仓库级契约修复与合并，不要求部署或真实经济实验；明确记录 Fedora 未验证，不声称线上已修好。

<details>
<summary>历史 issue 正文（保留原始证据；旧路径、数量、建议以本次复核为准）</summary>

## Severity

`high` — an accepted `hard_block` configuration can silently execute every over-budget non-safety call because enforcement is coupled to a separately disabled shadow-observation flag.

## Pinned review baseline

- Branch: `origin/master`
- SHA: `c86e554cfe0ae6e6311d63a93479420770dcd595`

## Problem

`enforcement_mode` and `budgets_shadow_enabled` are independently valid configuration fields, but runtime enforcement has no independent budget-evaluation path. `complete_with_fallback()` invokes the optional shadow observer first; when shadow observation is disabled, no budget result is attached. `_enforce_budget_if_configured()` then interprets the missing result as no crossing and sends the call even when `enforcement_mode=hard_block` and the declared budget is exhausted.

The intended contract separates **observe-only budget evidence** from **actual enforcement authority**. Runtime incorrectly makes strict enforcement depend on the optional observation toggle, and no validator rejects or normalizes that contradictory state.

## Current code evidence

- `EconomicsConfig` independently accepts `enabled=True`, `budgets_shadow_enabled=False`, and `enforcement_mode='hard_block'`.
- `_observe_would_block()` returns before calculation when shadow evidence is disabled or unavailable.
- `_enforce_budget_if_configured()` returns when the request has no would-block observation; it does not distinguish “not evaluated” from “evaluated, within budget”.
- provider I/O therefore follows a missing observation that should have been treated as unavailable for strict enforcement.
- current tests cover shadow-off observation and hard blocking separately but do not cover the integrated `hard_block + shadow=false` failure.

## Deterministic trigger

1. Configure economics enabled, `enforcement_mode=hard_block`, `budgets_shadow_enabled=false` and an already-exhausted priced budget.
2. Invoke a non-safety LLM caller.
3. Shadow observation returns without evaluating the budget.
4. Enforcement sees no attached result and returns.
5. Provider I/O executes and incurs cost instead of returning the declared denial.

## Expected

An explicit non-warn enforcement mode must evaluate applicable budgets independently of whether observe-only evidence is enabled, or reject/fail closed when budget evaluation is unavailable. “Not evaluated” must never mean “within budget”.

## Impact

Operators can believe a hard provider-spend control is active while it is fully bypassed. Every subsequent call may continue spending past the cap without warning or degraded health.

## Root cause

Strict enforcement was implemented as a consumer of optional observation metadata. A missing optional observation was treated as a negative budget result instead of an explicit availability state.

## Minimal repair

Use one typed budget-evaluation result such as `within_budget | would_block | unavailable` whenever either observe-only evidence or strict enforcement needs a decision.

- `budgets_shadow_enabled` controls only observation/persistence of would-block evidence;
- non-safety `hard_block` rejects before provider I/O on `would_block`;
- `unavailable` is handled explicitly/fail-closed according to the selected strict contract;
- safety-caller exemptions remain unchanged;
- reuse the existing conservative fallback-chain estimate and atomic budget-reservation boundary rather than adding another calculator.

## Focused regression

Cover `complete_with_fallback()` end to end for:

- `hard_block + shadow=false + exhausted budget` → provider not called;
- `warn_only + shadow=false` → provider proceeds without shadow metadata;
- `hard_block + shadow=true` → expected strict behavior;
- unavailable budget evaluation → explicit unavailable/fail-closed behavior;
- safety-caller exemption.

## Boundary

No budget-value, provider-routing, strategy, Risk, Execution, credential or Live-authority change.

</details>
