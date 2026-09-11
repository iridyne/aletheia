# Framework-Agnostic Core Boundary & Contract (框架中立层契约)

> **契约目标**：本文件规定 Aletheia 前端重构中，哪些模块**必须是 100% 框架无关的纯 TypeScript 代码**，并严格禁止任何视图层技术（React / Solid / Vue）向核心机制反向侵入。

---

## 一、 绝对禁止的侵入模式（Prohibited Invasions）

在以往的开发中，出现过部分视图库/状态库跨越展现层边界、入侵系统底层的问题。重构过程中严格执行以下**红线禁令**：

1. ❌ **严禁底层运行时导入视图组件库的上下文或实例**：
   - 严禁 `wakeupGate`、`incidentDump`、`tabSync` 等底层系统服务接受 `QueryClient` 作为必选参数。
   - 严禁非 UI 工具库导入 `react`、`react-dom` 或特定 Hooks。
2. ❌ **严禁业务组件 Props 穿透具体的缓存/框架对象**：
   - 严禁 `SettingsProps`、`CardProps` 直接透传 `qc: QueryClient`。
   - 所有子模块与外部交互必须走**显式的领域回调（Domain Callbacks）**，例如 `onSave: (payload) => Promise<void>`、`onRefresh: () => void`。
3. ❌ **严禁把网络或客户端环境下的全局存储散落在组件内部**：
   - 严禁在页面或组件内部直接调用原生 `window.localStorage.getItem(...)` 或 `setItem(...)`。
   - 必须统一调用强类型的 `StorageService`。

---

## 二、 框架中立核心模块清单（The Neutral Core）

以下模块必须保持为独立的纯 TypeScript 结构，存放在 `src/core/` 或独立 SDK 包中：

```
frontend/src/core/
├── api/                   # 纯 TS REST Client (基于 OpenAPI 生成，零 React 依赖)
│   ├── client.ts
│   └── types.ts
├── realtime/              # 纯 TS WebSocket 客户端与流式事件总线
│   ├── transport.ts       # 负责连接建立、心跳检测、指数退避重连
│   ├── eventBus.ts        # 细粒度发布订阅中心 (Pub/Sub)
│   └── types.ts
├── storage/               # 统一类型安全存储服务
│   ├── storageService.ts  # 命名空间管理、JSON 自动序列化、Schema 版本迁移
│   └── keys.ts
├── runtime/               # 纯 TS 系统运行时健康与自愈
│   ├── wakeupGate.ts      # 纯 TS 实现的机器休眠/唤醒时间跳变检测
│   └── incidentDump.ts    # 纯 TS 实现的浏览器环境快照导出器
└── charts/                # 纯 TS 图表指标计算与微观数据模型
    └── priceChartModel.ts # OHLCV 解析、Volume Profile 48 分箱直方图计算
```

---

## 三、 标准事件总线规范（Realtime Event Bus Protocol）

为了打破以往“收到变更事件 $\to$ 批量失效 $\to$ REST 重拉整个列表”的低效模式，L2 实时总线必须遵循统一的增量事件格式：

```typescript
export interface MarketTickEvent {
  type: 'MARKET_TICK';
  symbol: string;
  price: number;
  change_24h_pct: number;
  timestamp: number;
}

export interface PositionUpdateEvent {
  type: 'POSITION_UPDATE';
  position_id: string;
  symbol: string;
  current_price: number;
  unrealized_pnl: number;
  status: 'OPEN' | 'CLOSED' | 'LIQUIDATING';
}

export interface SystemHealthEvent {
  type: 'SYSTEM_HEALTH';
  level: 'HEALTHY' | 'DEGRADED' | 'OUTAGE';
  issue?: string;
}
```

* **消费模式**：
  - 视图组件可以直接订阅单个 `symbol` 的 `MARKET_TICK`，仅在自身 DOM 节点挂载 `useCellFlash` 变色，**不触发父级组件重绘**。
  - 列表类组件仅在收到批量结构变化事件（如 `ORDER_FILLED`）时，才执行有界的数据重绘。
