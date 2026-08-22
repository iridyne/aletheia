import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import SRC from './PredictionLens.tsx?raw'
import { PredictionStatusNotice, resolvePredictionDataState, resolvePredictionStatusState } from './PredictionLens'

describe('Prediction Lens status presentation', () => {
  it('distinguishes loading, live enabled, disabled, unavailable, and stale status', () => {
    expect(resolvePredictionStatusState(undefined, true, false, undefined)).toEqual({ state: 'loading', enabled: undefined })
    expect(resolvePredictionStatusState({ enabled: true } as any, false, false, undefined)).toEqual({ state: 'enabled', enabled: true })
    expect(resolvePredictionStatusState({ enabled: false } as any, false, false, undefined)).toEqual({ state: 'disabled', enabled: false })
    expect(resolvePredictionStatusState(undefined, false, true, undefined)).toEqual({ state: 'unavailable', enabled: undefined })
    expect(resolvePredictionStatusState(undefined, false, true, true)).toEqual({ state: 'stale', enabled: true })
    expect(resolvePredictionStatusState({ enabled: true } as any, false, true, undefined)).toEqual({ state: 'stale', enabled: true })
  })

  it('uses canonical English state copy and labels disabled preferences as configuration-only', () => {
    expect(SRC).not.toContain('Prediction Market 已关闭')
    expect(SRC).toContain('Preferences (configuration only)')
    expect(SRC).toContain('DataStateMessage')
    expect(SRC).toContain("statusView.state !== 'enabled'")
  })

  it('renders distinct notices for disabled, unavailable, stale, and loading states', () => {
    expect(renderToStaticMarkup(createElement(PredictionStatusNotice, { state: 'disabled', enabled: false }))).toContain('configuration-only')
    expect(renderToStaticMarkup(createElement(PredictionStatusNotice, { state: 'unavailable', enabled: undefined }))).toContain('status read failed')
    expect(renderToStaticMarkup(createElement(PredictionStatusNotice, { state: 'stale', enabled: true }))).toContain('stale')
    expect(renderToStaticMarkup(createElement(PredictionStatusNotice, { state: 'loading', enabled: undefined }))).toContain('Loading Prediction Markets status')
    expect(renderToStaticMarkup(createElement(PredictionStatusNotice, { state: 'enabled', enabled: true }))).toBe('')
  })

  it('keeps read failures distinct from successful empty collections', () => {
    expect(resolvePredictionDataState(undefined, true, false)).toBe('loading')
    expect(resolvePredictionDataState(undefined, false, true)).toBe('unavailable')
    expect(resolvePredictionDataState([], false, false)).toBe('empty')
    expect(resolvePredictionDataState([{ id: 'retained' }], false, true)).toBe('stale')
  })
})
