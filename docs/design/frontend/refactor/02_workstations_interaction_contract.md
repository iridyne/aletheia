# Workstations Information Architecture & Interaction Contract (四大工作空间交互契约)

> **契约目标**：规范 13 个旧路由如何无缝收敛至 4 大核心工作空间，明确每个工作空间的数据投影、组件排版、快捷键流转与跨页面穿透规范。

---

## 一、 路由收敛对照与重定向映射表（Route Mapping）

| 新工作空间 (Workstation) | 对应主路由 | 融合/收敛的原页面路由 | 前端平滑重定向规则 |
| :--- | :--- | :--- | :--- |
| **1. Mission Control**<br>*(任务控制中心)* | `/` (或 `/mission`) | • `/overview`<br>• `/risk`<br>• `/command-center` | 访问 `/overview`、`/risk`、`/command-center` 自动高亮对应主卡片/抽屉，不跳转新页 |
| **2. Alpha & Entry**<br>*(研判与入场工作台)* | `/workbench` | • `/universe`<br>• `/charts`<br>• `/prediction-markets`<br>• `/serenity-lens` | 访问 `/universe`、`/charts`、`/prediction-markets` 自动切换工作台顶部的 Tab |
| **3. Execution Blotter**<br>*(交易执行与台账)* | `/blotter` | • `/trades`<br>• `/paper-trades`<br>• `/positions`<br>• `/blotter` | 访问 `/trades`、`/positions` 直接映射到 Blotter 的不同清算视角 |
| **4. System Governance**<br>*(系统治理与配置)* | `/governance` | • `/learning`<br>• `/usage`<br>• `/settings`<br>• `/news` | 访问 `/settings` 等自动打开 Governance 对应的配置中心面板 |

---

## 二、 工作空间交互与数据连贯性契约

### 1. Mission Control（主控台）交互规范
- **布局比例**：左侧 35%（资产净值、PnL 趋势、活动仓位摘要），中间 40%（Agent 实时活动与思考推演流），右侧 25%（Command Center 命令行交互 + 风险快速制动开关）。
- **即时响应**：资产与 PnL 数字通过 L2 事件流秒级原地跳动变色，不需要全屏骨架屏重新加载。

### 2. Alpha & Entry Workbench（入场工作台）交互规范
- **两栏联动**：
  - **左侧**：候选标的池（Candidate Table）与入场计划（Entry Plans）。
  - **右侧**：所选中币种的深度研判（Candlestick K线 + 48 分箱 Volume Profile + 交易所真实挂单深度）。
- **标的秒级穿透**：左侧点击任意币种行，右侧图表与微观指标在 **50ms 内完成数据切流**，中间保持视图不白屏。

### 3. Execution & Ledger Blotter（交易台账）交互规范
- **高密度金融表**：对标 Bloomberg EMSX，每一列支持等宽数字右对齐。
- **因果链一键下钻**：任意一笔成交记录点击后，右侧滑出抽屉，完整投影其上游触发点：
  $$\text{Opportunity (AI 机会)} \to \text{Entry Plan (计划)} \to \text{Risk Check (风控放行)} \to \text{Order (报单)} \to \text{Fill (成交)}$$

---

## 三、 全局顶栏与快捷键操作规范（Global Chrome & Keybindings）

1. **顶栏常驻核心体征**：
   - `MarketTicker`：主流标的行情跳动。
   - `DriftUpdatePill`：检测到后端代码升级时显示琥珀色胶囊，点击无缝热重启。
   - `NotificationCenter`：常驻铃铛图标，未读红点显示未恢复的健康告警。
   - `StatusCluster`：展示系统总控状态（Master State）与网络连接质量。
2. **专业键盘快捷键**：
   - `⌘K` / `Ctrl+K`：呼出全局指令控制面板（Command Palette）。
   - `⌘Shift+P` / `Ctrl+Shift+P`：一键开启/关闭隐私脱敏模式（模糊化所有余额与具体法币数值，适合演示与录屏）。
   - `1` ~ `4`：在顶层未处于输入框时，数字键秒级切换 4 大工作空间。
