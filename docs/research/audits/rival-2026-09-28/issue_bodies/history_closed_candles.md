# [data][ohlcv] History downloader persists forming candles and can skip their final values

## Why

历史表应只接收确认收盘的 CCXT bar。History downloader 直接写形成中 bar，且按 newest + timeframe 增量推进，可能跳过同一根 bar 的最终值。这会削弱历史数据可信度；没有证据证明真实收益已经受影响。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [extensions/data/ccxt_provider.py:691](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/extensions/data/ccxt_provider.py#L691) 将尚未结束的尾部 bar 标为 is_closed=false；`_persist:719` 的正常路径过滤未收盘行。
- [core/aletheia/data/history_downloader.py:90](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/data/history_downloader.py#L90) `_record_page` 整页 upsert；forward/bootstrap 只过滤 timestamp < now，仍接纳形成中 bar；其计数/newest 也包括此行。
- [core/aletheia/storage/repositories_market_data.py:519](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/storage/repositories_market_data.py#L519) upsert 不过滤 is_closed，表也不保存该标记。
- 真实入口为 cli/data_cmd.py、agent_runtime/scheduler.py 和 data/active_ohlcv_backfill.py。下载器下一次从 newest_existing + tf_ms 开始，可能不再获取已写入暂态 bar；其他实时来源可能纠正，故不声称永久污染。
- 查重 history_downloader / OHLCV unclosed / historical open candle；#1503 是未来时间行问题，#2801 是 market identity，不是本根因。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

在共享 `_record_page` 对用于历史持久化的 bar 做 closed 检查，forward/backward/bootstrap 一致使用。返回 bars_fetched、oldest/newest、progress 仅反映历史接受行；分页进度/原始页是否耗尽不能错误改用过滤后数量，避免空 closed 页导致死循环或遗漏。

保持 provider 可返回形成中 bar 的现有观测契约；不要在 provider 层删掉所有形成中数据，不要在多个 CLI/scheduler caller 重复过滤。没有已确认历史尾行时，下一次必须还能取到原 forming bar 的最终值。

## Non-goals

不清理/回写真实存量，不改变保留窗口、市场/venue 身份或运行调度。已有表无法仅凭时间戳证明某历史值曾是暂态；存量重叠重取须另有数据修复范围。本 issue 不借机修 #1395/#1789 的返回状态或 #1503 的未来时间过滤。

## Affected Authorities

[docs/design/contracts/ohlcv_provider_result_contract.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/design/contracts/ohlcv_provider_result_contract.md)（only confirmed CCXT bars persisted）；[docs/operations/development/agent-guides/STATE_AND_DATA.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/operations/development/agent-guides/STATE_AND_DATA.md)

## Verification — 本项验收

- 扩展 `tests/test_history_downloader.py`，固定时钟、替身精确 venue provider、隔离 repository/SQLite：closed + forming 页仅 closed 入库，返回计数和范围吻合。
- 全 forming 页、bootstrap、新上市空页、左侧补洞、已有 newest 的增量路径均有有界请求和单调游标；不能只检查 upsert 的 mock 参数而遗漏真实表读回。
- 推进到下一周期，原 forming bar 的最终值能被下载与持久化；下一次继续下载无重复计数异常。
- market_type/exchange 不变；成功和异常都 close provider；原错误传播与 max_bars 行为不被无关修改。

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
