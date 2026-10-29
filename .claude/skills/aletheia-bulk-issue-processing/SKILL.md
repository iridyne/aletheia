---
name: aletheia-bulk-issue-processing
description: SOP for processing large batches of Aletheia repository issues efficiently with subagents
---

# Aletheia 大规模 Issue 批处理 SOP

## 适用边界

只处理已经存在且生产可达的 Bug。Feature、Refactor、umbrella、migration 与 D2 设计变更优先由其 owner 收口；Generic Bug Fix 必须避让。

开始前完整读取仓库 `AGENTS.md` 路由到的指南。每项先声明：

- SDD Depth：D0 / D1 / D2；
- Contract Delta：D0 必须为 `NONE`，D1 必须列出 `ADDED / MODIFIED / REMOVED` 并同步 canonical Contract，D2 先有已接受 Proposal；
- 改动边界与明确不变项。

## 固定快照与分类

1. 固定一次 Open issue 编号集合与 master SHA，保留可追溯分类快照。
2. 逐项分类为 ARCH / BUG / OTHER；Bug 再按最小盈利循环标注 DIRECT / INDIRECT / NONE 与 High / Medium / Low。
3. 实施前重新读取 live issue、Open PR changed files 与 Orca worktree/terminal 状态。固定快照用于覆盖证明，live 状态用于行动。
4. current master 已修复的 Issue 用可运行回归证据关闭，不重复写代码。

## Eligibility Gate

只有同时满足以下条件才可 claim：

- live Issue 仍 Open，缺陷仍能从 current master 证明；
- High/Medium，生产可达；
- D0，或已经有明确 D1 Contract Delta 与 canonical Contract；
- 没有 Open PR、Feature/Refactor owner、dirty/active Orca worktree 修改同一语义边界；
- 不涉及未经批准的 Live、Risk、Execution、权限、凭据或不可逆状态变化。

发生 owner 冲突时进入 `BLOCKED`，记录 owner/PR/文件与重新筛选触发条件；不要并行写同一 surface。

## 并行契约

最多同时运行两个 subagent，只切真实独立的文件/模块边界：

- 实施阶段：两个独立实现切片，主 Agent 预先给出共享接口、文件所有权和验收条件；
- 复审阶段：每个 PR 一个只读 reviewer；
- 一个切片完成后立即补满第二个真实可行动切片；没有独立工作时不虚构并行。

Subagent 必须：

1. 先用 CodeGraph/LSP 定位符号与调用面；
2. 写明精确文件、符号、非目标和可观察验收；
3. 跳过 tests/Ruff/formatter/build/GitHub mutation，由主 Agent 统一验证与发布；
4. 共享文件冲突先通过 `hub` 协调。

主 Agent 保留 eligibility、冲突裁决、Git/GitHub、最终验证与 merge 所有权。

## Fail-Closed 修复模式

### 数据批处理

坏行不得污染新鲜事实，也不应无故中止其他独立行：验证有限值、领域范围、身份与来源时间；逐行跳过并把 domain/job 降级为 partial/failed。事务链中间行缺失会伪造连续性时，整个链必须 unavailable。

### 操作与状态转换

关键 mutation 失败必须向调用方返回/抛出真实失败；不得把“未找到”“未加载”“全局 PID 存在”当作成功 mutation。停止、结算、订单、审计等路径必须保留实际完成阶段与失败阶段。

## 最小验证

每项只跑能证明 root invariant 的最窄行为验证：

- Bug fix：先复现，再确认同一路径不再触发；
- Python：focused pytest + affected Ruff；
- Web UI：实际 Vite/browser surface；
- Gateway/TUI/CLI：运行真实命令并观察输出/state；
- D1：`scripts/check_docs.py` 与 canonical Contract；
- 推送前：`scripts/ci_preflight.py`、`git diff --check`、merge-tree。

macOS 不运行 broad pytest。Gateway/session/lifecycle/launchd 改动优先跑对应 focused 文件；CI/manual policy以仓库当前规则为准，未运行就不得声称 CI green。

## 审查清单

- D0/Contract Delta NONE 是否真的是既有契约修复；D1 文档与 UI/API 是否一致；
- 数据层 `return None/skip` 与操作层 `raise/failure result` 是否用对；
- `except Exception` 是否把读取失败伪装成不存在或零；
- NaN/Infinity、future/stale timestamp、单位与 venue/market identity 是否 fail closed；
- 事务链是否因跳过中间行而伪造完整；
- timestamp tie 是否有稳定 secondary identity；
- stop/cleanup/settlement 是否报告真实 partial phase；
- 没有 shim、旧 alias、重复路径或新增一次性抽象。

每个 exact head 至少进行一次独立只读复审；GitHub Codex finding 必须逐条修复或以代码证据驳回，并在新 head 重新触发 review。

## 非阻塞等待与合并

- 外部 review/CI 只在 head 变化后读取一次；等待期间继续处理独立工作，不 busy-poll。
- 合并前核对最新 master、PR exact head、mergeable、未解决 review threads 与当前 policy 要求。
- 使用 exact-head squash merge；远端 head 变化立即停止。
- 合并后验证 PR=`MERGED`、Issue=`CLOSED`、merge commit 位于 master。代码合并不等于 Fedora 部署、Runtime qualification 或盈利验证。

## Orca 清理门

仅清理本轮创建且同时满足以下条件的精确 worktree：

- PR 已合并、Issue 状态已核对；
- worktree clean；
- terminal 无任务、无活跃 Agent；
- 没有 dirty/untracked 用户工作。

先用版本匹配的 `orca-cli` 指南关闭精确 terminal，再移除精确 worktree。Detached、dirty、owner 不明或其他 Agent 的 workspace 一律保留。
