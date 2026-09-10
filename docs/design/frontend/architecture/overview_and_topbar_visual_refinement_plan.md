# Aletheia 顶栏与 Overview 驾驶舱视觉重构与后续优化实施计划

> **文档背景**：根据操作员实际运行截屏（`Image #1`），针对顶栏按钮样式穿透、Agent Activity 动态信息流背景发光与文字粘连等严重视觉降级问题，进行像素级缺陷归因分析，并制定立即可实施的修复代码与分阶段优化演进计划。  
> **核心原则**：回归专业金融终端（Bloomberg / TradingView）冷峻、克制、无荧光污染的硬核美学；严禁非受控默认样式污染。

---

## 目录
1. [截屏缺陷像素级诊断清单（Defect Analysis）](#一-截屏缺陷像素级诊断清单defect-analysis)
2. [根本技术原因溯源（Root Cause Analysis）](#二-根本技术原因溯源root-cause-analysis)
3. [第一阶段：立即可实施的样式修复方案（Immediate CSS Fixes）](#三-第一阶段立即可实施的样式修复方案immediate-css-fixes)
4. [第二阶段：Agent Activity 信息流视觉升维（Feed Typography Upgrade）](#四-第二阶段agent-activity-信息流视觉升维feed-typography-upgrade)
5. [第三阶段：顶栏（Topbar）统一网格与交互规范（Topbar Alignment）](#五-第三阶段顶栏topbar统一网格与交互规范topbar-alignment)
6. [第四阶段：Overview 终态 3 列式控制台重构（Overview 3-Column Docking）](#六-第四阶段overview-终态-3-列式控制台重构overview-3-column-docking)

---

## 一、 截屏缺陷像素级诊断清单（Defect Analysis）

从操作员截屏中捕捉到的关键缺陷如下：

### 1. 顶栏按钮样式被全局 Button 污染（Cyan Block Pollution）
- **现象**：顶栏右侧的 `⬇ Dump`、眼睛图标（`[Eye]` 隐私打码开关）和铃铛图标（`[Bell]` 通知中心）均渲染为刺眼的**高亮纯电青色块（Electric Cyan `#22d3ee`）配黑色文字**。
- **严重度**：高。犹如在暗黑驾驶舱内点亮了 3 盏刺眼的探照灯，视觉重心完全被无意义的辅助按钮掠夺，破坏了全屏暗黑沉浸感。
- **与相邻元素脱节**：左侧的 `⌘K` 按钮拥有沉静暗色背景（`.command-pill`），而紧挨着的 3 个按钮却是荧光方块；更右侧的状态集群又是暗色胶囊，导致顶栏右侧视觉节奏严重撕裂。

### 2. Agent Activity 动态流整行背景沦为高亮青色（Feed Background Collapse）
- **现象**：左下角 `Agent Activity` 卡片内的每一条活动记录，都被渲染成了一个横贯整个卡片的**纯电青色块**。
- **严重度**：致命。操作员原本期待看到的是一条条清晰、微弱分隔的暗黑文本流，结果整块区域变成了巨大的霓虹色矩阵，可读性彻底降为零。

### 3. Agent 动态标题与 Thesis 正文无空格粘连（Text Collision Bug）
- **现象**：
  - `long ONG/USDTONG/USDT is poised for a potential long opportunity...`
  - `short SOXS/USDTMomentum short on SOXS/USDT...`
  - `long SOXL/USDTMomentum long SOXL/USDT on Binance...`
  - `short XLM/USDTMomentum short on XLM/USDT...`
- **严重度**：高。标的代码（如 `ONG/USDT`）与后续正文开头直接贴合，中间缺少空格与断行，文字堆叠造成严重阅读障碍。

### 4. 状态标签（expired / invalidated）浮动漂移
- **现象**：每行末尾的 `expired` 或 `invalidated` 状态徽标紧跟在超长单行正文最后，由于正文长短不一，导致状态徽标在垂直方向错落散乱，毫无表格列对齐秩序。

---

## 二、 根本技术原因溯源（Root Cause Analysis）

### 1. 全局标签级 CSS 样式的意外泄漏
在 `frontend/src/index.css:1275` 中，定义了全局重置：
```css
button {
  background: var(--accent); color: #04181d; border: none; padding: 0.32rem 0.7rem;
  border-radius: var(--r-sm); cursor: pointer; font-size: 0.76rem; font-weight: 600;
  font-family: var(--sans); ...
}
```
**致命连锁反应**：
1. `.topbar-btn` 只声明了尺寸和 padding，**没有显式声明 `background` 与 `color`**，导致 `⬇ Dump`、`Eye` 和 `Bell` 自动套用了 `button` 的默认样式（即 `background: var(--accent)` 亮青色）。
2. 在 `AgentFeed.tsx:335` 中，每一行活动都是一个 `<button className="feed-row" ...>`，但 `index.css` 中**根本没有任何以 `.feed-row` 开头的 CSS 规则**！这导致每一行活动都被浏览器当成了默认的大青色操作按钮。

### 2. 内联 Span 没有声明 Block / Flex 纵向流
在 `AgentFeed.tsx:341-354` 中：
```tsx
<span className="feed-body">
  <span className="feed-headline">{it.headline}</span>
  {it.detail && <span className="feed-detail">{it.detail}</span>}
</span>
```
因为 `.feed-body`、`.feed-headline`、`.feed-detail` 均为未经 CSS 约束的原生 `<span>`（行内元素），浏览器将其直接拼接在同一行文本节点中，且两个 span 之间在 JSX 中没有任何空格字符，直接导致了 `ONG/USDTONG/USDT` 这类字符串物理碰撞。

---

## 三、 第一阶段：立即可实施的样式修复方案（Immediate CSS Fixes）

### 1. 治本：顶栏按钮全部归于沉静暗态
在 `frontend/src/index.css` 中重写 `.topbar-btn`：
```css
.topbar-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-2);
  height: 28px;
  min-width: 28px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  color: var(--muted);
  font-size: var(--fs-xs);
  cursor: pointer;
  transition: background 80ms, color 80ms, border-color 80ms;
}
.topbar-btn:hover {
  background: var(--surface);
  color: var(--text);
  border-color: var(--border-2);
}
.topbar-btn.active {
  background: rgba(34, 211, 238, 0.12);
  border-color: var(--accent);
  color: var(--accent);
}
```

### 2. 治本：规范 AgentFeed 信息流的网格排版
在 `frontend/src/index.css` 中完整补齐丢失的 `.feed-*` 规则：
```css
/* ---------- Agent Activity Feed ---------- */
.feed-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.feed-row {
  display: grid;
  grid-template-columns: 4rem 1.4rem minmax(0, 1fr) auto;
  gap: var(--space-2);
  align-items: baseline;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: var(--r-sm);
  color: var(--text);
  font-family: var(--sans);
  text-align: left;
  cursor: pointer;
  transition: background 80ms;
}
.feed-row:last-child {
  border-bottom: none;
}
.feed-row:hover {
  background: var(--surface-2);
}
.feed-ts {
  font-size: var(--fs-2xs);
  color: var(--faint);
  font-family: var(--mono);
  white-space: nowrap;
}
.feed-icon {
  font-size: 0.82rem;
  display: flex;
  align-items: center;
  justify-content: center;
}
.feed-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.feed-headline {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.feed-detail {
  font-size: var(--fs-xs);
  color: var(--muted);
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.feed-meta {
  font-size: var(--fs-2xs);
  color: var(--faint);
  font-family: var(--mono);
}
```

---

## 四、 第二阶段：Agent Activity 信息流视觉升维（Feed Typography Upgrade）

### 1. 拆解单行粘连，实现双行阶梯排版
在 `AgentFeed.tsx` 中明确分层：
- **第一行**：`[📐 Entry Plan]` 标的代码（如 `ONG/USDT`，等宽粗体）+ 动作方向（`long`，带色徽标）；
- **第二行**：纯文本 Thesis 简述（`is poised for a potential long opportunity...`，冷灰浅色截断）。
- **右侧列**：绝对右对齐的紧凑状态胶囊（`expired` / `invalidated`）。

### 2. 状态标签色彩与背景沉淀
- `expired` / `invalidated`：采用极低饱和度的暗底 `rgba(125, 137, 151, 0.12)` + 灰色文字，不再与活跃的运行状态争夺注意力。

---

## 五、 第三阶段：顶栏（Topbar）统一网格与交互规范（Topbar Alignment）

在顶栏右侧建立统一的 Action Group 容器，统一度量衡：
1. **统一高度**：所有辅助工具按钮高度严格锁定为 `28px`；
2. **统一视觉层级**：
   - 核心交易态势（行情、时钟）：纯等宽数字呈现；
   - 次级操作（`⌘K` 搜索、`⬇ Dump` 快照、`[Eye]` 隐私、`[Bell]` 通知）：统一为冷灰暗底胶囊；
   - 生产状态集群（`STANDBY`、`Observe`）：统一为深邃暗黑状态卡片。

---

## 六、 第四阶段：Overview 终态 3 列式控制台重构（Overview 3-Column Docking）

*参考原型：`docs/design/frontend/rfcs/prototypes/overview_preview.html`*

彻底突破当前 Overview 垂直单一流布局，在 1440px 宽屏下演进为三大业务列：
1. **左列：核心资产与台账区（40% 宽）**：巨幅净值、当日 PnL 盈亏曲线、未结订单微缩条；
2. **中列：智能体全自主运行流（35% 宽）**：经过上述排版改造的极致沉静 `Agent Activity` 流；
3. **右列：物理风控与连接真理轨（25% 宽，Sticky Rail）**：风控违规告警、网关/调度器连接度、数据落库与新鲜度遥测。

---

*（本文档永久保存在 `docs/design/frontend/architecture/overview_and_topbar_visual_refinement_plan.md`，作为后续专项视觉精细化推进的权威依据）*
