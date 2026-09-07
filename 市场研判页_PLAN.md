# 市场研判页（Charts）1:1 深度对齐 R20 专业指标工作台与 Overview 终端视觉更新执行计划

## Context
用户要求基于参考项目 R20（`/Users/yixian/Projects/references/r20-quantum-trader`，已通过 `git pull` 更新至最新提交 `bb2ee77`）及提供的截图（Image #1），将 Aletheia 市场研判页（`frontend/src/pages/Charts.tsx`）的图表工具栏与技术指标系统进行 1:1 深度复刻与重构。
同时，用户要求对应更新 Overview 页面的 TV/图表相关图标与视觉标识，彻底去除陈旧的 TradingView/第三方割裂感，实现统一高信噪比的极客操盘终端视觉。

---

## Approach

### 1. 技术指标数学模型层扩展（`frontend/src/charts/priceChartModel.ts`）
- **保留现有已实现指标**：`calculateEma` (EMA 20/50)、`calculateBollingerBands` (BOLL 20/2)、`calculateMacd` (MACD 12/26/9)、`calculateRsi` (RSI 14)、`calculateStochastic` (KDJ 9/3/3)、`calculateAtr` (ATR 14)。
- **新增 R20 对齐的技术指标纯函数**（无副作用、确定性计算）：
  - **`calculateVwap(bars: Bar[]): IndicatorPoint[]`**：
    日内/当前窗口成交量加权平均价。典型价 $TP = (H + L + C) / 3$；累加 $\sum (TP \times V) / \sum V$。
  - **`calculateSma(bars: Bar[], period: number): IndicatorPoint[]`**：
    简单移动平均线（用于 MA: 5, 10, 20）。
  - **`calculateBbi(bars: Bar[]): IndicatorPoint[]`**：
    多空综合均线 $BBI = (MA(3) + MA(6) + MA(12) + MA(24)) / 4$。
  - **`calculateSar(bars: Bar[], step = 0.02, maxStep = 0.2): IndicatorPoint[]`**：
    标准抛物线转向指标（Parabolic Stop and Reverse），维护趋势方向、极值点（EP）与加速因子（AF）。
  - **`calculateObv(bars: Bar[]): IndicatorPoint[]`**：
    能量潮指标（On-Balance Volume），以收盘价涨跌符号累加真实成交量。
  - **`calculateWr(bars: Bar[], period = 14): IndicatorPoint[]`**：
    威廉指标（Williams %R），$WR = \frac{H_{14} - C}{H_{14} - L_{14}} \times (-100)$。
- **扩展指标类型定义与默认值**：
  - `MainIndicatorId` 扩展为 `'vwap' | 'ma' | 'ema20' | 'ema50' | 'bollinger' | 'sar' | 'bbi'`；
  - `SubchartIndicatorId` 扩展为 `'vol' | 'macd' | 'rsi' | 'stochastic' | 'obv' | 'wr' | 'atr'`；
  - `MAX_INDICATORS_PER_ZONE` 提升至 6，支持主副图多指标自由共存。

### 2. 图表画布与系列渲染挂载（`frontend/src/charts/PriceChart.tsx`）
- **主图叠加系列扩展**：
  - 新增 `vwapRef` 系列（颜色 `#06B6D4` 青色，线宽 1.5）；
  - 新增 `maRef` 均线系列（颜色 `#F59E0B` 琥珀色，线宽 1.2）；
  - 新增 `sarRef` 系列（散点或阶梯折线，颜色 `#EC4899` 粉色）；
  - 新增 `bbiRef` 系列（颜色 `#10B981` 翠绿色，线宽 1.2）；
  - 维持原有的 `ema20Ref` (`#38BDF8`)、`ema50Ref` (`#818CF8`) 与 `bbUpperRef` / `bbMiddleRef` / `bbLowerRef`（布林带三轨）。
- **`applyMainIndicators` 批量同步更新**：
  - 根据 `settings.indicators.main` 动态执行计算并 `setData`；未启用的指标传递空数组清空画布，无需销毁/重建整个 Lightweight Chart 实例。
- **副图独立窗口管理**：
  - `vol`：受 `settings.overlays.volume` 控制，高亮渲染成交量柱状图及买卖色阶；
  - `macd`、`rsi`、`stochastic`（KDJ）、`obv`、`wr`：在副图区域动态创建/复用对应指标面板。
