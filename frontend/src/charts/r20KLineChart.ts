/**
 * Aletheia Trading OS - R20 Quantum Trader Charts & Indicators Adaptation
 * Directly reusing indicators, styles, and risk models from R20 (commit bb2ee77).
 */

import type {
  KLineData,
  DeepPartial,
  Styles,
} from 'klinecharts'

let vwapRegistered = false

/**
 * 注册 R20 原生 VWAP 指标 (基于成交量加权平均价，日内自然重置)
 */
export async function registerR20Indicators(): Promise<void> {
  if (vwapRegistered || typeof window === 'undefined') return
  // klinecharts accesses window at module root; dynamic import is required for SSR / Node environment safety.
  const { registerIndicator } = await import('klinecharts')
  registerIndicator({
    name: 'VWAP',
    shortName: 'VWAP',
    series: 'price',
    precision: 2,
    figures: [{ key: 'vwap', title: 'VWAP: ', type: 'line' }],
    styles: {
      lines: [{ style: 'solid', smooth: false, size: 1.5, color: '#06B6D4' }], // 青蓝色
    },
    calc: (dataList: KLineData[]) => {
      let cumTypicalVol = 0
      let cumVol = 0
      let lastDay = -1

      return dataList.map((kLine) => {
        const d = new Date(kLine.timestamp)
        const day = d.getUTCDate()
        // 每天重置或者连续累计
        if (lastDay !== -1 && day !== lastDay) {
          cumTypicalVol = 0
          cumVol = 0
        }
        lastDay = day

        const typicalPrice = (kLine.high + kLine.low + kLine.close) / 3
        const vol = Number(kLine.volume || 0)
        cumTypicalVol += typicalPrice * vol
        cumVol += vol

        return {
          vwap: cumVol > 0 ? cumTypicalVol / cumVol : typicalPrice,
        }
      })
    },
  })
  vwapRegistered = true
}

export interface IndicatorOption {
  key: string
  name: string
  label: string
  desc: string
  color: string
  defaultParams?: number[]
  isSub: boolean
}

// 主图叠加指标 (Overlay on Main Candle Pane)
export const MAIN_INDICATORS: IndicatorOption[] = [
  { key: 'VWAP', name: 'VWAP', label: 'VWAP', desc: '成交量加权均价线', color: '#06B6D4', isSub: false },
  { key: 'MA', name: 'MA', label: 'MA', desc: '均线 (5, 10, 20)', color: '#F59E0B', defaultParams: [5, 10, 20], isSub: false },
  { key: 'EMA', name: 'EMA', label: 'EMA', desc: '指数均线 (12, 26, 50)', color: '#38BDF8', defaultParams: [12, 26, 50], isSub: false },
  { key: 'BOLL', name: 'BOLL', label: 'BOLL', desc: '布林带轨道 (20, 2)', color: '#818CF8', defaultParams: [20, 2], isSub: false },
  { key: 'SAR', name: 'SAR', label: 'SAR', desc: '抛物线转向', color: '#EC4899', isSub: false },
  { key: 'BBI', name: 'BBI', label: 'BBI', desc: '多空多均线综合', color: '#10B981', isSub: false },
]

// 副图独立窗格指标 (Sub Panes)
export const SUB_INDICATORS: IndicatorOption[] = [
  { key: 'VOL', name: 'VOL', label: 'VOL', desc: '成交量与柱形量能', color: '#10B981', isSub: true },
  { key: 'MACD', name: 'MACD', label: 'MACD', desc: '异同移动平均线', color: '#3B82F6', defaultParams: [12, 26, 9], isSub: true },
  { key: 'RSI', name: 'RSI', label: 'RSI', desc: '相对强弱动量 (6, 12, 24)', color: '#F97316', defaultParams: [6, 12, 24], isSub: true },
  { key: 'KDJ', name: 'KDJ', label: 'KDJ', desc: '随机摆动指标 (9, 3, 3)', color: '#A855F7', defaultParams: [9, 3, 3], isSub: true },
  { key: 'OBV', name: 'OBV', label: 'OBV', desc: '能量潮累积线', color: '#EAB308', isSub: true },
  { key: 'WR', name: 'WR', label: 'WR', desc: '威廉超买超卖 (14)', color: '#6366F1', defaultParams: [14], isSub: true },
]

