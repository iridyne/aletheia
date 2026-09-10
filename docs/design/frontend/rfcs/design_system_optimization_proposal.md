# Aletheia 交易终端设计系统演进与优化方案

> **状态**：优化建议草案（Design System RFC）  
> **目标**：在保持“No Fluff（无废话）、高信息密度、语义诚实”核心原则的前提下，对当前设计系统的 Token 架构、组件原语、无障碍规范与工程治理体系进行系统性升级。

---

## 目录
1. [现状诊断与演进愿景](#一-现状诊断与演进愿景)
2. [第一步：三层设计令牌（Design Token）体系升级](#二-第一步三层设计令牌design-token体系升级)
3. [第二步：原子组件库（UI Primitives）生态补齐](#三-第二步原子组件库ui-primitives生态补齐)
4. [第三步：暗黑对比度、色弱安全与触觉反馈](#四-第三步暗黑对比度色弱安全与触觉反馈)
5. [第四步：统一响应式栅格与布局系统（Layout & Breakpoints）](#五-第四步统一响应式栅格与布局系统layout--breakpoints)
6. [第五步：设计系统自动化门禁与工程治理（Governance）](#六-第五步设计系统自动化门禁与工程治理governance)
7. [分阶段平滑迁移路线图](#七-分阶段平滑迁移路线图)

---

## 一、 现状诊断与演进愿景

### 1. 当前优势（保留项）
* **终端定位明确**：确立了 Bloomberg 式的高密度信息呈现哲学（4px 基础步进、9 阶离散字号、全数据等宽 `tnum`）。
* **单一饱和交互色**：严格遵守 Signal Cyan (`#22d3ee`) 作为全站唯一饱和交互色，红/黄/绿纯粹保留给状态语义。
* **无 Preflight 的纯净集成**：避免了 Tailwind 对关键列表样式的侵入性重置。

### 2. 当前核心痛点（破局点）
* **扁平的单层 Token**：`:root` 中定义的颜色 Token 缺乏“语义层”。组件直接引用 `--surface` 或 `--border`，缺乏 Surface Elevation（卡片表面、悬浮表面、按压表面、模态顶层）的抽象。
* **表单输入原语缺失**：只有 `Switch` 与 `Button` 做了组件封装，全站所有的 `<input>`、`<select>`、`<textarea>` 均分散裸写在 13 个页面中，产生数十处样式重复与非标行内样式。
* **浮层生态断层**：缺乏标准的 `Tooltip`、`Popover`、`Dropdown` 基础组件，导致 `Charts.tsx` 出现 `-9999px` 负坐标黑客，以及依赖浏览器默认 `title` 属性（有 2 秒悬浮延迟、无法换行且移动端失效）。
* **治理与检查手段滞后**：目前仅有 `cssTokens.test.ts` 检查变量是否存在，但缺乏阻止开发者在 JSX 中直接写死 Hex 颜色或静态 `style={{}}` 的静态 Lint 拦截。

---

## 二、 第一步：三层设计令牌（Design Token）体系升级

建立工业标准的 **Global $\to$ Semantic $\to$ Component** 三层 Token 体系：

```
[Global Tokens]       Primitive 色盘、物理数值 (固定常量，不可业务化)
      ↓
[Semantic Tokens]     按用途赋予语义: --surface-card, --surface-raised, --text-muted
      ↓
[Component Tokens]    具体组件映射: --table-header-bg, --switch-track-bg
```

### 1. 语义化 Surface 阶梯升级
针对暗黑终端在普通屏幕或护眼模式下层级不分明的问题，拉大阶梯并统一语义：

```css
:root {
  /* 基础物理底色 (Global) */
  --slate-950: #0a0e12;
  --slate-900: #0f141a;
  --slate-850: #141a21;
  --slate-800: #1a222c;
  --slate-750: #222c3a;

  /* 语义化表面 (Semantic Surface) */
  --bg-app:        var(--slate-950); /* 应用基底 (近黑冷色) */
  --bg-recessed:   var(--slate-900); /* 嵌入区域 (主内容区) */
  --surface-base:  var(--slate-850); /* 卡片、边栏默认背景 */
  --surface-raised:var(--slate-800); /* 悬浮态、二级面板、输入框背景 (明度调高提升辨识度) */
  --surface-overlay:var(--slate-750);/* 模态弹窗、右侧抽屉、下拉菜单 (最高层级) */

  /* 边框层级 (Semantic Borders) */
  --border-subtle: #1e2631; /* 弱分隔线、输入框默认边框 */
  --border-default:#263240; /* 卡片边框、标准表格线 */
  --border-strong: #3b495c; /* 表头分割线、高亮卡片边框 */
  --border-focus:  var(--accent); /* 获得键盘焦点的边框高亮 */
}
```

### 2. 动效与过渡规范化
```css
:root {
  --duration-instant: 80ms;  /* 按钮按下、Tab 切换 */
  --duration-fast:    150ms; /* 悬浮淡入、下拉菜单展开 */
  --duration-normal:  250ms; /* 抽屉滑出、模态对话框淡入 */
  --ease-out:         cubic-bezier(0.16, 1, 0.3, 1); /* 自然物理回弹减速曲线 */
}
```

---

## 三、 第二步：原子组件库（UI Primitives）生态补齐

当前 `components/ui/` 仅有 5 个基础组件。亟需补齐以下 4 个高频交易原语：

### 1. 标准表单输入原语 (`Input`, `Select`, `Textarea`)
* **痛点**：全站散落 80 余处 `style={{ width: '80px' }}` 或直接对原生 input 赋予不同 class。
* **规范方案**：
  ```tsx
  // components/ui/Input.tsx
  interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
    mono?: boolean;       // 默认 true，所有数字/代码类输入强制等宽
    size?: 'sm' | 'base'; // sm: 26px (紧凑), base: 32px (标准)
    state?: 'default' | 'error' | 'warning';
    unit?: string;        // 内嵌右侧单位 (e.g. "USDT", "min", "%")
  }
  ```
  内置内联单位渲染，消除外层用 flex 拼装 span 的冗余代码。

### 2. 即时信息提示原语 (`Tooltip`)
* **痛点**：原生 `title` 属性存在约 1.5s 延迟，排版不可控，在触屏上无法显示。
* **规范方案**：
  基于轻量无依赖的 Floating UI 或原生 HTML `popover`，封装零延迟或 200ms 微延迟的 `<Tooltip content="...">`，自带暗黑阴影与高对比文本。

### 3. 浮层与下拉菜单原语 (`Popover` / `Dropdown`)
* **痛点**：`Charts.tsx` 依赖负 9999px 定位隐藏下拉菜单，时常脱节。
* **规范方案**：
  封装 `<Popover trigger={<button />} content={<Menu />} />`，彻底废除 `-9999px` 黑客式隐藏，未激活时从 DOM 条件卸载。

### 4. 骨架占位原语 (`Skeleton`)
* **痛点**：各页面自己用 CSS 拼 `.skeleton-bar`，高度和圆角各异。
* **规范方案**：
  提供统一的 `<Skeleton variant="text" | "rect" | "circle" width="..." height="..." />`，自带全局统一的 `boot-shimmer` 渐变动画，并天然支持 `prefers-reduced-motion` 静止降级。

---

## 四、 第三步：暗黑对比度、色弱安全与触觉反馈

### 1. 消除 OLED / 夜间模式下的暗态粘连
* **现状**：当前 `--surface` (`#141a21`) 与 `--surface-2` (`#1a212a`) 在普通显示器或护眼模式下明度差极小（仅 4%），导致输入框和按钮与卡片背景融为一体。
* **改进**：将二级悬浮/输入框背景提升至 `#1e2631`，并在所有交互元素获得焦点或悬停时，提供 1px 细边框物理强化：
  ```css
  .input:hover, .btn-secondary:hover {
    border-color: var(--border-strong);
  }
  ```

### 2. 动效无障碍（Motion Accessibility）
在 `index.css` 顶层统一捕获媒体查询，彻底消除高频闪烁对交易员的疲劳困扰：
```css
@media (prefers-reduced-motion: reduce) {
  *, ::before, ::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
  .tick-flash-up, .tick-flash-down {
    animation: none !important;
  }
}
```

### 3. 色弱友好与双重通道确认
* **铁律**：除了红/绿背景外，所有盈亏与状态必须伴随符号或文字（如 `+`、`-`、箭头图标或文本状态）。
* 当前 `pnlCell` 已经做到了 `+`/`-` 显式标出，这一模式应推广至所有的状态指示点（StatusDot 带有专属形状，或配合 StatusText 文字输出）。

---

## 四、 第四步：统一响应式栅格与布局系统（Layout & Breakpoints）

### 1. 断点令牌标准化
废除全站硬编码的 `@media (max-width: 700px)`，统一下发标准化断点：
```css
/* 移动端: <= 640px | 平板/小笔记: 641px - 1024px | 桌面: 1025px - 1440px | 宽屏: > 1440px */
--breakpoint-sm: 640px;
--breakpoint-md: 768px;
--breakpoint-lg: 1024px;
--breakpoint-xl: 1280px;
```

### 2. 全站表格吸顶表头规范（Sticky Header Contract）
在 `components/DataTable.tsx` 与全局样式中固化：
```css
.dt-table-wrapper {
  max-height: calc(100vh - 240px);
  overflow-y: auto;
}
.dt-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface-base);
  box-shadow: 0 1px 0 var(--border-default);
}
```
保证无论数据行如何翻滚，表头字段说明永久对齐。

---

## 五、 第五步：设计系统自动化门禁与工程治理（Governance）

依靠人工代码审查无法根绝行内样式的死灰复燃，必须建立静态防护网：

### 1. 静态样式审查扩展 (`scripts/check-design-lint.mjs`)
在 `npm run lint` 阶段增加轻量 AST 校验（或利用 `ast-grep` 规则）：
1. **禁止 JSX 静态行内样式**：对 `<div style={{ width: '80px' }}>` 报警告，提示改用 Tailwind 原子类；
2. **禁止散落 Hex 色值**：检查 `.tsx` 文件中是否出现 `#ff5d6c` 等裸色，强制要求引用 CSS 变量或主题类；
3. **强制 `useMutation` 绑定错误反馈**：确保每个异步突变都有 `onError` 提示，杜绝静默失败。

### 2. 契约驱动的图标规范
统一全站图标库为 `lucide-react`，严禁在页面中混用手写的内联 `<svg>` 或特定字符圆点（如 `●`、`◈`、`⚠`）。所有状态图标通过统一的 `<StatusIcon state="..." />` 派发。

---

## 六、 分阶段平滑迁移路线图（Milestones）

| 阶段 | 实施内容 | 风险等级 | 影响范围 |
|---|---|---|---|
| **Phase 1: 零破坏体验抛光** | • 表格表头增加 `position: sticky`<br>• Overview 右轨增加 `position: sticky`<br>• Topbar 增加 `⌘K` 搜索快捷胶囊<br>• CSS 注入 `prefers-reduced-motion` | 🟢 极低（纯 CSS 与外围组件） | 极大提升日常看盘、台账查阅体验 |
| **Phase 2: 消除核心坏味道** | • 修复 `ActivityTimeline` 无保护的 `JSON.parse`<br>• `Charts.tsx` 消除 `-9999px` 负坐标，改为条件渲染<br>• Settings 35+ 处 `as any` 接入生成的 `settings.d.ts` | 🟡 中等（代码结构清洗） | 消除潜在崩溃，恢复 TypeScript 编译器保障 |
| **Phase 3: 基础原语生态补齐** | • 封装统一的 `<Input>` / `<Select>` 原语，批量替换全站 80+ 处硬编码行内样式<br>• 封装轻量 `<Tooltip>` / `<Popover>`，规范悬浮说明与菜单 | 🟡 中等（样式类重构） | 消除样式重复，统一交互触感 |
| **Phase 4: 令牌分层与静态守护** | • 升级 `:root` 为三层 Token（引入 `--surface-raised` 等）<br>• 将静态样式与未定义 Token 扫描集成至 `npm run lint` CI 流程 | 🔴 较高（全局调色微调） | 完成设计系统正规化，防止历史债务回潮 |

---

*（本文档已保存至：`docs/design/frontend/rfcs/design_system_optimization_proposal.md`，可作为后续前端设计系统迭代的指导技术方案）*