- **属性与引擎语义清洗**：
  - 将 `data-chart-engine="tradingview-lite"` 清洗为 `data-chart-engine="terminal-workbench"`，消除外部品牌残留。

### 3. 图表顶栏 1:1 复刻 R20 指标工作台交互（`frontend/src/pages/Charts.tsx`）
- **指标工作台下拉菜单（Indicators Dropdown & Popover）**：
  - **触发按钮**：位于周期切换栏右侧，包含滑块图标 `<SlidersHorizontal className="w-3 h-3" />` + 文本 `指标` + 当前激活指标数量胶囊徽标（`<span className="indicator-badge">{activeCount}</span>`，当计数 > 0 时呈半透明翠绿微光高亮）+ 旋转折叠箭头 `<ChevronDown />`。
  - **悬浮面板（Floating Popover）**：点击弹出，宽度 `288px (w-72)`，暗色磨砂玻璃背景（`backdrop-blur-md`），分为两大标准板块：
    1. **主图叠加指标（Main Chart Overlays）**：
       - 副标题淡灰提示：`主蜡烛同屏`；
       - 双列网格紧凑按钮：`VWAP`（青点 `#06B6D4`）、`MA`（橙点 `#F59E0B`）、`EMA`（天蓝点 `#38BDF8`）、`BOLL`（靛蓝点 `#818CF8`）、`SAR`（粉点 `#EC4899`）、`BBI`（绿点 `#10B981`）；
       - 激活态：卡片高亮背景，右侧显式显示翠绿色对勾 `<Check />`。
    2. **副图震荡指标（Sub-Window Panes）**：
       - 副标题淡灰提示：`独立高宽窗口`；
       - 双列网格紧凑按钮：`VOL`（绿点 `#10B981`）、`MACD`（蓝点 `#3B82F6`）、`RSI`（橙点 `#F97316`）、`KDJ`（紫点 `#A855F7`）、`OBV`（黄点 `#EAB308`）、`WR`（靛青点 `#6366F1`）；
       - 激活态：卡片高亮背景，右侧显式显示翠绿色对勾 `<Check />`。
  - **默认初始状态（对齐 R20 黄金组合）**：默认激活 `VWAP` + `VOL`（激活指示徽标为 `2`），操作员可一键自由增减叠加。
  - **无障碍与边界处理**：绑定 `onClickOutside` 监听与 `Escape` 键盘事件，点击外部或按下 Esc 立即关闭下拉弹窗。
- **K 线结线实时倒计时（Candle Countdown）**：
  - 在顶栏显著位置渲染 `当前蜡烛线倒计时: <span className="countdown-timer">{candleCountdown}</span>`（琥珀黄色等宽字体）。
  - 根据当前选中的周期（`1m`, `5m`, `15m`, `1h`, `4h`, `1d`）与本地时钟毫秒差，通过 `setInterval(..., 1000)` 每秒高精更新剩余分秒，在结线瞬间自动归零并触发下一周期重置。
- **调价平移试算模式（`试算`）**：
  - 保留并对齐 R20 的 `<button className="btn-simulate">{simMode ? '退出' : '试算'}</button>` 开关；
  - 开启试算时允许在图表上拖拽/平移止损止盈参考线，并在顶部浮出当前模拟盈亏比 $R:R$ 变动读数。
- **刷新动作（`🔄`）**：
  - 右侧提供紧凑的快速刷新图标按钮，点击重新获取最新 OHLCV 与深度快照，加载期间呈现 CSS `animate-spin`。

### 4. Overview 页面及全局图表图标/标识同步更新（`frontend/src/pages/Overview.tsx` & `routeManifest.ts`）
- **路由与侧边栏图标对齐**：
  - 校验 `routeManifest.ts` 中 Markets 的导航图标（当前为 `ChartCandlestick`），对齐终端量化操盘站属性，确保与 Overview 及其他模块的视觉语言高度统一。
- **Overview 页面的行情与图表入口图标升级**：
  - 检查 Overview 页面中所有跳转至 `/charts` 的符号链接（`SymbolLink`）、资产持仓穿透行以及 PnL 资产曲线头部；
  - 若存在历史遗留的 TV 徽标或外链标志，统一替换为简洁的机构级终端 K 线图标（如 `CandlestickChart` 或微型蜡烛图 SVG，搭配 `Inspect on Markets` 语义化 title），杜绝外部第三方商标感。