// 默认激活指标：默认开启 VOL 与 VWAP（对齐 R20 黄金组合）
export const DEFAULT_ACTIVE_INDICATORS: Record<string, boolean> = {
  VWAP: true,
  VOL: true,
  MA: false,
  EMA: false,
  BOLL: false,
  SAR: false,
  BBI: false,
  MACD: false,
  RSI: false,
  KDJ: false,
  OBV: false,
  WR: false,
}

/**
 * 获取 R20 原生高信噪比暗色图表主题配置
 */
export function getR20ChartStyles(dark = true): DeepPartial<Styles> {
  return {
    grid: {
      show: true,
      horizontal: {
        show: true,
        size: 1,
        color: dark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
        style: 'solid',
      },
      vertical: {
        show: false, // 隐藏垂直杂乱网格
      },
    },
    candle: {
      type: 'candle_solid',
      bar: {
        upColor: '#10B981',
        downColor: '#F43F5E',
        noChangeColor: '#888888',
        upBorderColor: '#10B981',
        downBorderColor: '#F43F5E',
        noChangeBorderColor: '#888888',
        upWickColor: '#10B981',
        downWickColor: '#F43F5E',
        noChangeWickColor: '#888888',
      },
      priceMark: {
        show: true,
        high: {
          show: true,
          color: dark ? '#CBD5E1' : '#475569',
          textOffset: 4,
          textSize: 10,
        },
        low: {
          show: true,
          color: dark ? '#CBD5E1' : '#475569',
          textOffset: 4,
          textSize: 10,
        },
        last: {
          show: true,
          upColor: '#10B981',
          downColor: '#F43F5E',
          noChangeColor: '#888888',
          line: {
            show: true,
            style: 'dashed',
            dashedValue: [4, 4],
            size: 1,
          },
          text: {
            show: true,
            size: 11,
            paddingLeft: 4,
            paddingTop: 2,
            paddingRight: 4,
            paddingBottom: 2,
            color: '#FFFFFF',
          },
        },
      },
      tooltip: {
        showRule: 'always',
        showType: 'standard',
      },
    },
    indicator: {
      ohlc: {
        upColor: '#10B981',
        downColor: '#F43F5E',
        noChangeColor: '#888888',
      },
      lines: [
        { style: 'solid', smooth: false, size: 1.5, color: '#F59E0B' }, // MA5 / 黄
        { style: 'solid', smooth: false, size: 1.5, color: '#38BDF8' }, // MA10 / 蓝
        { style: 'solid', smooth: false, size: 1.5, color: '#A855F7' }, // MA20 / 紫
        { style: 'solid', smooth: false, size: 1.5, color: '#F43F5E' },
        { style: 'solid', smooth: false, size: 1.5, color: '#10B981' },
      ],
      lastValueMark: {
        show: true,
        text: {
          show: true,
          size: 10,
          paddingLeft: 3,
          paddingTop: 1,
          paddingRight: 3,
          paddingBottom: 1,
          color: '#FFFFFF',
        },
      },
    },
    xAxis: {
      show: true,
      size: 'auto',
      axisLine: {
        show: true,
        color: dark ? '#1E293B' : '#E2E8F0',
        size: 1,
      },
      tickText: {
        show: true,
        color: dark ? '#64748B' : '#94A3B8',
        family: 'IBM Plex Mono, monospace',
        size: 10,
      },
      tickLine: {
        show: true,
        size: 1,
        length: 3,
        color: dark ? '#1E293B' : '#E2E8F0',
      },
    },
    yAxis: {
      show: true,
      size: 'auto',
      axisLine: {
        show: true,
        color: dark ? '#1E293B' : '#E2E8F0',
        size: 1,
      },
      tickText: {
        show: true,
        color: dark ? '#94A3B8' : '#64748B',
        family: 'IBM Plex Mono, monospace',
        size: 11,
      },
      tickLine: {
        show: false,
      },
    },
    separator: {
      size: 1,
      color: dark ? '#1E293B' : '#E2E8F0',
      fill: true,
      activeBackgroundColor: dark ? '#334155' : '#CBD5E1',
    },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: {
          style: 'dashed',
          dashedValue: [4, 4],
          size: 1,
          color: dark ? '#64748B' : '#94A3B8',
        },
        text: {
          show: true,
          color: '#FFFFFF',
          size: 11,
          family: 'IBM Plex Mono, monospace',
          backgroundColor: '#3B82F6',
        },
      },
      vertical: {
        show: true,
        line: {
          style: 'dashed',
          dashedValue: [4, 4],
          size: 1,
          color: dark ? '#64748B' : '#94A3B8',
        },
        text: {
          show: true,
          color: '#FFFFFF',
          size: 10,
          family: 'IBM Plex Mono, monospace',
          backgroundColor: '#475569',
        },
      },
    },
  }
}

