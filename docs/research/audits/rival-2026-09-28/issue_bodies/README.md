# 首批修复 issue 正文发布快照

日期：2026-09-28。Owner 要求将分析转成新 Agent 可独立读取、修复、验证并合并 master 的完整 issue body。本目录保存本次发布内容的冻结副本，**后续状态、补充、PR 与关闭信息以 GitHub 为唯一来源，不维护第二份执行看板**。本次没有启动其他 Agent 或实施业务修复。

发布前拉取 master 并固定源码 `a998070130d2ab892e9ba89b7de48886a1a708e4`；所涉根因文件及追加的 backup/frontdoor 调用路径与审计基线相同。发布后逐条回读 GitHub，正文与本地文件逐字一致。三个新 issue 完成限定查重，五个旧 issue 保留原编号及折叠的历史正文；旧正文路径和策略建议以当前正文及仓库权威为准。

## 可直接领取的入口

| GitHub issue | 问题 | 本次动作 | 完整正文快照 |
|---|---|---|---|
| [#3675](https://github.com/iridyne/aletheia/issues/3675) | Preview seed 数据根隔离 | 新增 | [preview_seed_isolation.md](preview_seed_isolation.md) |
| [#1379](https://github.com/iridyne/aletheia/issues/1379) | Backup / sync 配置脱敏 | 补充原 issue | [config_archive_redaction.md](config_archive_redaction.md) |
| [#1332](https://github.com/iridyne/aletheia/issues/1332) | Frontdoor / WebSocket 未知鉴权拒绝 | 补充原 issue | [remote_auth_unavailable.md](remote_auth_unavailable.md) |
| [#1624](https://github.com/iridyne/aletheia/issues/1624) | 硬预算脱离 shadow 开关 | 补充原 issue | [budget_shadow_independence.md](budget_shadow_independence.md) |
| [#3676](https://github.com/iridyne/aletheia/issues/3676) | 历史下载仅持久化收盘 K 线 | 新增 | [history_closed_candles.md](history_closed_candles.md) |
| [#3677](https://github.com/iridyne/aletheia/issues/3677) | 完整窗口 context artifact 汇总 | 新增 | [context_window_aggregation.md](context_window_aggregation.md) |
| [#1725](https://github.com/iridyne/aletheia/issues/1725) | Economics schema 完整性 | 补充原 issue | [economics_schema_completeness.md](economics_schema_completeness.md) |
| [#1395](https://github.com/iridyne/aletheia/issues/1395) | 下载 / 回填 CLI 失败退出码 | 补充原 issue | [download_cli_exit_status.md](download_cli_exit_status.md) |

## 领取与交付

每份 issue 都包含当前问题、固定源码链接、直接依赖、D0 / Contract Delta NONE、修复目标和技术落点、非目标、行为回归及合并完成条件；可单独传递 GitHub 链接，无需把本聊天或其他报告交给执行者。具体工程选择允许根据最新源码调整，但不能降低验收。

Owner 此次请求明确要求修复到 master 合并闭环，正文记录这一授权。它不授予部署、真实凭证、历史数据修复、Live 或新的 D1/D2 权限。阻塞于真实分支保护时如实报告，不用 admin 绕过。正常步骤无需再次逐个请求批准；合并必须由实际 merged 状态和 master 包含证据证明。

八项并不意味着必须同时派八个 Agent。Preview、脱敏、鉴权可优先领取；预算、schema、context 汇总涉及 economics 共同测试，多个 PR 相继合入时需在新 master 重验相关路径。历史下载与 CLI 退出码是独立根因，不能用其中一个关闭另一个。

本快照仅发布首批明确可启动的八项，不代表其余扫描发现均已转成 issue。scorecard 未决准入语义、其余 CLI/server/extensions 发现及剩余模块分析继续保留在原报告；不为它们自动扩权或派发。下一批发布也需核对 master 与已有 issue/PR。
