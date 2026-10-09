# UI design system — archived appendices

> 2026-10-09 归档：原为 `docs/design/frontend/ui_design_system.md` 的附录 A（死代码清理记录）、
> 附录 B（变更历史）、附录 C（优化 Backlog）。这些是带日期的记录；Backlog 条目未排期，按
> [文档治理](../../../governance/policies/documentation_governance.md) 的 Issue-body 规则，只有在具备
> 独立证据与验收时才另开 Issue。当前规则见
> [`docs/design/frontend/ui_design_system.md`](../../../design/frontend/ui_design_system.md)。

## 附录 A：死代码清理记录

经 `grep -r "class-name" frontend/src/ --include="*.tsx"` 确认无引用后删除：

| 类名 | 原用途 | 删除 commit |
|------|--------|------------|
| `.cockpit-grid`, `.cockpit-panel` | 旧 2-col 布局 | 6fa0f69 |
| `.cockpit-hero`, `.hero-zone`, `.hero-label` 等 | 旧 v2 hero section | 6fa0f69 |
| `.cockpit-grid-3` + media | 旧 3-col grid | 6fa0f69 |
| `.market-watch`, `.market-watch-*` | 旧 mini-watchlist | 6fa0f69 |
| `.panel-divider`, `.recent-runs-strip` | 旧面板分割线/badge strip | 6fa0f69 |
| `.pill-*`, `.ticker-token-pending` | 旧 topbar pill | 早期 commit |
| body grid texture `background-image` | 旧网格纹理 | 6d2f557 |
| `.card-head-space{margin-bottom:0}` (重复定义) | 被 @2120 完整定义覆盖 | UI review |
| `.signals-row-long/short` + `td` 修饰 | 旧 Signals tab 行着色 | UI review |
| `.exchange-tiles`, `.exchange-tile*`（7 行） | 交易所 tile 卡片（改为 select 下拉） | 3b9ffff |
| `.entry-*`, `.human-approval-*`, `.funnel-*`, `.approval-list`, `.desk-*`, `.proposal-*`, `.dir-*`（~241 行） | Overview 决策卡 → 迁移至 Blotter | Blotter migration |

---

## 附录 B：变更历史

| 日期 | Commit | 摘要 |
|------|--------|------|
| 2026-06-15 | 88a46be | Command Center radius 统一 (12/14px → var(--r)) |
| 2026-06-15 | 74bf57a | WCAG AA contrast: th/labels faint→muted |
| 2026-06-15 | 33a47d4 | Charts timeframe: dropdown→segmented toggle |
| 2026-06-15 | 311f5c2 | lens/CC tabs 字号对齐 0.78rem |
| 2026-06-15 | de7a9b4 | 微交互扩展: card/row/watchlist press feedback |
| 2026-06-15 | 6fa0f69 | 删除 ~70 行 dead CSS |
| 2026-06-15 | 56ae379 | button press/focus/transition 80ms |
| 2026-06-15 | 6d2f557 | tables/nav/bg/buttons 终极打磨 |
| 2026-06-15 | 08dc3b8 | drawer/tabs/scrollbar/color discipline |
| 2026-06-15 | 675dc80 | controls/metrics/sections 紧凑 |
| 2026-06-15 | 2ac849c | Bloomberg-density pass 1 |
| 2026-06-26 | (多次) | UI 重构：风险控制 / Learning / Overview / 多页一致性 |
| 2026-06-29 | bcdcebd | Settings 搜索框从 shell 外移入 panel 内 |
| 2026-06-29 | 3b9ffff | Universe Board Toggle 加 Row 标签；Exchange Connection tile→select；删 exchange-tile* CSS |
| 2026-07-18 | (多次) | Blotter 工作站：Overview 决策卡迁移 + Proposals 合并；`fmtCompact` PnL tile 格式化；响应式布局精修（cockpit-rail 禁 column-count → grid/flex）；Settings API key 掩码 + 折叠；cc-heartbeat-zone flex→grid 修复坍塌；全局暗色 scrollbar（color-scheme:dark）；全站表格 `noWrap` 默认开启 |
| 2026-08-12 | pending | Usage 信息架构：消费优先、父子 token 不重复、三阶段加载、coverage list 替代 radar、所有明细表有界分页 |

---

## 附录 C：优化 Backlog

1. **响应式断点精修** — 700-900px 区间在 density pass 后可能过挤，需实机验证
2. **暗模式色彩微调** — `--surface-2` hover 在 OLED 上可能与 `--surface` 不可区分
3. **动画 reduced-motion** — 尚未添加 `prefers-reduced-motion` 媒体查询
4. **Command Center terminal 化** — message body 已切 monospace，可进一步加 prompt prefix / syntax highlighting
5. **表格 sticky header** — 长列表 `position: sticky` 列头
6. **Code-split** — JS bundle ~900KB，应拆分 lightweight-charts 为 dynamic import
7. **键盘快捷键提示** — 按钮旁显示 shortcut badge（`⌘K` 等）
8. **侧栏 icon-only 折叠** — 折叠为 48px icon rail，给 chart 更多空间
9. **Volume 子图** — PriceChart 下方联动 volume 柱图，lightweight-charts 原生支持
10. **PnL 归因热力图** — treemap 按持仓权重 + PnL 着色