/**
 * 实时计算 K 线结线倒计时 (格式 MM:SS)
 */
export function updateR20Countdown(period: string, now = new Date()): string {
  const sec = now.getSeconds()
  const min = now.getMinutes()
  const hr = now.getHours()
  let remainSec = 0
  const normalized = period.toLowerCase()

  if (normalized === '1m') {
    remainSec = 60 - sec
  } else if (normalized === '5m') {
    remainSec = (5 - (min % 5)) * 60 - sec
  } else if (normalized === '15m') {
    remainSec = (15 - (min % 15)) * 60 - sec
  } else if (normalized === '30m') {
    remainSec = (30 - (min % 30)) * 60 - sec
  } else if (normalized === '1h' || normalized === '60m') {
    remainSec = (60 - min) * 60 - sec
  } else if (normalized === '4h') {
    remainSec = (4 - (hr % 4)) * 3600 - min * 60 - sec
  } else {
    remainSec = 86400 - (hr * 3600 + min * 60 + sec)
  }

  remainSec = Math.max(0, remainSec)
  const m = Math.floor((remainSec % 3600) / 60)
  const s = remainSec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/**
 * R20 真实数学风控测算模型
 */
export interface RiskRewardMetrics {
  riskDist: number
  rewardDist: number
  riskPct: number
  rewardPct: number
  rrRatio: number
  atrMultiple: number
  isRrCompliant: boolean
  isAtrOptimal: boolean
  estProfitUsd: number
  estRiskUsd: number
}

export function calculateR20RiskReward(params: {
  entry: number
  sl: number
  tp: number
  side: 'long' | 'short'
  atr?: number
  margin?: number
  leverage?: number
}): RiskRewardMetrics {
  const { entry, sl, tp, side, atr = 0, margin = 100, leverage = 3 } = params
  let riskDist = 0
  let rewardDist = 0

  if (side === 'long') {
    riskDist = Math.max(0, entry - sl)
    rewardDist = Math.max(0, tp - entry)
  } else {
    riskDist = Math.max(0, sl - entry)
    rewardDist = Math.max(0, entry - tp)
  }

  const riskPct = entry > 0 ? (riskDist / entry) * 100 : 0
  const rewardPct = entry > 0 ? (rewardDist / entry) * 100 : 0
  const rrRatio = riskDist > 0 ? rewardDist / riskDist : 0
  const atrMultiple = atr > 0 ? riskDist / atr : 0

  const isRrCompliant = rrRatio >= 2.0
  const isAtrOptimal = atrMultiple >= 1.8 && atrMultiple <= 2.2
  const estProfitUsd = margin * leverage * (rewardPct / 100)
  const estRiskUsd = margin * leverage * (riskPct / 100)

  return {
    riskDist,
    rewardDist,
    riskPct,
    rewardPct,
    rrRatio,
    atrMultiple,
    isRrCompliant,
    isAtrOptimal,
    estProfitUsd,
    estRiskUsd,
  }
}
