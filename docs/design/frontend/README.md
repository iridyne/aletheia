# Frontend Design System & Architecture

This directory serves as the documentation authority for Aletheia's Web UI (`frontend/`).

---

## 目录结构与快速导航 (Directory Atlas)

| 目录 | 定位与性质 | 权威状态 | 适用场景 |
| :--- | :--- | :--- | :--- |
| [`guidelines/`](./guidelines/) | **长期设计规范** (Living Guidelines) | 🟢 现行权威 (Current Authority) | 界面设计、Token 定义、文案与布局审核、零低熵铁律 |
| [`architecture/`](./architecture/) | **页面与交互架构** (Information Architecture) | 🟢 现行权威 (Current Authority) | 路由映射、页面职责划分、标的穿透、遥测控制台与通知中心 |
| [`rfcs/`](./rfcs/) | **行业对标与演进提案** (RFCs & Benchmarks) | 🟡 演进参考 (RFC / Guidance) | 金融终端对标 (Bloomberg/TradingView)、价格闪烁、演进路线与原型 |
| [`refactor/`](./refactor/) | **前端重构主契约与工作台规范** (Refactor Contracts) | 🟣 施工契约 (Refactor Blueprints) | 核心解耦契约、框架无关通信层、四大工作空间信息架构与迁移路径 |

> 💡 **历史审计与归档说明**：
> - 早期全站 13 页面缺陷排查、表格审计、全景 UI 走查报告（均已 100% 修复合入 master）已归档至 [`docs/archive/legacy/audits/frontend/`](../../archive/legacy/audits/frontend/)。
> - 历史旧版 Blotter、旧 Overview 等已废弃草案已完全清除，当前页面架构与路由以 [`architecture/webui_page_map.md`](./architecture/webui_page_map.md) 和 [`architecture/entry_workbench_design.md`](./architecture/entry_workbench_design.md) 为唯一事实依据。

---

## 核心文档推荐路由 (Fast Routing)

### 1. 界面开发与设计准则 (`guidelines/`)
- **[`ui_design_system.md`](./guidelines/ui_design_system.md)**: Aletheia 核心设计系统、物理表面分层、CSS Token 与组件约束。
- **[`no_fluff_ui.md`](./guidelines/no_fluff_ui.md)**: No Fluff 极简设计铁律——禁止无用修饰，界面优先回答核心事实。
- **[`terminal_taste_and_zero_entropy_rules.md`](./guidelines/terminal_taste_and_zero_entropy_rules.md)**: 金融交易终端审美与零低熵信息铁律。
- **[`visual_and_conveyance_system_design.md`](./guidelines/visual_and_conveyance_system_design.md)**: 高保真暗黑终端色彩模型与金融信息传达系统。

### 2. 页面拓扑与功能权属 (`architecture/`)
- **[`webui_page_map.md`](./architecture/webui_page_map.md)**: 全站路由、数据来源与页面责任归属总图（Current Authority）。
- **[`entry_workbench_design.md`](./architecture/entry_workbench_design.md)**: 入场工作台（#2288 当前权属规范，收敛替代了旧 Blotter）。
- **[`cross_page_symbol_teleportation_and_realistic_market_data.md`](./architecture/cross_page_symbol_teleportation_and_realistic_market_data.md)**: 全站标的瞬态穿透与微观市场数据诚实表达。
- **[`observer_mission_control_and_physical_telemetry_design.md`](./architecture/observer_mission_control_and_physical_telemetry_design.md)**: 任务控制中心（Mission Control）物理大屏遥测交互规范。
- **[`market_page_and_cross_linking_rectification.md`](./architecture/market_page_and_cross_linking_rectification.md)**: 市场研判页（Charts / Markets）与跨页联动整改标准。
- **[`notification_center_extraction_and_design.md`](./architecture/notification_center_extraction_and_design.md)**: 独立站内持久化通知中心设计规范。
- **[`overview_and_topbar_visual_refinement_plan.md`](./architecture/overview_and_topbar_visual_refinement_plan.md)**: 顶栏与 Overview 驾驶舱视觉重构方案。

### 3. 演进提案与参考原型 (`rfcs/`)
- **[`financial_terminal_benchmarking_and_evolution.md`](./rfcs/financial_terminal_benchmarking_and_evolution.md)**: 对标 Bloomberg / TradingView 体验差距分析与演进方案。
- **[`price_change_flash_benchmarking_and_practice.md`](./rfcs/price_change_flash_benchmarking_and_practice.md)**: 金融终端数字跳动背景闪烁（Cell Flash）成熟方案。
- **[`frontend_runtime_reload_notification_rfc.md`](./rfcs/frontend_runtime_reload_notification_rfc.md)**: 前端感知后台版本升级与优雅热重载 RFC。
- **[`design_system_optimization_proposal.md`](./rfcs/design_system_optimization_proposal.md)**: 设计系统演进优化建议草案。
- **[`frontend_seven_unresolved_architectural_solutions.md`](./rfcs/frontend_seven_unresolved_architectural_solutions.md)**: 前端七大架构议题深入方案与实施状态。
- **[`uncovered_strategic_topics_and_future_discussion.md`](./rfcs/uncovered_strategic_topics_and_future_discussion.md)**: 7 项未覆盖深水区战略议题清单。
- **[`prototypes/overview_preview.html`](./rfcs/prototypes/overview_preview.html)**: Overview 驾驶舱 HTML 交互原型。

### 4. 前端重构蓝图与施工契约 (`refactor/`)
- **[`00_master_contract.md`](./refactor/00_master_contract.md)**: 前端架构重构总契约、痛点归因、三项铁律与分阶段路线图。
- **[`01_framework_agnostic_boundary.md`](./refactor/01_framework_agnostic_boundary.md)**: 纯 TypeScript 核心解耦边界、禁止侵入清单与细粒度事件总线。
- **[`02_workstations_interaction_contract.md`](./refactor/02_workstations_interaction_contract.md)**: 四大工作空间（Mission Control, Alpha Workbench, Blotter, Governance）信息架构与交互契约。
- **[`03_solidjs_runtime_and_islands_contract.md`](./refactor/03_solidjs_runtime_and_islands_contract.md)**: SolidJS vs React 运行时选型与微岛混合架构契约（完全对齐 GitHub Issue #3030）。
- **[`04_neutral_runtime_decoupling_contract.md`](./refactor/04_neutral_runtime_decoupling_contract.md)**: 框架解耦与中立运行时演进契约（完全对齐 GitHub Issue #3069，七大具体问题解耦规范与三阶段路线）。
