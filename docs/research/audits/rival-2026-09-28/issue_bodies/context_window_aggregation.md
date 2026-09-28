# [economics][completeness] Context artifact summaries silently truncate the requested window to 5000 rows

## Why

API/CLI 以窗口汇总展示 context artifact 总量，却只统计最近 5,000 条，未标注截断。窗口中旧 kind、字符量和 freshness 分布会被遗漏。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/economics/context_artifacts.py:161](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/economics/context_artifacts.py#L161) summarize_context_artifacts 固定调用 list_context_artifacts(limit=5_000) 后 Python 聚合，返回 artifacts=len(records)。
- 同文件 `:132` 列表按 created_at DESC LIMIT，days/run_id 只是筛选条件；窗口 5,001 条时汇总最多 5,000。
- [core/aletheia/server/routes/economics.py:229](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/server/routes/economics.py#L229) /context-artifacts 与 [core/aletheia/cli/audit_cmd.py:396](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/cli/audit_cmd.py#L396) audit context 直接显示该汇总，明细 limit 无法恢复完整总数。
- 已搜索 summarize_context_artifacts、context artifacts/5000/truncation，无对应根因命中；本条静态证据，未运行数据库复现。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

汇总在现有模块用完整窗口 SQL COUNT/分组聚合或等价有界内存方法计算，不复用明细分页。使用相同 days/run_id 和共享时间边界，保证总数、各 kind、rendered_chars、freshness 来自一致读取快照。不得把 limit 调大或 fetchall 全量 JSON 当作修复。

保持输出键、合法空窗口与明细分页行为；不读取 rendered text。历史 malformed rendered_chars 必须维持明确处理，不能以 SQLite 宽松 CAST 把任意字符串算成合法数。

## Non-goals

不改变上下文生成/采样、成本政策、保留策略、数据库历史或 API 天数范围；不加入通用报表框架/新配置。

## Affected Authorities

[core/aletheia/economics/context_artifacts.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/economics/context_artifacts.py) 的 aggregate/window 语义；API/CLI 现有参数契约。

## Verification — 本项验收

- 复用 `tests/test_context_artifacts_properties.py` 和 `tests/test_economics_routes.py` 的临时 DB fixture，覆盖 0/5,000/5,001 条；最旧独有 kind 必须出现，字符量和 freshness 均准确。
- 窗口边界、run_id 过滤、不同明细 limit、include_artifacts true/false 均不改变汇总总体；总数等于分组计数和。
- malformed rendered_chars 不使其他统计失效且与目标语义一致；并发插入下多个汇总字段来自同一快照。
- API 与 CLI --json/文本通过实际汇总函数给相同结果；测试无需启动真实服务。

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
