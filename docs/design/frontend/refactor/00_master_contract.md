# Frontend Architecture Refactor Contract (前端重构主契约)

> **文档定位**：本文档为 Aletheia Web UI 从“传统多页面 CRUD 管理后台”演进为“专业自营级金融遥测交易终端（Trading OS / Supervisory Cockpit）”的顶层重构契约与实施蓝图。  
> **核心使命**：彻底解耦 UI 视图与系统底层机制，建立框架无关的通信与状态总线，收敛信息架构为四大工作台，实现细粒度流式响应与零后端破坏性迁移。

---

## 一、 核心痛点与重构动因（Why We Refactor）

1. **心智模型错位（CRUD Dashboard vs Supervisory Cockpit）**：
   - 现状：13 个分散路由相互割裂，操作员在多个页面间频繁点击跳转，缺乏自营台全局资产、即时敞口、AI 决策流与紧急干预的连贯视角。
   - 目标：收敛为 **4 大聚焦工作空间（Workstations）**，90% 的日常观察与审计在单屏完成秒级下钻。
2. **状态传播机制低效（间接震荡拉取 vs 真实流式推送）**：
   - 现状：`WebSocket 提示变更 -> React Query 失效 Key -> REST 全量并发重拉 -> 全量虚拟 DOM diff`。高频场景下引发请求风暴且无法实现局部流式跳动。
   - 目标：引入框架中立的 **细粒度数据总线（Event/Signal Bus）**，WebSocket 增量直达目标 DOM 节点，REST 仅负责冷启动与重连补偿。
3. **框架严重越界（React / TanStack Query 成为底层运行胶水）**：
   - 现状：休眠唤醒（`wakeupGate`）、事故快照（`incidentDump`）、配置契约（`SettingsProps`）深度绑定 TanStack Query 实例，架构被单一套件锁死（详见 GitHub Issue #3030 / #3069）。
   - 目标：下沉为**纯 TypeScript 运行时服务层**，上层无论使用 React 19、SolidJS 还是自研 Signals，均可无缝切换。

---

## 二、 三项不可逾越的重构铁律（Non-Negotiable Invariants）

1. **铁律一：后端零破坏（Zero Backend Breaking）**
   - 绝对禁止因前端重构要求后端改写已有稳定的 FastAPI 业务逻辑与 REST 路由。
   - 前端必须严格基于现有的 FastAPI 接口、OpenAPI Schema 与 WebSocket 协议演进。
2. **铁律二：契约层框架无关化（Framework-Agnostic Core）**
   - 核心通信层（`API Client`）、实时推流层（`Realtime Transport`）、存储层（`StorageService`）与图表计算模型（`priceChartModel`）必须是**纯 TypeScript 模块**，禁止导入任何特定 UI 框架（如 React/Solid/Vue）的 Hooks 或 Context。
3. **铁律三：零低熵与金融终端极简品味（Zero-Entropy & Terminal Taste）**
   - 严格继承 `docs/design/frontend/guidelines/` 的核心规范：
     - `ui_design_system.md`（高密度网格、表面分层、等宽数字、slashed-zero）
     - `no_fluff_ui.md`（严禁自嗨式保姆文案，界面第一优先级回答事实）
     - `terminal_taste_and_zero_entropy_rules.md`（冷峻克制，严禁无意义的荧光污染与过度动效）

---

## 三、 四大工作空间信息架构收敛（Workstation Convergence）

