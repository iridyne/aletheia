# 站内通知中心从 StatusCluster 抽离与独立架构设计规范

> **核心目标**：将杂糅在 `StatusCluster.tsx` 悬浮弹窗中的“基础设施事件（Infra Events）”与“健康告警（Health Issues）”剥离，重构为独立的**站内持久化通知中心（Notification Center）**。  
> **核心原则**：纯技术事实，零黑话，零冗余词，职责绝对单一。

---

## 目录
1. [现状解剖：为什么必须从 StatusCluster 抽离？](#一-现状解剖为什么必须从-statuscluster-抽离)
2. [职责边界划分（Status vs Notifications）](#二-职责边界划分status-vs-notifications)
3. [独立通知中心架构与极简状态机](#三-独立通知中心架构与极简状态机)
4. [UI 交互与排版规范（零大改排版）](#四-ui-交互与排版规范零大改排版)
5. [重构实施代码路径（Before vs After）](#五-重构实施代码路径before-vs-after)

---

## 一、 现状解剖：为什么必须从 StatusCluster 抽离？

### 1. 现有代码事实 (`frontend/src/components/StatusCluster.tsx`)
当前 `StatusCluster.tsx` 承担了两个性质完全相悖的功能：

```
StatusCluster (单个组件 416 行)
  ├─ 1. 系统瞬时体征 (System Status)
  │    Gateway 运行状态, 调度器状态, 实盘状态, 权限模式, 节点延迟, 运营商心跳
  │
  └─ 2. 时间序列事件与告警 (Notifications & Events)  ◀── 杂糅在此！
       • healthIssues (健康告警列表: 110-112行, 332-348行)
       • recentInfraEvents (基础设施事件流: 113-115行, 363-379行)
       • operator.last_error (智能体报错: 391行)
```

### 2. 产生的严重体验缺陷
1. **焦点一失就消失（无法驻留阅读）**：
   `status-popover` 依赖 `onBlurCapture` 与 `pointerdown` 监听。操作员将鼠标悬停在状态灯上展开面板，一旦鼠标移出边界，弹窗立刻自动关闭。当有一条详细的报警信息或长报错时，操作员根本无法从容阅读；
2. **缺乏“未读状态”与“已读消除”生命周期**：
   目前无论是 3 小时前恢复的网络事件，还是 10 秒前新产生的风控拦截，永远按照固定数组挤在面板底部。操作员无法区分“哪些是我已经处理过的，哪些是刚刚新爆出来的”；
3. **顶栏状态集群自身臃肿不堪**：
   为了塞下事件列表，`status-popover` 高度被撑到 500px 以上，里面横跨 System、Infrastructure、Health Issues、Infra Events、Operator、Code Drift 六个大块，违反了组件单一职责原则（Single Responsibility Principle）。

---

## 二、 职责边界划分（Status vs Notifications）

彻底将“**系统当前瞬时体征**”与“**带时间戳的离散历史事件**”解耦为两个平级的顶栏组件：

| 维度 | 系统状态集群 (`StatusCluster`) | 站内通知中心 (`NotificationCenter`) |
|---|---|---|
| **物理本质** | **瞬时态（Snapshot / Gauge）** | **事件流（Time-Series / Stream）** |
| **回答的问题** | “网关现在跑着没？调度器停没停？延迟多少？” | “刚才发生了什么故障？哪个订单被拦截了？什么时候恢复的？” |
| **触发方式** | 点击/悬浮展开紧凑状态卡片 | 独立铃铛图标点击触发右侧滑出抽屉 |
| **持久性** | 随鼠标移开可自动关闭 | 显式点击关闭按钮或按 `Escape` 才关闭 |
| **生命周期** | 永远只显示当下一秒的最新值 | 具备 `unread`（未读高亮）与 `lastReadAt` 审计标记 |

---

## 三、 独立通知中心架构与极简状态机

按照第一性原理与奥卡姆剃刀原则，**不增加任何后端新接口，0 外部依赖**，直接消费现有数据源：

```
数据源 (现有接口)
  ├─ 1. api.getInfraEvents()     (物理网络/网关恢复/掉线事件)
  ├─ 2. data.health.issues       (当前未结健康告警)
  └─ 3. WebSocket "alert" 消息   (100ms 紧急广播帧)
             │
             ▼
[NotificationCenter 统一归并引擎]
  • 合并排序: 按时间戳倒序排列
  • 未读计算: Event.timestamp > localStorage['aletheia.notifications.lastReadAt']
             │
             ├── 未读数 > 0: 顶栏铃铛高亮 + 呈现小红点数字
             └── 操作员点开抽屉: 自动将 lastReadAt 更新为当前时间，清除红点
```

### 极简状态存储（无需后端数据库变更）：
未读状态直接借助浏览器的 `localStorage` 保存一个时间戳标尺：
```typescript
const LAST_READ_KEY = 'aletheia.notifications.lastReadAt'
```
* **未读数量**：`events.filter(e => new Date(e.created_at).getTime() > lastReadTimestamp).length`
* **标记已读**：打开抽屉时执行 `localStorage.setItem(LAST_READ_KEY, new Date().toISOString())`。

---

## 四、 UI 交互与排版规范（零大改排版）

顶栏（Topbar）排版保持完全稳定，仅在状态集群左侧或右侧嵌入一个标准的控制台铃铛按钮：

```
顶栏 (Topbar) 右侧布局:
[行情条] ... [Dev Banner]  [ 🔔 2 ]  [ ●●●●● RUNNING ]
                            ▲             ▲
                   独立通知中心(带未读)   纯粹系统状态卡片
```

### 1. 铃铛按钮形态 (`NotificationBell.tsx`)
```tsx
<button
  type="button"
  className="topbar-btn notification-bell-btn"
  onClick={() => setDrawerOpen(true)}
  aria-label={`Notifications: ${unreadCount} unread`}
>
  <Bell size={16} />
  {unreadCount > 0 && (
    <span className="bell-badge mono">{unreadCount > 9 ? '9+' : unreadCount}</span>
  )}
</button>
```

### 2. 通知抽屉交互形态 (`NotificationDrawer.tsx`)
点击铃铛后，从屏幕右侧滑出标准的 `DetailDrawer`（360px 宽）：
* **抽屉标题栏**：`Notifications & Alerts` + 右侧紧凑按钮 `[Mark all read]`；
* **分类筛选（Segment Toggle）**：`All` | `Errors` | `Health`；
* **事件条目卡片**：
  - **红/黄严重度图标**（`Critical` / `Warning` / `Info`）；
  - **事件代码**（等宽粗体，如 `external_connectivity_unavailable`）；
  - **相对时间戳**（如 `2m ago` / `14:05:02`）；
  - **详细文案与操作指引**（如 `hint: Feed will auto-retry in 30s`）；
  - 若为未读事件，左侧显示一条 2px 细蓝线或高亮微光。

---

## 五、 重构实施代码路径（Before vs After）

### 1. 从 `StatusCluster.tsx` 中物理剥离的冗余代码：
* **删除**：
  - `infraEventsQuery` 相关代码（第 89 行）；
  - `recentInfraEvents` 排序逻辑（第 113-115 行）；
  - `healthIssues` 在面板中的重复渲染（第 332-348 行）；
  - `sp-section Infra Events` 整个 DOM 树（第 363-379 行）。
* **保留**：
  - `StatusCluster` 内部纯净保留网关、调度器、权限、心跳、网络延迟等指标，组件代码量从 416 行骤降至约 260 行。

### 2. 新建独立组件：
* `frontend/src/components/notifications/NotificationBell.tsx`（顶栏触发器，~25 行）
* `frontend/src/components/notifications/NotificationDrawer.tsx`（展示与已读标记抽屉，~80 行）
* `frontend/src/components/notifications/useNotifications.ts`（数据合并与未读计算 Hook，~40 行）

---

## 六、 收益评估

1. **消除视觉干扰**：解决操作员移开鼠标状态卡片就消失、无法完整阅读报错的痛点；
2. **审计生命周期建立**：首次为 Aletheia 建立了“离座后回来看一眼有没有未读故障”的标准事件审计能力；
3. **组件单一职责**：`StatusCluster` 回归仪表盘状态真理，`NotificationCenter` 专注时序告警，架构高度解耦。

---

*（本文档已保存至：`docs/design/frontend/notification_center_extraction_and_design.md`，可直接作为后续落地拆分的实施依据）*
