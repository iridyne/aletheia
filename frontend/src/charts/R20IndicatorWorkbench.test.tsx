// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import R20IndicatorWorkbench from './R20IndicatorWorkbench'
import { DEFAULT_ACTIVE_INDICATORS } from './r20KLineChart'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | undefined
let container: HTMLDivElement

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  container.remove()
  document.body.innerHTML = ''
})

describe('R20IndicatorWorkbench', () => {
  it('renders indicator trigger with active count badge and countdown', () => {
    act(() => {
      root?.render(
        <R20IndicatorWorkbench
          timeframe="15m"
          activeIndicators={DEFAULT_ACTIVE_INDICATORS}
          onToggleIndicator={() => {}}
        />,
      )
    })

    const trigger = container.querySelector('button[aria-haspopup="true"]') as HTMLButtonElement
    expect(trigger).toBeTruthy()
    expect(trigger.textContent).toContain('指标')
    // Default active indicators are VWAP and VOL (count 2)
    expect(trigger.textContent).toContain('2')
    expect(container.querySelector('.candle-countdown-wrap')?.textContent).toContain('倒计时:')
  })

  it('opens indicator popover, displays 6 main and 6 sub indicators, and toggles selection', () => {
    const onToggle = vi.fn()
    act(() => {
      root?.render(
        <R20IndicatorWorkbench
          timeframe="1h"
          activeIndicators={DEFAULT_ACTIVE_INDICATORS}
          onToggleIndicator={onToggle}
        />,
      )
    })

    const trigger = container.querySelector('button[aria-haspopup="true"]') as HTMLButtonElement
    act(() => trigger.click())

    const menu = container.querySelector('.indicator-dropdown-menu')
    expect(menu).toBeTruthy()
    expect(menu?.textContent).toContain('主图叠加指标')
    expect(menu?.textContent).toContain('副图震荡指标')

    const buttons = [...menu!.querySelectorAll('button')]
    expect(buttons).toHaveLength(12) // 6 main + 6 sub

    const bollBtn = buttons.find(b => b.textContent?.includes('BOLL'))
    expect(bollBtn).toBeTruthy()
    act(() => bollBtn?.click())
    expect(onToggle).toHaveBeenCalledWith('BOLL')
  })

  it('closes popover on Escape key press', () => {
    act(() => {
      root?.render(
        <R20IndicatorWorkbench
          timeframe="1h"
          activeIndicators={DEFAULT_ACTIVE_INDICATORS}
          onToggleIndicator={() => {}}
        />,
      )
    })

    const trigger = container.querySelector('button[aria-haspopup="true"]') as HTMLButtonElement
    act(() => trigger.click())
    expect(container.querySelector('.indicator-dropdown-menu')).toBeTruthy()

    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(container.querySelector('.indicator-dropdown-menu')).toBeNull()
  })

  it('renders simulation metrics console when simMode is active', () => {
    act(() => {
      root?.render(
        <R20IndicatorWorkbench
          timeframe="1h"
          activeIndicators={DEFAULT_ACTIVE_INDICATORS}
          onToggleIndicator={() => {}}
          simMode={true}
          currentPrice={100}
          currentAtr={2}
          simEntryPrice={100}
          simSL={96}
          simTP={108}
        />,
      )
    })

    const simConsole = container.querySelector('.sim-console')
    expect(simConsole).toBeTruthy()
    expect(simConsole?.textContent).toContain('科学预期盈亏比测算控制台')
    expect(simConsole?.textContent).toContain('2.00 : 1') // (108 - 100) / (100 - 96) = 2.00
    expect(simConsole?.textContent).toContain('预期收益目标 (TP)')
    expect(simConsole?.textContent).toContain('最大硬风控风险 (SL)')
  })
})