### 5. 样式规范（`frontend/src/index.css`）
- 注入 `.indicator-dropdown-container`、`.indicator-dropdown-menu`、`.indicator-group-header`、`.indicator-toggle-btn`、`.indicator-badge`、`.candle-countdown` 等高信噪比金融终端样式类，全面复用已有的 CSS Token（`--border`、`--surface-card`、`--accent`、`--text`）。

---

## Critical files & anchors

1. `frontend/src/charts/priceChartModel.ts`
   - 新增 `calculateVwap`、`calculateSma`、`calculateSar`、`calculateBbi`、`calculateObv`、`calculateWr`；
   - 扩展 `MainIndicatorId` 与 `SubchartIndicatorId`。
2. `frontend/src/charts/PriceChart.tsx`
   - 在 `useEffect` 与 `applyMainIndicators` 中挂载 VWAP、MA、SAR、BBI 等新系列；
   - 清理 `data-chart-engine` 属性，统一引擎标识。
3. `frontend/src/pages/Charts.tsx`
   - 重构 `market-chart-toolbar`：集成 `指标 2 ^` 下拉面板、倒计时组件与 `试算` 动作条；
   - 维护 `activeIndicators` 状态与切换逻辑。
4. `frontend/src/pages/Overview.tsx` & `frontend/src/components/ui/SymbolLink.tsx`
   - 统一图表穿透链接与 TV 图标为现代终端 K 线图标与统一无障碍提示。
5. `frontend/src/index.css`
   - 新增工作台下拉面板及指标小圆点、对勾、徽标的 CSS 规则。
6. `frontend/src/pages/Charts.test.tsx`
   - 补充指标工作台点击展开、主副图指标状态切换、倒计时渲染及键盘关闭的自动化测试。

---

## Verification

1. **指标工作台点击与视觉对齐验证**：
   - 启动前端服务，进入 `/charts` 市场研判页；
   - 确认图表顶栏呈现 `指标 2 ^` 按钮（默认激活 `VWAP` 与 `VOL`）；
   - 点击该按钮，确认无缝弹出 288px 磨砂暗色面板，完美呈现“主图叠加指标（6 项）”与“副图震荡指标（6 项）”；
   - 逐一点击 `BOLL`、`MA`、`MACD` 等按钮，确认右侧对勾亮起，顶部按钮的徽标数字实时从 `2` 递增变动；
   - 观察主蜡烛图上，布林轨道、VWAP 等均线即时绘制并跟随 K 线更新。
2. **K 线结线倒计时动态验证**：
   - 切换周期至 `15m`，观察倒计时读数以秒级平滑递减（如 `14:59` $\to$ `14:58`）；
   - 切换至 `1h` 或 `4h`，确认倒计时准确反映距下一根整点小时蜡烛的结线时间。
3. **Overview 页面图标与跳转验证**：
   - 打开 Overview 页面，检查资产分布与穿透入口，确认图标展示与跳转至 `/charts?symbol=...` 正常运作，无 TradingView 历史遗留残留。
4. **点击外部与全键盘交互验证**：
   - 打开指标面板后，点击图表区域或按下 `Escape` 键，面板立即关闭；
   - 键盘按下 `Tab` 与 `Enter`，各指标按钮可无障碍获得焦点并触发开关。
5. **回归测试与生产打包**：
   - 运行 `cd frontend && npm test`，确保 84 套件全部通过；
   - 运行 `npm run build`，确保 TypeScript 检查 0 错误，Brotli 打包体积不超标。

---

## Assumptions & contingencies

- **假设 1**：继续基于当前经过性能深度打磨的 `lightweight-charts` 引擎承载各项指标折线与柱状图渲染，而不是推倒重构更换为 `klinecharts` 底层。
  - *理由*：Aletheia 独有的 AI Thesis Envelope 筹码包络、48-bin 真实水平 Volume Profile 直方图以及 Entry Plan 挂单线均与当前引擎深度绑定。直接复用引擎并扩展指标算法，既能 100% 达成 R20 的顶层交互与视觉效果，又避免破坏现有的风控与因果回放能力。
- **假设 2**：默认激活的黄金指标组合为 `VWAP` + `VOL`（即激活徽标为 `2`）。
  - *回退预案*：若操作员有个人偏好，支持将选中的指标组合保存至 `localStorage`，下次进入时自动恢复。
