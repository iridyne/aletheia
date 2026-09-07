/**
 * Aletheia Trading OS - R20 Indicator Workbench & Simulation Console
 * Reusing R20's indicator dropdown popover, countdown timer, and risk simulation workbench (commit bb2ee77).
 */

import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import {
  SlidersHorizontal,
  ChevronDown,
  Check,
  Sliders,
  RefreshCw,
  Zap,
  RotateCcw,
  Copy,
} from 'lucide-react'
import {
  MAIN_INDICATORS,
  SUB_INDICATORS,
  updateR20Countdown,
  calculateR20RiskReward,
  type RiskRewardMetrics,
} from './r20KLineChart'

export interface R20IndicatorWorkbenchProps {
  timeframe: string
  activeIndicators: Record<string, boolean>
  onToggleIndicator: (key: string) => void
  onRefresh?: () => void
  isRefreshing?: boolean
  currentPrice?: number
  currentAtr?: number
  symbol?: string
  simMode?: boolean
  onToggleSimMode?: (active: boolean) => void
  simEntryPrice?: number
  simSL?: number
  simTP?: number
  onResetSimulation?: () => void
}

export default function R20IndicatorWorkbench({
  timeframe,
  activeIndicators,
  onToggleIndicator,
  onRefresh,
  isRefreshing = false,
  currentPrice = 0,
  currentAtr = 0,
  symbol = 'BTC',
  simMode = false,
  onToggleSimMode,
  simEntryPrice,
  simSL,
  simTP,
  onResetSimulation,
}: R20IndicatorWorkbenchProps) {
  const [showIndicatorMenu, setShowIndicatorMenu] = useState(false)
  const [countdown, setCountdown] = useState('00:00')
  const [copied, setCopied] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // 激活指标总数
  const activeCount = useMemo(() => {
    return Object.values(activeIndicators).filter(Boolean).length
  }, [activeIndicators])

  // 实时结线倒计时
  useEffect(() => {
    setCountdown(updateR20Countdown(timeframe))
    const timer = setInterval(() => {
      setCountdown(updateR20Countdown(timeframe))
    }, 1000)
    return () => clearInterval(timer)
  }, [timeframe])

  // 点击外部和键盘 Esc 关闭下拉面板
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowIndicatorMenu(false)
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setShowIndicatorMenu(false)
      }
    }
    if (showIndicatorMenu) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleKeyDown)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [showIndicatorMenu])

  // 风控试算指标模型
  const effectiveEntry = simEntryPrice && simEntryPrice > 0 ? simEntryPrice : currentPrice
  const effectiveSL = simSL && simSL > 0 ? simSL : currentPrice * 0.97
  const effectiveTP = simTP && simTP > 0 ? simTP : currentPrice * 1.06

  const riskMetrics = useMemo<RiskRewardMetrics>(() => {
    return calculateR20RiskReward({
      entry: effectiveEntry,
      sl: effectiveSL,
      tp: effectiveTP,
      side: 'long',
      atr: currentAtr > 0 ? currentAtr : effectiveEntry * 0.015,
      margin: 100,
      leverage: 3,
    })
  }, [effectiveEntry, effectiveSL, effectiveTP, currentAtr])

  const copySimulationSummary = useCallback(() => {
    const text = `【Aletheia R20 风控测算】${symbol} 入场:${effectiveEntry.toFixed(2)} SL:${effectiveSL.toFixed(2)} TP:${effectiveTP.toFixed(2)} R:R=${riskMetrics.rrRatio.toFixed(2)}:1`
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [symbol, effectiveEntry, effectiveSL, effectiveTP, riskMetrics.rrRatio])

  return (
    <div className="r20-workbench-wrapper flex flex-col w-full font-mono text-xs">
      {/* 工具栏右侧：指标下拉、试算按钮、刷新动作 */}
      <div className="flex items-center space-x-1.5 sm:space-x-2 shrink-0">
        {/* 结线倒计时显示 */}
        <div className="candle-countdown-wrap flex items-center space-x-1 px-2 py-1 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[11px]">
          <span className="text-[var(--muted)] text-[10px]">倒计时:</span>
          <span className="font-bold text-amber-400 tabular-nums">{countdown}</span>
        </div>

        {/* 指标分类下拉工作台 (Indicators Dropdown) */}
        <div className="relative indicator-dropdown-container" ref={containerRef}>
          <button
            type="button"
            onClick={() => setShowIndicatorMenu(!showIndicatorMenu)}
            aria-expanded={showIndicatorMenu}
            aria-haspopup="true"
            className={`h-7 px-2.5 rounded border flex items-center space-x-1.5 transition-all cursor-pointer font-bold ${
              showIndicatorMenu || activeCount > 0
                ? 'bg-cyan-950/40 border-cyan-500/50 text-cyan-300'
                : 'bg-[var(--surface)] border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]'
            }`}
          >
            <SlidersHorizontal className="w-3 h-3" />
            <span>指标</span>
            {activeCount > 0 && (
              <span className="w-4 h-4 rounded-full bg-emerald-500/20 text-emerald-400 text-[10px] flex items-center justify-center font-bold">
                {activeCount}
              </span>
            )}
            <ChevronDown className={`w-3 h-3 transition-transform ${showIndicatorMenu ? 'rotate-180' : ''}`} />
          </button>

          {/* 下拉浮层面板 */}
          {showIndicatorMenu && (
            <div
              className="indicator-dropdown-menu absolute right-0 top-8 z-50 w-72 p-3 rounded-xl border shadow-2xl flex flex-col space-y-3 font-mono text-xs backdrop-blur-md bg-[var(--surface)] border-[var(--border-strong)]"
              role="dialog"
              aria-label="技术指标选择面板"
            >
              {/* 板块 1: 主图指标 */}
              <div>
                <div className="flex items-center justify-between pb-1.5 border-b mb-1.5 border-[var(--border)]">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-amber-400">
                    主图叠加指标
                  </span>
                  <span className="text-[9px] text-[var(--muted)]">主蜡烛同屏</span>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  {MAIN_INDICATORS.map(ind => {
                    const active = !!activeIndicators[ind.key]
                    return (
                      <button
                        key={ind.key}
                        type="button"
                        onClick={() => onToggleIndicator(ind.key)}
                        className={`p-1.5 rounded border text-left flex items-center justify-between transition-all cursor-pointer ${
                          active
                            ? 'bg-cyan-950/40 border-cyan-500/60 text-[var(--text)]'
                            : 'bg-[var(--surface-2)] border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]'
                        }`}
                      >
                        <div className="flex items-center space-x-1.5 truncate">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: ind.color }} />
                          <span className="font-bold truncate">{ind.label}</span>
                        </div>
                        {active && <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0" />}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* 板块 2: 副图指标 */}
              <div>
                <div className="flex items-center justify-between pb-1.5 border-b mb-1.5 border-[var(--border)]">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-400">
                    副图震荡指标
                  </span>
                  <span className="text-[9px] text-[var(--muted)]">独立高宽窗口</span>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  {SUB_INDICATORS.map(ind => {
                    const active = !!activeIndicators[ind.key]
                    return (
                      <button
                        key={ind.key}
                        type="button"
                        onClick={() => onToggleIndicator(ind.key)}
                        className={`p-1.5 rounded border text-left flex items-center justify-between transition-all cursor-pointer ${
                          active
                            ? 'bg-cyan-950/40 border-cyan-500/60 text-[var(--text)]'
                            : 'bg-[var(--surface-2)] border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]'
                        }`}
                      >
                        <div className="flex items-center space-x-1.5 truncate">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: ind.color }} />
                          <span className="font-bold truncate">{ind.label}</span>
                        </div>
                        {active && <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0" />}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 调价平移试算开关按钮 */}
        <button
          type="button"
          onClick={() => onToggleSimMode?.(!simMode)}
          className={`h-7 px-2.5 rounded border flex items-center space-x-1 transition-all cursor-pointer font-bold ${
            simMode
              ? 'bg-cyan-950/40 border-cyan-500/60 text-cyan-300'
              : 'bg-[var(--surface)] border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]'
          }`}
          title={simMode ? '退出试算' : '平移试算 R:R'}
        >
          <Sliders className="w-3 h-3" />
          <span>{simMode ? '退出' : '试算'}</span>
        </button>

        {/* 快速刷新行情 */}
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            className="h-7 w-7 rounded border flex items-center justify-center transition-colors cursor-pointer bg-[var(--surface)] border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]"
            title="刷新行情数据"
          >
            <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin text-cyan-400' : ''}`} />
          </button>
        )}
      </div>

      {/* 底部风控测算控制台 (平移试算展开时展现) */}
      {simMode && (
        <div className="sim-console border-t mt-3 pt-3 space-y-3 transition-all border-[var(--border)] bg-[var(--surface-2)]/40 p-3 rounded-lg">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center space-x-2">
              <Zap className="w-4 h-4 text-amber-400 shrink-0" />
              <span className="text-xs font-bold text-[var(--text)]">
                科学预期盈亏比测算控制台
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded border font-bold text-slate-400 bg-slate-500/10 border-slate-500/30">
                标准观望测算模式
              </span>
            </div>
          </div>

          {/* 四维测算卡片 */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
            <div className={`p-2.5 rounded-lg border flex flex-col justify-between ${
              riskMetrics.isRrCompliant ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-400' : 'bg-amber-950/30 border-amber-500/40 text-amber-400'
            }`}>
              <span className="text-[10px] uppercase font-bold">
                {riskMetrics.isRrCompliant ? '✅ 期望盈亏比 (R:R)' : '⚠️ 盈亏比不足 2.0'}
              </span>
              <div className="flex items-baseline space-x-1 mt-0.5">
                <span className="text-base sm:text-lg font-black tabular-nums">
                  {riskMetrics.rrRatio.toFixed(2)} : 1
                </span>
                <span className="text-[9px] opacity-70">底线 2.0</span>
              </div>
            </div>

            <div className="p-2.5 rounded-lg border flex flex-col justify-between bg-[var(--surface)] border-[var(--border)]">
              <span className="text-[10px] uppercase font-bold text-[var(--muted)]">
                止损呼吸空间 (ATR)
              </span>
              <div className="flex items-baseline space-x-1 mt-0.5">
                <span className={`text-base sm:text-lg font-black tabular-nums ${
                  riskMetrics.isAtrOptimal ? 'text-cyan-400' : 'text-amber-400'
                }`}>
                  {riskMetrics.atrMultiple.toFixed(2)}x
                </span>
                <span className="text-[9px] font-bold text-[var(--muted)]">
                  {riskMetrics.isAtrOptimal ? '防插针区间' : '偏离1.8~2.2'}
                </span>
              </div>
            </div>

            <div className="p-2.5 rounded-lg border flex flex-col justify-between bg-[var(--surface)] border-[var(--border)]">
              <span className="text-[10px] uppercase font-bold text-emerald-500">
                预期收益目标 (TP)
              </span>
              <div className="flex items-baseline space-x-1 mt-0.5">
                <span className="text-base sm:text-lg font-black text-emerald-400 tabular-nums">
                  +${riskMetrics.estProfitUsd.toFixed(2)}
                </span>
                <span className="text-[9px] text-emerald-400 font-bold">
                  ({riskMetrics.rewardPct.toFixed(1)}%)
                </span>
              </div>
            </div>

            <div className="p-2.5 rounded-lg border flex flex-col justify-between bg-[var(--surface)] border-[var(--border)]">
              <span className="text-[10px] uppercase font-bold text-rose-500">
                最大硬风控风险 (SL)
              </span>
              <div className="flex items-baseline space-x-1 mt-0.5">
                <span className="text-base sm:text-lg font-black text-rose-400 tabular-nums">
                  -${riskMetrics.estRiskUsd.toFixed(2)}
                </span>
                <span className="text-[9px] text-rose-400 font-bold">
                  ({riskMetrics.riskPct.toFixed(1)}%)
                </span>
              </div>
            </div>
          </div>

          {/* 复位与复制风控参数 */}
          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <button
              type="button"
              onClick={onResetSimulation}
              className="px-2.5 py-1 rounded border text-[11px] font-mono flex items-center space-x-1 cursor-pointer transition-colors bg-[var(--surface)] border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]"
              title="重置点位"
            >
              <RotateCcw className="w-3 h-3" />
              <span>复位</span>
            </button>

            <button
              type="button"
              onClick={copySimulationSummary}
              className="px-3 py-1 rounded border text-xs font-mono font-bold flex items-center space-x-1.5 cursor-pointer transition-all bg-cyan-950/40 border-cyan-500/50 text-cyan-300 hover:bg-cyan-900/50"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? '已复制' : '复制风控参数'}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
