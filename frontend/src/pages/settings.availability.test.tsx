// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-query', () => ({
  useQuery: vi.fn(),
  useQueryClient: vi.fn(() => ({})),
}))
vi.mock('react-router-dom', () => ({
  useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  useBlocker: vi.fn(() => ({ state: 'unblocked' })),
}))
vi.mock('../api/client', () => ({
  api: { getStatus: vi.fn(), getSettings: vi.fn() },
}))
vi.mock('../components/PageHeader', () => ({ default: () => null }))
vi.mock('../features/settings/dirtyGuard', () => ({
  hasUncontrolledDraft: () => false,
  isFallbackDraftControl: () => false,
  shouldBlockNavigation: () => false,
}))

// Section stubs — must supply id/title/tab/searchTerms consumed by SETTINGS_INDEX
vi.mock('../features/settings/model-providers/modelProviderState', () => ({
  MODEL_PROVIDERS_SECTION: { id: 'model-providers', title: 'Model Providers', tab: 'agent', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/health/healthState', () => ({
  HEALTH_MONITORING_SECTION: { id: 'health-monitoring', title: 'Health Monitoring', tab: 'health', description: '', searchTerms: [] },
  WEBHOOK_SECTION:           { id: 'webhook',           title: 'Webhook',          tab: 'health', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/telegram/telegramState', () => ({
  TELEGRAM_SECTION: { id: 'telegram', title: 'Telegram', tab: 'health', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/tabs/HealthNotificationsTab', () => ({ HealthNotificationsTab: () => null }))
vi.mock('../features/settings/tabs/AdvancedTab', () => ({
  AdvancedTab: () => null,
  ADVANCED_SECTION: { id: 'advanced', title: 'Advanced', tab: 'advanced', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/tabs/ExecutionTab', () => ({
  ExecutionTab: () => null,
  CAPITAL_EVENTS_SECTION:      { id: 'capital-events',      title: 'Capital Events',     tab: 'execution', description: '', searchTerms: [] },
  EXCHANGE_CONNECTION_SECTION: { id: 'exchange-connection', title: 'Exchange Connection', tab: 'execution', description: '', searchTerms: [] },
  PAPER_ACCOUNT_SECTION:       { id: 'paper-account',       title: 'Paper Account',      tab: 'execution', description: '', searchTerms: [] },
  PAPER_PARAMETERS_SECTION:    { id: 'paper-parameters',    title: 'Paper Parameters',   tab: 'execution', description: '', searchTerms: [] },
  TRADING_MODE_SECTION:        { id: 'trading-mode',        title: 'Trading Mode',       tab: 'execution', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/tabs/AgentModelsTab', () => ({
  AgentModelsTab: () => null,
  INFERENCE_ADMISSION_SECTION: { id: 'inference-admission', title: 'Inference Admission', tab: 'agent', description: '', searchTerms: [] },
  LEARNING_SECTION:            { id: 'learning',            title: 'Learning',            tab: 'agent', description: '', searchTerms: [] },
  LLM_COST_SECTION:            { id: 'llm-cost',            title: 'LLM Cost',            tab: 'agent', description: '', searchTerms: [] },
  PERMISSION_SECTION:          { id: 'permission',          title: 'Permission',          tab: 'agent', description: '', searchTerms: [] },
  REGIME_SECTION:              { id: 'regime',              title: 'Regime',              tab: 'agent', description: '', searchTerms: [] },
  SCHEDULER_SECTION:           { id: 'scheduler',           title: 'Scheduler',           tab: 'agent', description: '', searchTerms: [] },
  SERENITY_SECTION:            { id: 'serenity',            title: 'Serenity',            tab: 'agent', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/tabs/RuntimeSystemTab', () => ({
  RuntimeSystemTab: () => null,
  APP_SECTION:              { id: 'app',              title: 'App',              tab: 'runtime', description: '', searchTerms: [] },
  DATA_EXPORT_SECTION:      { id: 'data-export',      title: 'Data Export',      tab: 'runtime', description: '', searchTerms: [] },
  DATA_RETENTION_SECTION:   { id: 'data-retention',   title: 'Data Retention',   tab: 'runtime', description: '', searchTerms: [] },
  NETWORK_PROXY_SECTION:    { id: 'network-proxy',    title: 'Network Proxy',    tab: 'runtime', description: '', searchTerms: [] },
  SOFTWARE_UPDATES_SECTION: { id: 'software-updates', title: 'Software Updates', tab: 'runtime', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/tabs/SignalsTab', () => ({
  SignalsTab: () => null,
  EQUITIES_SECTION:           { id: 'equities',           title: 'Equities',           tab: 'signals', description: '', searchTerms: [] },
  MACRO_SIGNALS_SECTION:      { id: 'macro-signals',      title: 'Macro Signals',      tab: 'signals', description: '', searchTerms: [] },
  MARKET_DATA_SECTION:        { id: 'market-data',        title: 'Market Data',        tab: 'signals', description: '', searchTerms: [] },
  NEWS_FEEDS_SECTION:         { id: 'news-feeds',         title: 'News Feeds',         tab: 'signals', description: '', searchTerms: [] },
  PREDICTION_MARKETS_SECTION: { id: 'prediction-markets', title: 'Prediction Markets', tab: 'signals', description: '', searchTerms: [] },
}))
vi.mock('../features/settings/tabs/RiskTab', () => ({
  RiskTab: () => null,
  EVENT_TRIGGERS_SECTION:   { id: 'event-triggers',   title: 'Event Triggers',   tab: 'risk', description: '', searchTerms: [] },
  RISK_LIMITS_SECTION:      { id: 'risk-limits',      title: 'Risk Limits',      tab: 'risk', description: '', searchTerms: [] },
  TRADABLE_SYMBOLS_SECTION: { id: 'tradable-symbols', title: 'Tradable Symbols', tab: 'risk', description: '', searchTerms: [] },
  UNIVERSE_SECTION:         { id: 'universe',         title: 'Universe',         tab: 'risk', description: '', searchTerms: [] },
}))

import { useQuery } from '@tanstack/react-query'
import Settings from './Settings'

type QueryResult = { data: unknown; isPending: boolean; isError: boolean; refetch: ReturnType<typeof vi.fn> }

function setup(statusQ: QueryResult, settingsQ: QueryResult) {
  vi.mocked(useQuery).mockImplementation(({ queryKey }: any) => {
    const key = Array.isArray(queryKey) ? queryKey[0] : ''
    if (key === 'status') return statusQ as any
    if (key === 'settings') return settingsQ as any
    return { data: undefined, isPending: false, isError: false, refetch: vi.fn() } as any
  })
}

const okStatus   = { mode: 'paper' }
const okSettings = { agent: {} }

const storageMock = { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn() }

describe('Settings load-state availability (issue #1185)', () => {
  let container: HTMLDivElement
  let root: ReturnType<typeof createRoot>

  beforeEach(() => {
    Object.defineProperty(window, 'localStorage', { value: storageMock, writable: true, configurable: true })
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.clearAllMocks()
  })

  it('initial pending — shows skeleton, no error panel', async () => {
    setup(
      { data: undefined, isPending: true,  isError: false, refetch: vi.fn() },
      { data: undefined, isPending: true,  isError: false, refetch: vi.fn() },
    )
    await act(async () => root.render(<Settings />))
    expect(container.querySelector('.skeleton')).toBeTruthy()
    expect(container.querySelector('.settings-load-error')).toBeNull()
  })

  it('status failure — error panel names System status, no skeleton', async () => {
    setup(
      { data: undefined,  isPending: false, isError: true,  refetch: vi.fn() },
      { data: undefined,  isPending: false, isError: false, refetch: vi.fn() },
    )
    await act(async () => root.render(<Settings />))
    expect(container.querySelector('.settings-load-error')).toBeTruthy()
    expect(container.querySelector('.skeleton')).toBeNull()
    expect(container.textContent).toContain('System status')
  })

  it('settings failure — error panel names Settings configuration, no skeleton', async () => {
    setup(
      { data: okStatus,  isPending: false, isError: false, refetch: vi.fn() },
      { data: undefined, isPending: false, isError: true,  refetch: vi.fn() },
    )
    await act(async () => root.render(<Settings />))
    expect(container.querySelector('.settings-load-error')).toBeTruthy()
    expect(container.querySelector('.skeleton')).toBeNull()
    expect(container.textContent).toContain('Settings configuration')
  })

  it('retry button triggers refetch on both queries', async () => {
    const refetchStatus = vi.fn()
    const refetchSettings = vi.fn()
    setup(
      { data: undefined, isPending: false, isError: true, refetch: refetchStatus },
      { data: undefined, isPending: false, isError: true, refetch: refetchSettings },
    )
    await act(async () => root.render(<Settings />))
    const retryBtn = container.querySelector<HTMLButtonElement>('button')
    expect(retryBtn?.textContent?.trim()).toBe('Retry')
    await act(async () => { retryBtn?.click() })
    expect(refetchStatus).toHaveBeenCalledOnce()
    expect(refetchSettings).toHaveBeenCalledOnce()
  })

  it('background refetch failure — renders shell without discarding draft data', async () => {
    // When data is present (background refetch fails), React Query keeps stale data.
    // The component must continue rendering the UI — no skeleton, no error takeover.
    setup(
      { data: okStatus,   isPending: false, isError: true, refetch: vi.fn() },
      { data: okSettings, isPending: false, isError: true, refetch: vi.fn() },
    )
    await act(async () => root.render(<Settings />))
    expect(container.querySelector('.settings-shell')).toBeTruthy()
    expect(container.querySelector('.skeleton')).toBeNull()
    expect(container.querySelector('.settings-load-error')).toBeNull()
  })
})
