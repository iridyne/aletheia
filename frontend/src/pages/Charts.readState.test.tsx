import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  opportunities: undefined as unknown,
  opportunityLoading: false,
  opportunityError: false,
  news: undefined as unknown,
  newsLoading: false,
  newsError: false,
  macro: undefined as unknown,
  macroLoading: false,
  macroError: false,
}))

vi.mock('../api/client', () => ({
  api: {
    getOpportunities: vi.fn(),
    getNews: vi.fn(),
    getMacro: vi.fn(),
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    if (queryKey[0] === 'opportunities') return { data: state.opportunities, isLoading: state.opportunityLoading, isError: state.opportunityError }
    if (queryKey[0] === 'news') return { data: state.news, isLoading: state.newsLoading, isError: state.newsError }
    return { data: state.macro, isLoading: state.macroLoading, isError: state.macroError }
  },
}))

vi.mock('../charts/PriceChart', async () => {
  const model = await vi.importActual<typeof import('../charts/priceChartModel')>('../charts/priceChartModel')
  return {
    DEFAULT_CHART_SETTINGS: model.DEFAULT_CHART_SETTINGS,
    MAIN_INDICATOR_IDS: model.MAIN_INDICATOR_IDS,
    SUBCHART_INDICATOR_IDS: model.SUBCHART_INDICATOR_IDS,
    MAX_INDICATORS_PER_ZONE: model.MAX_INDICATORS_PER_ZONE,
    default: () => null,
  }
})

import { NewsTab, SignalsTab } from './Charts'
import MacroBar from '../components/MacroBar'

const opportunity = {
  id: 'opp-1', symbol: 'BTC/USDT', market: 'spot', asset_class: 'crypto',
  direction_bias: 'long', confidence_score: 90, confidence: 'high', source_agent: 'observer', thesis: 'Demand is improving.',
}

describe('Charts read-state contract', () => {
  it('renders signal read failures as unavailable instead of an empty market', () => {
    state.opportunities = undefined
    state.opportunityError = true
    const html = renderToStaticMarkup(<SignalsTab onInstrumentSelect={() => undefined} />)
    expect(html).toContain('Market signals unavailable')
    expect(html).not.toContain('No active signals')
  })

  it('keeps cached signals visible with an explicit stale marker', () => {
    state.opportunities = [opportunity]
    state.opportunityError = true
    const html = renderToStaticMarkup(<SignalsTab onInstrumentSelect={() => undefined} />)
    expect(html).toContain('Market signals are stale')
    expect(html).toContain('Demand is improving.')
  })

  it('does not describe a stale empty signal cache as an empty market', () => {
    state.opportunities = []
    state.opportunityError = true
    const html = renderToStaticMarkup(<SignalsTab onInstrumentSelect={() => undefined} />)
    expect(html).toContain('Market signals are stale')
    expect(html).not.toContain('No active signals')
  })

  it('distinguishes news failures from a successful empty news feed', () => {
    state.news = undefined
    state.newsError = true
    expect(renderToStaticMarkup(<NewsTab />)).toContain('News data unavailable')

    state.news = []
    state.newsError = false
    expect(renderToStaticMarkup(<NewsTab />)).toContain('News ingestion not configured')
  })

  it('does not describe a stale empty news cache as unconfigured', () => {
    state.news = []
    state.newsError = true
    const html = renderToStaticMarkup(<NewsTab />)
    expect(html).toContain('News data is stale')
    expect(html).not.toContain('News ingestion not configured')
  })

  it('renders macro read failures as unavailable and retained values as stale', () => {
    state.macro = undefined
    state.macroError = true
    expect(renderToStaticMarkup(<MacroBar />)).toContain('Macro data unavailable')

    state.macro = { available: true, data: { risk_regime: 'neutral', vix_close: 18 } }
    expect(renderToStaticMarkup(<MacroBar />)).toContain('Macro data is stale')
    expect(renderToStaticMarkup(<MacroBar />)).toContain('18')
  })

  it('surfaces a failed refetch of a cached unavailable macro snapshot', () => {
    state.macro = { available: false, data: null }
    state.macroError = true
    const html = renderToStaticMarkup(<MacroBar />)
    expect(html).toContain('Macro data is stale')
    expect(html).not.toContain('collector initializing')
  })

  it('restores successful empty and current states after recovery', () => {
    state.opportunities = []
    state.opportunityError = false
    expect(renderToStaticMarkup(<SignalsTab onInstrumentSelect={() => undefined} />)).toContain('No active signals')

    state.news = [{ id: 'news-1', title: 'Current item' }]
    state.newsError = false
    expect(renderToStaticMarkup(<NewsTab />)).not.toContain('News data is stale')

    state.macro = { available: false, data: null }
    state.macroError = false
    const macro = renderToStaticMarkup(<MacroBar />)
    expect(macro).toContain('collector initializing')
    expect(macro).not.toContain('Macro data is stale')
  })
})
