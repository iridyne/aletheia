# Aletheia 任务控制中心（Mission Control）与物理大屏遥测交互规范

> **设计定位**：Aletheia 前端不是供人类手动高频下单的交易软件，而是**全自主 AI 交易系统的地面任务控制中心（Mission Control / Supervisory Cockpit）**。操作员的核心职责是观察、审计、评估与全局熔断。  
> **核心原则**：保持现有 UI 架构与组件网格排版稳定（零大动干戈），借鉴**物理世界真实交易所大屏、VFD 荧光显示屏与电子管辉光余辉衰减机制**，为观测终端注入纯正的物理遥测质感。

---

## 目录
1. [心智模型确立：从“操作台”到“任务控制中心”（Mission Control）](#一-心智模型确立从操作台到任务控制中心mission-control)
2. [物理世界显示原型溯源（Physical Archetypes）](#二-物理世界显示原型溯源physical-archetypes)
3. [通电激发与荧光指数衰减模型（Phosphor Glow & Decay Physics）](#三-通电激发与荧光指数衰减模型phosphor-glow--decay-physics)
4. [小数点跳动与单元格动态发光规范（Cell & Digit Dynamics）](#四-小数点跳动与单元格动态发光规范cell--digit-dynamics)
5. [现有 UI 架构下的零破坏（Zero-Disruption）嵌入点](#五-现有-ui-架构下的零破坏zero-disruption嵌入点)
6. [CSS 硬件加速实现与低开销规范](#六-css-硬件加速实现与低开销规范)

---

## 一、 心智模型确立：从“操作台”到“任务控制中心”（Mission Control）

### 1. 为什么“绝不追求人类操作速度”？
Aletheia 的核心闭环是高度自主的：
$$\text{Market Feed} \longrightarrow \text{AI Agents} \longrightarrow \text{Entry Plan} \longrightarrow \text{Risk Engine} \longrightarrow \text{Gateway} \longrightarrow \text{Exchange}$$

在这一架构中，每一个具体订单的参数生成、几何测算与报单执行全权由算法完成。  
如果把界面设计成 TradingView 式的人类手动拖拽止损线，或者 Bloomberg 式的人类快速敲键下单，就会彻底背离系统的本质——**把自动化系统的监督者降级为了手动操作员**。

### 2. 界面设计的新灵魂：Major Read-Only（超级只读与因果监视）
* **第一诉求**：**信噪比与态势感知**。一目了然看清 AI 当前在想什么（Thesis）、为什么买（Rationale）、当前各币种的实时生命体征如何。
* **第二诉求**：**物理世界真实交易大屏的仪式感与信任感**。当数据刷新时，操作员能清晰感知到“数据从物理交易所通过电信号击中了我的屏幕”，如同站在纽约证券交易所（NYSE）或芝加哥商品交易所（CME）的巨型跳动行情屏下。
* **第三诉求**：**版面克制与排版稳定**。现有 13 个页面的卡片布局与导航体系经过多次业务打磨，已非常成熟，**严禁无故大改既有组件排布**。升级应聚焦在“物理质感、微交互动效与数据表现力”本身。

---

## 二、 物理世界显示原型溯源（Physical Archetypes）

在设计跳动与变色时，不采用现代消费级 App 绵软线性的 CSS fade，而是从以下三个经典的物理电子显示设备中提炼质感：

```
┌────────────────────────────────────────────────────────────────────────┐
│                        物理世界三大电子显示原型                          │
├────────────────────────────────┬───────────────────────────────────────┤
│ 1. 真空荧光屏 (VFD Display)    │ 极高对比度、冷青/翠绿荧光、通电瞬间点亮、     │
│    经典金融柜台与工业仪器      │ 断电后荧光粉自然指数衰减熄灭。                │
├────────────────────────────────┼───────────────────────────────────────┤
│ 2. 电光辉光管 (Nixie Tube)     │ 惰性气体通电激发、字符轮廓周围带有淡淡的      │
│    早期雷达与核电站遥测控制屏  │ 离子雾化辉光（Halo Glow）、瞬间点火跃迁。     │
├────────────────────────────────┼───────────────────────────────────────┤
│ 3. 交易所电光点阵屏 (Big Board)│ 远距离可视、整齐划一的等宽单色栅格、          │
│    NYSE / 港交所交易大厅巨屏   │ 局部数字跳动刷新、只有变化的数字单元格高亮。  │
└────────────────────────────────┴───────────────────────────────────────┘
```

---

## 三、 通电激发与荧光指数衰减模型（Phosphor Glow & Decay Physics）

现实物理世界中，电子管通电并不是线性的，而是**“脉冲式急速拉升（Excitation Attack）$\to$ 荧光粉粒子受激辐射 $\to$ 指数半衰期自然熄灭（Exponential Decay）”**。

```
亮度 (Luminance)
  100% ┌─┐ (0~25ms 急速冲击峰值)
       │  ╲
   70% │    ╲
       │      ╲  (指数级自然平滑衰减，半衰期 τ ≈ 200ms)
   30% │        ╲
       │          ╲___
    0% └──────────────────── 时间 (t)
       0ms     300ms   800ms
```

### 1. 物理贝塞尔衰减曲线设计
```css
:root {
  /* 物理激发与衰减曲线：前快后缓的受激辐射模拟 */
  --ease-phosphor: cubic-bezier(0.08, 0.82, 0.17, 1);
}
```

### 2. 通电脉冲（Flash Impulse）动画定义
摒弃目前生硬的 `linear` 或普通 `ease-out`，在 `index.css` 中升级为带微弱表面泛光的物理管激发动画：

```css
/* 涨：阳极荧光绿 (Phosphor Green Excitation) */
@keyframes phosphor-tick-up {
  0% {
    background-color: rgba(46, 230, 168, 0.28);
    box-shadow: 0 0 12px rgba(46, 230, 168, 0.35), inset 0 0 4px rgba(46, 230, 168, 0.25);
    color: #ffffff;
  }
  15% {
    background-color: rgba(46, 230, 168, 0.22);
    box-shadow: 0 0 8px rgba(46, 230, 168, 0.20);
  }
  100% {
    background-color: transparent;
    box-shadow: none;
    color: inherit;
  }
}

/* 跌：阴极放电红 (Neon Discharge Red Excitation) */
@keyframes phosphor-tick-down {
  0% {
    background-color: rgba(255, 93, 108, 0.28);
    box-shadow: 0 0 12px rgba(255, 93, 108, 0.35), inset 0 0 4px rgba(255, 93, 108, 0.25);
    color: #ffffff;
  }
  15% {
    background-color: rgba(255, 93, 108, 0.22);
    box-shadow: 0 0 8px rgba(255, 93, 108, 0.20);
  }
  100% {
    background-color: transparent;
    box-shadow: none;
    color: inherit;
  }
}

.tick-flash-up {
  animation: phosphor-tick-up 850ms var(--ease-phosphor) forwards;
}

.tick-flash-down {
  animation: phosphor-tick-down 850ms var(--ease-phosphor) forwards;
}
```

---

## 四、 小数点跳动与单元格动态发光规范（Cell & Digit Dynamics）

### 1. 避免“整行大晃动”，聚焦“局部数值单元格”
物理大屏最经典的体验是：**只有价格那个小框框在通电闪亮，其他静态文字稳如磐石**。
* **规则**：严禁给整个 `<tr className="tick-flash">` 或整个卡片加闪烁。
* **做法**：闪烁只施加在 `.mono.price-cell` 或 `.mono.pnl-badge` 这种叶子节点上。

### 2. 小数点与末位跳动（Sub-Pip Excitation）
高频行情变动时，往往是千分位或百分位跳动。
* 当价格由 `4.524` 变成 `4.538` 时，数字基底背景瞬时被点亮；
* 配合 OpenType `font-feature-settings: 'tnum' 1, 'zero' 1`，保证字符宽度严格固定，闪烁时数字绝对不产生横向挤压抖动（Zero Horizontal Jitter）。

---

## 五、 现有 UI 架构下的零破坏（Zero-Disruption）嵌入点

完全不需要重写任何页面骨架，仅在现有组件的数值节点注入物理质感：

### 1. 顶栏行情条 (`MarketTicker.tsx`)
* **现状**：目前已有 600ms 背景变色，但缺少物理光晕。
* **升级**：直接将 CSS 类切换为 `.tick-flash-up` 与 `.tick-flash-down`，维持 650ms `setTimeout` 清空机制不变，视觉质感瞬间从普通网页变色升华为“物理行情牌通电”。

### 2. 概览 PnL 盈亏数字 (`Overview.tsx` $\to$ `PnLZone.tsx`)
* **现状**：账户今日盈亏（Day PnL）与总资产随每次 15s 轮询拉取静默替换。
* **升级**：通过一个极轻量的 Hook `useValueFlash(currentValue)` 比较数值：
  - 若资金上涨，数字背景浮起翠绿色微光晕并缓缓淡出；
  - 若资金产生回撤，浮起红色微光晕提醒监视者。

### 3. 持仓监控台 (`TradesPositions.tsx`)
* **现状**：已平仓和持仓表格的未实现盈亏列（Unrealized PnL）。
* **升级**：仅对当前产生 Tick 变动的活跃行进行 `cell-flash` 激活。

---

## 六、 CSS 硬件加速实现与低开销规范

交易终端常常数天不关，物理动效必须具备极佳的能耗控制：

1. **启用独立的合成层（Hardware Compositing Layer）**：
   ```css
   .tick-flash-up, .tick-flash-down {
     will-change: background-color, box-shadow, color;
     transform: translateZ(0); /* 强制提权至独立 GPU 合成图层 */
   }
   ```
   避免动效引起全树的重排（Reflow），每次价格闪烁只消耗 GPU 显存片段合成，CPU 占用率接近 0%。

2. **动效无障碍硬屏蔽（Accessibility Fallback）**：
   ```css
   @media (prefers-reduced-motion: reduce) {
     .tick-flash-up, .tick-flash-down {
       animation: none !important;
       box-shadow: none !important;
     }
   }
   ```
   若操作员系统设置了“减弱动效”，所有物理通电辉光自动转为无动画的静态色阶。

---

*（本文档已完整保存在本地：`docs/design/frontend/architecture/observer_mission_control_and_physical_telemetry_design.md`，确立了 Aletheia 作为“自主 AI 任务控制中心”在排版稳定前提下的物理遥测审美标准）*
