# SolidJS vs React 运行时选型、混合微岛契约与演进决策 (#3030 对齐)

> **契约目标**：本文件基于 GitHub Issue #3030（*Deferred: evaluate SolidJS migration for Dashboard UI runtime*）的决议与深度诊断，为 Aletheia 前端重构制定**框架选型边界、微岛混合架构（Solid Hot-Path Islands）标准、评测基准与止损红线**。

---

## 一、 对 #3030 核心决议的继承与定调

在 Issue #3030 中，系统架构师与系统所有者（Owner）已经做出了明确的定调：

1. **禁止“休克式全量重写”（No Blind Full Rewrite）**：
   - 现有的 React 19 + TypeScript 架构拥有 130+ TSX 文件、455 个全绿单元测试、OpenAPI 类型严格同步，以及高度严苛的打包预算（Critical Brotli 143KB < 358KB）。
   - 将这套成熟系统全量推倒重写为 SolidJS 会将团队注意力从真实的交易/风控/收益闭环转移到无谓的框架搬砖中，**ROI 极低且风险极高**。
2. **诊断真实瓶颈所在（Architecture, Not Just Framework）**：
   - Aletheia 历史上面临的 UI 延迟或卡顿，绝大部分源于**后端大 Payload / 数据库查询 / React Query 粗粒度失效导致的 REST 级联风暴**，而不是 React 虚拟 DOM 本身的计算开销。
   - 框架的真正痛点在于：**TanStack Query 跨界侵入了系统底层机制（休眠唤醒、状态快照、配置契约）**，导致视图与运行时过度耦合。
3. **优先演进路径：React 应用外壳 + Solid 性能微岛（Solid Hot-Path Islands）**：
   - 类似于“Python 负责宏观编排，Rust/PyO3 负责极限热点计算”的哲学：**React 19 继续主导全站外壳、路由、全局弹窗与复杂表单设置；而在高频行情跳动、微观深度盘口与高频台账刷新等极限热点区域，按需挂载独立的 SolidJS 渲染微岛**。

---

## 二、 两种形态的核心对比与权衡（Trade-off Matrix）

| 维度 | 方案 A：保留纯 React 19（切除架构滥用） | 方案 B：React 外壳 + Solid 性能微岛（#3030 推荐路径） | 方案 C：全盘彻底迁移至 SolidJS |
| :--- | :--- | :--- | :--- |
| **执行模型** | 虚拟 DOM Tree Diffing + 调度器 | 外壳虚拟 DOM，热点微岛编译期原生细粒度响应 | 彻底去虚拟 DOM，全站基于 Signals 细粒度更新 |
| **行情跳动性能** | 依靠 `useMemo` / 原生 DOM 逃生通道，仍有组件上下文开销 | **极限吞吐**：Tick 推送直达 DOM 文本节点，0 组件重渲染 | **极限吞吐**：全站细粒度更新，CPU 占用极低 |
| **工程重构代价** | **极低**：保持现有技术栈，仅解耦底层总线与状态 | **中等受控**：仅在 1~2 个热点组件引入微岛，渐进式验证 | **极高**：130+ 组件全部重写，测试框架与生态链重搭 |
| **生态兼容性** | 顶级：兼容所有现成 React 库、TanStack 生态 | 良好：热点区域独立沙箱，互不污染 | 受限：部分重型 React 专属生态需寻找替代品 |
| **当前推进建议** | **先跑第一步**（Phase 0 解耦） | **作为高频热点实验的第二步**（Phase 1 验证） | **当前明确 DEFERRED（禁止全量启动）** |

---

## 三、 规范的“Solid 性能微岛”实施契约（Solid Island Contract）

根据 #3030 的规定，如果要在特定工作台（如 Alpha Workbench 的微观深度、或 Mission Control 的实时跳字 Ticker）中激活 Solid 微岛，必须严格遵守以下 **6 大铁律**：

### 1. 独立所有权，单一 DOM 子树（One Subtree, One Owner）
- React 仅负责挂载/卸载微岛的容器宿主节点（Host Node，如 `<div id="island-market-ticker" />`）。
- 一旦挂载，SolidJS 独占该宿主节点下方的整个 DOM 树。**React 绝不能在微岛内部进行任何子 DOM 更新或属性干扰**。

### 2. 严禁倒灌 React 依赖（Zero React Inside Island）
- Solid 微岛内部绝对禁止导入 `react`、`react-dom`、`@tanstack/react-query` 或任何 React UI 组件库。
- 微岛内部仅允许使用 SolidJS 核心原生原语（`createSignal`, `createMemo`, `For` 等）。

### 3. 基于框架中立总线的数据消费（Framework-Neutral Boundary）
- **禁止的错误模式（Anti-Pattern）**：
  $$\text{React Query} \longrightarrow \text{React 组件 Rerender} \longrightarrow \text{传 Props 给 Solid 微岛}$$
  *（这种做法同时保留了 React 的重新渲染和两个运行时的开销，属于双重负债）*。
- **正规模式（The Correct Flow）**：
  $$\text{WebSocket / L2 Event Bus (纯 TS)} \longrightarrow \text{Solid 微岛独立订阅 Signal} \longrightarrow \text{原地局部 DOM 更新}$$
  *（React 仅在初始化时把容器给 Solid，之后数据流动完全绕过 React 调度器）*。

### 4. 共享底座与复用规范
- 微岛必须直接复用 `docs/design/frontend/guidelines/` 规定的 CSS 变量系统（`index.css`）、Tailwind 原子类与纯 TypeScript 图表模型（`priceChartModel.ts`），绝不维护第二套视觉或计算样式。

---

## 四、 激活微岛实验的硬性门槛与止损红线（Stop Rules & Gates）

在将任何 Solid 微岛合并入生产环境之前，必须执行严格的**同等负载对照测试（A/B Profiling Gate）**：

1. **激活门槛（Revisit Triggers）**：
   - 只有在 Chrome DevTools Profiling 明确证明：在 50~100 档盘口实时推流或高频 Ticks 下，React 渲染占用主线程 CPU 显著超过 35%、或帧率明显掉出 60fps 时，才允许启动微岛改造。
   - 禁止在普通的静态表单、Settings 配置面板或低频审计列表上浪费精力尝试微岛。
2. **度量指标（Must Measure）**：
   - 主线程 CPU 耗时（Scripting / Rendering Time）；
   - 突发更新下的 DOM 操作延迟；
   - 引入 Solid 运行时增加的 Brotli Bundle Delta（不得突破已有的静态体积预算）；
   - 跨框架调试与测试维护成本。
3. **止损红线（Rollback Invariant）**：
   - 如果微岛带来的运行时性能提升在实际使用中低于 20%，或者胶水代码复杂度显著增加，**必须无条件回滚微岛，维持单一纯粹的 React 呈现层**。绝对禁止无节制的双运行时蔓延。
