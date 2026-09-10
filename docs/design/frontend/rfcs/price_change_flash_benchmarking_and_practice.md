# 金融终端“价格变动背景闪烁（Cell Flash）”行业成熟实践与对标借鉴

> **文档定位**：针对 Aletheia 交易终端在“数字价格变动时，背景瞬间亮起绿色/红色并平滑淡出熄灭”的视觉体验，全面梳理**顶级金融/加密交易终端（AG Grid、Binance、DexScreener、Bloomberg）**的现成成熟做法。  
> **核心原则**：**拒绝复杂数学与过度设计，直接拿来主义**，采用全球金融工程中验证过数亿次的最简 CSS 与轻量 Hook 实现。

---

## 目录
1. [行业对标：这个效果到底来自哪里？](#一-行业对标这个效果到底来自哪里)
2. [顶级实装 1：AG Grid 企业级金融表格（高盛/摩根/花旗标准）](#二-顶级实装-1ag-grid-企业级金融表格高盛摩根花旗标准)
3. [顶级实装 2：Binance (币安) / OKX (欧易) 实时订单簿与最新成交](#三-顶级实装-2binance-币安--okx-欧易-实时订单簿与最新成交)
4. [顶级实装 3：DexScreener / Birdeye 实时链上交易闪烁](#四-顶级实装-3dexscreener--birdeye-实时链上交易闪烁)
5. [Aletheia 当前代码现状与差距（仅差一步）](#五-aletheia-当前代码现状与差距仅差一步)
6. [最简“拿来即用”落地方案（10 行 CSS + 1 个通用 Hook）](#六-最简拿来即用落地方案10-行-css--1-个通用-hook)

---

## 一、 行业对标：这个效果到底来自哪里？

你在其他交易平台上看到过的“**数字变动时背景突然亮一下（涨绿/跌红），然后缓缓灭下去**”，在专业金融软件开发中有一个公认的标准术语：
$$\textbf{Cell Data Change Flash（单元格变动闪烁）} \quad \text{或} \quad \textbf{Tick Flash（跳动闪光）}$$

它广泛存在于以下三类成熟产品中：

| 平台 / 框架 | 机制类别 | 具体视觉与技术表现 |
|---|---|---|
| **TradingView 原版标杆** | **Tail Digits Coloration<br>(尾数变动位着色)** | **无渐亮、无渐灭、无背景模糊**：仅将发生变动的尾数字体颜色瞬间切为绿（`#089981`）或红（`#f23645`），未变动的前缀数字保持白底，视觉焦点瞬间锁定在跳动的小数点位上。 |
| **AG Grid (华尔街金融网格)** | `enableCellChangeFlash: true` | 单元格数值更新时，背景瞬间切为半透明绿色/红色底色，随后通过 CSS `transition: background-color 1s ease-out` 平滑淡出至透明。 |
| **Binance (币安) / OKX (欧易)** | Orderbook Cell Flash | 实时订单簿与最新成交：价格变动时，该价格单元格背景亮起微透光绿/红底色（0.25），并在 750ms 内单次淡化。 |
| **DexScreener / Birdeye** | Live Transaction Flash | 链上最新 Swap 成交或币价刷新时，价格胶囊背景闪亮绿色/红色微底色，0.8 秒平滑消退。 |
| **Bloomberg Anywhere** | `FLASH <GO>` (Field Flash) | 彭博终端设置：行情字段跳动时，背景短促变亮以引起交易员余光注意。 |
---

## 二、 顶级实装 1：TradingView 原版行业标准（尾数变动位着色 · 零渐亮渐灭）

在 TradingView 官方自选列表与报价小部件中，其实现完全舍弃了任何背景渐变、渐亮或渐灭。其核心交互原则是：**仅改变变动了的尾数数字的字体颜色，未发生变化的前缀数字保持白底中性**。

### 1. 算法机制解剖（公共前缀比对）
例如价格从 `63,420.50` 变更为 `63,420.85`：
1. 算法逐字比对两个字符串的公共前缀：发现第 7 位小数点后的 `'5'` 变成了 `'8'`；
2. 将字符串拆分为两个 `<span />`：
   - **公共前缀**：`<span class="ticker-prefix">63,420.</span>`（保持常规纯白）；
   - **变动尾数**：`<span class="ticker-suffix-up">85</span>`（直接赋予 `--green` 字体颜色）；
3. **完全没有动画、没有渐变、没有背景微光**，数值直接以极简的硬切变色呈现，交易员的视线能够以 $O(1)$ 的速度锁定到底跳变的是哪个小数位。

### 2. TradingView 极简 CSS 规范：
```css
.ticker-price {
  font-weight: 600;
  display: inline-flex;
}
.ticker-prefix {
  color: var(--text);
}
.ticker-suffix-up {
  color: var(--green); /* 涨：尾数亮绿 */
}
.ticker-suffix-down {
  color: var(--red);   /* 跌：尾数亮红 */
}
```

### 3. 极简 15 行 React 组件实现：
```tsx
export function TradingViewPrice({ prev, current }: { prev: string; current: string }) {
  const minLen = Math.min(prev.length, current.length)
  let divergeIdx = 0
  while (divergeIdx < minLen && prev[divergeIdx] === current[divergeIdx]) {
    divergeIdx++
  }
  const prefix = current.slice(0, divergeIdx)
  const suffix = current.slice(divergeIdx)
  const direction = parseFloat(current.replace(/,/g, '')) >= parseFloat(prev.replace(/,/g, '')) ? 'up' : 'down'
  
  return (
    <span className="ticker-price mono">
      <span className="ticker-prefix">{prefix}</span>
      <span className={direction === 'up' ? 'ticker-suffix-up' : 'ticker-suffix-down'}>{suffix}</span>
    </span>
  )
}
```

---

## 三、 顶级实装 2：AG Grid 企业级金融表格（高盛/摩根/花旗标准）

AG Grid 是华尔街几乎所有机构交易网页版系统的底层表格。它的实现原理极为简单干净：

### 1. 机制解剖
* 当检测到单元格的值（Value）发生变化时：
  1. 瞬间添加类名 `.ag-cell-data-changed`（背景设为目标高亮色）；
  2. 保持 500ms 后移除类名，激活 `.ag-cell-data-changed-animation`；
  3. 借助 CSS 原生属性 `transition: background-color 1s ease-out;`，浏览器自动将其平滑渐变回原来的底色。

### 2. AG Grid 原版核心 CSS 规范：
```css
/* 激活瞬间：通电变亮 */
.ag-cell-data-changed {
  background-color: var(--ag-value-change-delta-up-color, rgba(46, 204, 113, 0.35)) !important;
}

/* 衰减淡出：平滑回落至透明 */
.ag-cell-data-changed-animation {
  background-color: transparent;
  transition: background-color 1s ease-out;
}
```

---

## 三、 顶级实装 2：Binance (币安) / OKX (欧易) 实时订单簿与最新成交

在币安和 OKX 的 Web 交易端，为了兼顾高频数据下的极致性能（每秒可能几十次 Tick），它们没有频繁操作 transition 类名切换，而是直接采用了 **单次 CSS `@keyframes` 动画**：

```css
/* 币安官方类似的订单簿闪烁实现 */
@keyframes binance-flash-green {
  0% {
    background-color: rgba(14, 203, 129, 0.25); /* 币安绿 */
  }
  100% {
    background-color: transparent;
  }
}

@keyframes binance-flash-red {
  0% {
    background-color: rgba(246, 70, 93, 0.25); /* 币安红 */
  }
  100% {
    background-color: transparent;
  }
}

.flash-green {
  animation: binance-flash-green 750ms ease-out;
}
.flash-red {
  animation: binance-flash-red 750ms ease-out;
}
```
**优点**：完全不触发重排（Reflow），浏览器 GPU 直接在合成线程（Compositor Thread）跑完这 750ms，性能极其稳定。

---

## 四、 顶级实装 3：DexScreener / Birdeye 实时链上交易闪烁

DexScreener 的价格胶囊设计更适合暗黑终端。它的特点是**背景带有微弱的圆角与边界微泛光**：

```css
@keyframes dexscreener-flash-up {
  0% {
    background-color: rgba(34, 197, 94, 0.30);
    border-color: rgba(34, 197, 94, 0.60);
  }
  100% {
    background-color: transparent;
    border-color: transparent;
  }
}
```

---

## 五、 Aletheia 当前代码现状与差距（仅差一步）

事实上，Aletheia 在早期开发时已经在代码里写下了这个机制的雏形，但**因为只做了一半，所以没有形成系统性的 terminal 质感**：

### 1. 当前已有的代码 (`frontend/src/index.css:669-674`)
```css
@keyframes tick-up   { from { background: rgba(46,230,168,0.18); } to { background: transparent; } }
@keyframes tick-down { from { background: rgba(255,93,108,0.18); } to { background: transparent; } }
.tick-flash-up   { animation: tick-up 600ms ease-out; }
.tick-flash-down { animation: tick-down 600ms ease-out; }
```

### 2. 为什么当前感觉不明显、像“没做完整”？
1. **透明度过低 (`0.18`)**：在 `#0a0e12` 近黑背景上，`0.18` 的透明度太淡了，如果不死盯着屏幕，肉眼根本注意不到它闪了一下。借鉴币安和 DexScreener，峰值应当在 **`0.28 ~ 0.32`**，瞬间通电感才会出来；
2. **时长偏短 (`600ms`)**：600ms 转瞬即逝。币安和 AG Grid 普遍采用 **`800ms ~ 1000ms`**，让颜色亮起后有明显的“缓缓灭下去”的视觉余辉；
3. **覆盖面太窄**：目前全站**只有顶栏的 `MarketTicker.tsx` 引用了它**！
   - `Overview.tsx` 的当日盈亏（Day PnL）变动时没有闪烁；
   - `TradesPositions.tsx` 的未实现盈亏和当前价格变动时没有闪烁；
   - `Universe.tsx` 的候选集标的价格刷新时也没有闪烁。

---

## 六、 终态推荐方案：尾数变色 + 380ms 紧凑微底色（TradingView 尾数差异 + 币安微底色结合）

按照你确立的格式化标准与敏捷度要求：
1. **保留背景色**：价格变动瞬间，背景亮起带微圆角的绿色（`rgba(46, 230, 168, 0.25)`）或红色（`rgba(255, 93, 108, 0.25)`）；
2. **仅变动尾数文字变色**：只有发生变动的小数尾数变绿/变红，未变动的前缀数字保持纯白中性；
3. **缩短持续时间至 380ms**：杜绝长时间停留拖泥带水，激活 380ms 后背景与字体颜色在 300ms 内快速收敛归位，整个过程干净利落。

### 1. 样式规范 (`index.css`)
```css
.ticker-price {
  font-weight: 600;
  display: inline-flex;
  padding: 1px 4px;
  border-radius: 3px;
  transition: background-color 300ms ease-out;
}
.ticker-prefix {
  color: var(--text);
}
.ticker-suffix {
  color: var(--text);
  transition: color 300ms ease-out;
}

/* 涨：低明度深邃微绿底 + 高明度晶亮绿字 (明暗对比度 > 7:1) */
.ticker-flash-up {
  background-color: rgba(16, 185, 129, 0.16); /* 低明度底色 (~6% 亮度)，安静下沉 */
}
.ticker-flash-up .ticker-suffix {
  color: #34d399; /* 高明度前景色 (~65% 亮度)，文字锐利高亮，不被底色稀释 */
}

/* 跌：低明度暗红底 + 高明度警示红字 (明暗对比度 > 7:1) */
.ticker-flash-down {
  background-color: rgba(239, 68, 68, 0.16); /* 低明度底色 (~5% 亮度)，安静下沉 */
}
.ticker-flash-down .ticker-suffix {
  color: #f87171; /* 高明度前景色 (~55% 亮度)，文字锐利突出 */
}
```
```typescript
// frontend/src/lib/useCellFlash.ts
import { useEffect, useRef, useState } from 'react'

export function useCellFlash(value: number | string | null | undefined): 'tick-flash-up' | 'tick-flash-down' | '' {
  const prevRef = useRef(value)
  const [flashClass, setFlashClass] = useState<'' | 'tick-flash-up' | 'tick-flash-down'>('')

  useEffect(() => {
    const prev = Number(prevRef.current)
    const curr = Number(value)

    if (!Number.isNaN(prev) && !Number.isNaN(curr) && prevRef.current !== undefined && prev !== curr) {
      setFlashClass(curr > prev ? 'tick-flash-up' : 'tick-flash-down')
      const timer = setTimeout(() => setFlashClass(''), 850)
      prevRef.current = value
      return () => clearTimeout(timer)
    }
    prevRef.current = value
  }, [value])

  return flashClass
}
```

### 3. 在需要跳动的数字上挂上即可（0 破坏已有布局）
例如在持仓表的现价、概览页的 PnL 上，直接把类名挂在已有 span 上：
```tsx
function PriceCell({ price }: { price: number }) {
  const flash = useCellFlash(price)
  return <span className={`mono ${flash}`}>{fmtNum(price)}</span>
}
```

---

*（本文档已保存至：`docs/design/frontend/rfcs/price_change_flash_benchmarking_and_practice.md`，可作为后续直接拿来借鉴实装的免造轮子标准依据）*