全站 13 个独立路由收敛为 4 大核心工作空间：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Aletheia Topbar (遥测顶栏)                         │
│  [Logo] [Market Ticker] [Drift Pill] [Notification Center] [StatusCluster]  │
├──────────────────┬──────────────────────┬───────────────────┬───────────────┤
│ 1. Mission       │ 2. Alpha & Entry     │ 3. Execution      │ 4. Governance │
│    Control       │    Workbench         │    Blotter        │    & System   │
└──────────────────┴──────────────────────┴───────────────────┴───────────────┘
```

### 1. Mission Control（地面任务控制中心 / 默认工作台）
- **职责**：全局资产监视、即时风险敞口、AI 决策流与操作员指令干预。
- **融合页面**：`Overview` + `RiskControls` + `CommandCenter` + `Live Working Orders`。
- **布局布局**：
  - **左列（资产与风险）**：净值折线、当日 PnL、多空杠杆敞口、挂单条。
  - **中列（智能体动态）**：Agent Activity 实时决策流、Thesis 归因。
  - **右列（指挥与干预）**：Command Center 命令行交互、全局紧急制动/熔断闸。

### 2. Alpha & Entry Workbench（研判与入场工作台）
- **职责**：候选池筛选、微观结构透视与跨赛道研判。
- **融合页面**：`Universe` + `Charts` + `PredictionLens` + `SerenityLens`。
- **核心交互**：
  - 左侧为 Candidate/Entry Plan 列表与过滤器；
  - 点击任何标的，右侧图表与微观体征（Orderbook 深度、Volume Profile、资金费率、持仓量 OI）**秒级原地穿透**，杜绝切页重载。

### 3. Execution & Ledger Blotter（执行台账与审计工作台）
- **职责**：订单流水、成交明细、仓位清算与完整因果溯源。
- **融合页面**：`TradesPositions`（Orders / Fills / Positions / Causal Chain）。
- **核心交互**：
  - 高密度电子台账表（Bloomberg EMSX 风格），支持行级展开透视完整因果链：
    $$\text{Opportunity} \longrightarrow \text{Entry Plan} \longrightarrow \text{Risk Approval} \longrightarrow \text{Order} \longrightarrow \text{Fill} \longrightarrow \text{Position}$$

### 4. Governance & System（系统治理与配置中心）
- **职责**：学习模块演化、Token/成本账单与全局系统参数配置。
- **融合页面**：`Learning` + `Usage` + `Settings` + `NewsIntel`。
- **核心交互**：分类清晰的标签 Hub，集中展示 LLM 消耗与归因、假设验证和运行参数。

---

## 四、 分层架构与框架中立化设计（Architecture Layers）

```mermaid
flowchart TD
    subgraph L1 [Layer 1: Transport & Contracts (纯 TS / 框架无关)]
        API[OpenAPI Generated Types & REST Client]
        WS[WebSocket Stream Transport]
        STORAGE[Typed Storage Service]
    end

    subgraph L2 [Layer 2: Core Domain State & Services (纯 TS / 细粒度发布订阅)]
        BUS[Domain Event / Signal Bus]
        TICKER[Realtime Ticker & Quote Store]
        WAKEUP[OS Wakeup & Suspend Gate]
        INCIDENT[Incident Diagnostic Snapshot Engine]
    end

    subgraph L3 [Layer 3: UI Adapters & View Layer (可替换展现层)]
        REACT[React 19 Adapter / Shell]
        SOLID[SolidJS Island Adapter (Hot Paths)]
        COMP[Design Primitives / Tables / Drawers / Charts]
    end

    L1 --> L2
    L2 --> L3
```

1. **L1（通信与协议层）**：
   - 彻底与 UI 框架解耦，封装纯 TypeScript 的 API Client 与 WebSocket 连接池。
   - 自动处理鉴权 Token、断线重连指数退避与心跳探测。
2. **L2（领域运行时与状态总线）**：
   - 提供基于发布订阅或 Signals 的全局数据总线。
   - 行情跳动（Ticks）、仓位变化（Fills）直接通过事件流分发给具体的观察者，不依赖上层视图框架的生命周期。
3. **L3（呈现层适配器）**：
   - 视图层仅作为消费端，监听 L2 派发的数据快照或细粒度增量，进行高效率渲染。

---

## 五、 分阶段平滑迁移路线图（Migration Milestones）

* **Phase 0：底层解耦与中间契约固化（当前阶段）**
  - [x] 梳理并重组 `docs/design/frontend/` 目录体系，归档历史审计。
  - [x] 登记 GitHub 架构演进与盲区 Issues（#3068 ~ #3071）。
  - [ ] 实现框架中立的 `StorageService`（替代散落的 `localStorage`）。
  - [ ] 解耦 `wakeupGate` 与 `SettingsProps`，切断对 `QueryClient` 的深层依赖。
* **Phase 1：挂载遗留资产与核心组件原生化**
  - [ ] 挂载 `NotificationCenter` 至顶栏，打通站内持久化通知与告警红点（#3068）。
  - [ ] 封装框架无关的 WebSocket 增量分发引擎（#3070）。
* **Phase 2：首个工作台样板间验证（Alpha & Entry Workbench）**
  - [ ] 在 `/universe` 与 `/charts` 间打通即时穿透与微观结构流式更新，验证单屏双联动的低开销体验。
* **Phase 3：全站四工作台收敛与旧路由平滑切流**
  - [ ] 逐步将 13 页面并入 4 大工作台，旧路由统一配置 301/302 式前端平滑重定向。
