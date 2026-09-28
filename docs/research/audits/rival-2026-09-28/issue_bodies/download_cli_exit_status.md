# [data][cli] Download and backfill failures still exit successfully

## Why

下载器已经返回失败事实，CLI 却只打印错误并 exit 0，自动化无法据退出状态区分成功、部分失败与全部失败。修复应保留成功部分和错误摘要。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/data/history_downloader.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/data/history_downloader.py) 将 fetch exception 编码到 DownloadResult.error，保留部分 bars_fetched。
- [core/aletheia/cli/data_cmd.py:319](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/cli/data_cmd.py#L319) 单次 download 输出 Completed with error 后正常结束；backfill 的错误聚合也未非零退出。
- 前轮隔离 CliRunner 使用异步下载替身返回 error=synthetic_fetch_failure，单次和单 symbol/timeframe batch 均 exit 0；没有真实请求。
- 已有 #1395，复用此 issue；scheduler #1789 是另一消费者，不以本次 CLI 修复自动关闭它。

## Dependencies / Activation Conditions

与历史 K 线 closed-bar 修复没有实现依赖；前者修改 downloader，本条主要改 cli/data_cmd.py 与 CLI tests。若两项同时在途，合入后在最新 master 重跑相关 CLI 回归。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

在现有 download 命令输出完整结果后，存在 result.error 则 typer.Exit(1)。backfill 保留逐项与汇总输出，任一实际下载失败则最终 exit 1；保留已成功兄弟结果，不遇首错直接丢弃后续原本允许的工作。无错误合法空数据/全部成功仍为 0。异常边界保持真实失败，不把错误转成空成功。

## Non-goals

不改变 downloader 的历史写入规则、重试、市场选择、调度；不修改 scheduler #1789，不把无数据自动定义成错误，不增加新的 batch 状态存储。

## Affected Authorities

[core/aletheia/data/history_downloader.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/data/history_downloader.py) DownloadResult.error 与 CLI exit contract。

## Verification — 本项验收

- 扩展 `tests/test_data_cli.py`，用真实 Typer command 注册与异步替身：单次成功/失败/部分数据加 error；batch 全成功/混合/全失败。
- 失败 exit 1，完整 errors 和部分计数仍可见；所有成功 exit 0；合法空结果保持 0。
- batch 混合案例证明成功兄弟没有丢失，失败后没有输出暗示全批成功的完成状态。默认选池和显式 symbols 路径均核对。
- 网络、配置与 DB 全隔离；不要仅直接调用普通函数后断言打印字符串。

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

Reviewed current `origin/master@134c35a8ccfa6345d29bd235f8a4b314299b9de3` on 2026-08-22.

## Problem

`aletheia data download` and batch backfill encode fetch failures in `DownloadResult.error` and print them, but still exit 0. Cron, runbooks, and Agent automation therefore treat zero-row or partial failed market-data acquisition as successful.

## Evidence

- `aletheia/data/history_downloader.py:106-118,215-222` catches fetch failures into result errors.
- `aletheia/cli/data_cmd.py:390-397` prints `Completed with error` for a single download without nonzero exit.
- `data_cmd.py:433-445` aggregates batch errors into text but also exits successfully.

## Impact

Downstream validation/research can run on missing or partial history because the process contract says success, while automated retry/alerting never activates.

## Minimal repair path

Keep the complete human/partial-count summary, then raise `typer.Exit(1)` when a single result has an error or any batch member failed. Do not discard successful sibling results.

## Regression proof

Stub a single and batch downloader with explicit errors; assert exit code 1 and retained error/partial-count output. All-success remains 0.

## Classification and duplicate check

Class A, severity medium. Searches found no exact CLI exit-status issue.



</details>
