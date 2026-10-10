import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import {
  CollapsibleSection as Collapsible,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  Switch as Toggle,
} from '../../../components/ui'
import { DataStateMessage } from '../../../components/DataState'
import { listToCsv, parseCsvList } from '../csvList'
import { executionExchangeFromPaperMode } from '../execution/exchangeMode'
import { useSettingsDraftLifecycle } from '../draftLifecycle'
import { SettingsFeatureSection, type SettingsFeatureDescriptor } from '../SettingsFeatureSection'
import type { SettingsDirtyProps, SettingsProps } from '../types'

export const MARKET_DATA_SECTION = {
  id: 'market-data-sources', title: 'Market Data Sources', tab: 'signals',
  description: 'Override primary exchange data routing. Normally managed by the Primary Exchange selector in Execution.',
  searchTerms: ['ohlcv', 'data provider', 'override', 'long short', 'lsr'],
} satisfies SettingsFeatureDescriptor

export const MACRO_SIGNALS_SECTION = {
  id: 'macro-sentiment-signals', title: 'Macro & Sentiment Signals', tab: 'signals',
  description: 'Whole-market context fed into the LLM enriched_context: Fear & Greed Index (Alternative.me) and CoinGecko top movers. All free, no API key required.',
  searchTerms: ['fear greed', 'sentiment', 'coingecko', 'top movers', 'macro'],
} satisfies SettingsFeatureDescriptor

export const PREDICTION_MARKETS_SECTION = {
  id: 'prediction-markets', title: 'Prediction Markets', tab: 'signals',
  description: 'Polymarket observer — fetches probability data for information-edge signals. Observation-only, never routed to execution.',
  searchTerms: ['polymarket', 'categories'],
} satisfies SettingsFeatureDescriptor

function MarketDataCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const md = settings?.market_data || {}
  const exchangeSettings = settings?.exchange || {}
  const EXCHANGES = ['binance', 'bybit', 'okx', 'bitget', 'gate', 'kucoin', 'htx']

  // '' = follow the trading account's exchange (backend default).
  const [primary, setPrimary] = useState(String(md.ccxt_exchange || ''))
  const [fallback, setFallback] = useState(String(md.ccxt_fallback || ''))
  const [cacheTtl, setCacheTtl] = useState(md.ohlcv_cache_ttl_min ?? 10)
  const [longShortRatio, setLongShortRatio] = useState(md.enable_long_short_ratio ?? true)
  const [historyHours, setHistoryHours] = useState(md.derivatives_history_hours ?? 24)
  const [stockTokens, setStockTokens] = useState(listToCsv(md.observed_stock_tokens))
  const [predMarkets, setPredMarkets] = useState(listToCsv(md.observed_prediction_markets))
  const [confirmExchangeDivergence, setConfirmExchangeDivergence] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const executionExchange = executionExchangeFromPaperMode(
    String(exchangeSettings.paper_execution_mode || ''),
  )
  const createsDivergence = executionExchange !== null
    && primary !== ''
    && primary !== executionExchange
    && primary !== String(md.ccxt_exchange || '')

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      ccxt_exchange: primary,
      ccxt_fallback: fallback,
      ohlcv_cache_ttl_min: cacheTtl,
      enable_long_short_ratio: longShortRatio,
      derivatives_history_hours: historyHours,
      observed_stock_tokens: parseCsvList(stockTokens),
      observed_prediction_markets: parseCsvList(predMarkets),
      confirm_exchange_divergence: confirmExchangeDivergence,
    }),
    onSuccess: () => { setConfirmExchangeDivergence(false); setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = primary !== String(md.ccxt_exchange || '')
    || fallback !== String(md.ccxt_fallback || '')
    || cacheTtl !== (md.ohlcv_cache_ttl_min ?? 10)
    || longShortRatio !== (md.enable_long_short_ratio ?? true)
    || historyHours !== (md.derivatives_history_hours ?? 24)
    || stockTokens !== listToCsv(md.observed_stock_tokens)
    || predMarkets !== listToCsv(md.observed_prediction_markets)

  useSettingsDraftLifecycle({
    id: 'market-data',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      setPrimary(String(md.ccxt_exchange || ''))
      setFallback(String(md.ccxt_fallback || ''))
      setCacheTtl(md.ohlcv_cache_ttl_min ?? 10)
      setLongShortRatio(md.enable_long_short_ratio ?? true)
      setHistoryHours(md.derivatives_history_hours ?? 24)
      setStockTokens(listToCsv(md.observed_stock_tokens))
      setPredMarkets(listToCsv(md.observed_prediction_markets))
      setConfirmExchangeDivergence(false)
    },
  })

  return (
    <>
      <Row label="Primary exchange" hint="CCXT exchange ID used for OHLCV and ticker data. Must match a supported CCXT exchange.">
        <select value={primary} onChange={e => { setPrimary(e.target.value); setConfirmExchangeDivergence(false); setMsg(null) }} className="w-auto">
          <option value="">follow trading account{executionExchange ? ` (${executionExchange})` : ''}</option>
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      <Row label="Fallback exchange" hint="Used when the primary exchange fails or returns stale data.">
        <select value={fallback} onChange={e => { setFallback(e.target.value); setMsg(null) }} className="w-auto">
          <option value="">same as primary</option>
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      {createsDivergence && (
        <div className="settings-save-msg err mb-2">
          <Toggle
            checked={confirmExchangeDivergence}
            persistence="staged"
            onChange={setConfirmExchangeDivergence}
            label={`I understand: orders will execute on ${executionExchange}, while market analysis will use ${primary}.`}
          />
        </div>
      )}
      <Row label="OHLCV cache TTL" hint="Minutes to cache candlestick data before re-fetching. Lower = fresher data, higher = fewer API calls.">
        <input type="number" min={1} max={60} value={cacheTtl}
          onChange={e => { setCacheTtl(Math.max(1, Math.min(60, parseInt(e.target.value) || 1))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">min</span>
      </Row>
      <Row label="Long/Short ratio" hint="Pull Binance Futures globalLongShortAccountRatio per observed symbol every 2 min. Filled into open_interest_snapshots.long_short_ratio and folded into crowding_score. USDT-margined linear perps only.">
        <Toggle checked={longShortRatio} persistence="staged" onChange={v => { setLongShortRatio(v); setMsg(null) }} />
      </Row>
      <Row label="Derivatives backfill" hint="Backfill funding/OI history on startup if the local window is sparse (idempotent — re-running with ≥3 rows in window is a no-op). 0 disables. Bitget OI history is not supported by CCXT; the path silently skips that exchange.">
        <input type="number" min={0} max={168} value={historyHours}
          onChange={e => { setHistoryHours(Math.max(0, Math.min(168, parseInt(e.target.value) || 0))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">hours (0 = off)</span>
      </Row>
      <Row label="Stock tokens" hint="Exchange-traded equity tokens for real-time price tracking. Supports Bitget r-series (rNVDA/USDT, rTSLA/USDT) and ON-series (AAPLON/USDT). Comma-separated. Used by Serenity for supply-chain research.">
        <input value={stockTokens} placeholder="rNVDA/USDT, rTSLA/USDT, AAPLON/USDT"
          onChange={e => { setStockTokens(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      <Row label="Prediction markets" hint="Polymarket category slugs to track. Comma-separated.">
        <input value={predMarkets} placeholder="crypto, politics"
          onChange={e => { setPredMarkets(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      {dirty && (
        <div className="flex justify-end" style={{ padding: '0.5rem 0' }}>
          <button onClick={() => mut.mutate()} disabled={mut.isPending || (createsDivergence && !confirmExchangeDivergence)}>
            {mut.isPending ? 'Saving…' : 'Save market data config'}
          </button>
        </div>
      )}
      <SaveMsg msg={msg} />
    </>
  )
}

function MacroSignalsCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const md = settings?.market_data || {}

  const [fearGreed, setFearGreed] = useState(md.enable_fear_greed ?? true)
  const [cgTop, setCgTop] = useState(md.enable_coingecko_top ?? true)
  const [cgTopN, setCgTopN] = useState(md.coingecko_top_n ?? 100)
  const [hyperliquid, setHyperliquid] = useState(md.enable_hyperliquid ?? true)
  const [comparisonVenues, setComparisonVenues] = useState<Array<'binance' | 'okx' | 'bitget'>>(
    md.derivatives_comparison_venues ?? ['binance', 'okx', 'bitget'],
  )
  const [defillama, setDefillama] = useState(md.enable_defillama ?? true)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      enable_fear_greed: fearGreed,
      enable_coingecko_top: cgTop,
      coingecko_top_n: cgTopN,
      enable_hyperliquid: hyperliquid,
      derivatives_comparison_venues: comparisonVenues,
      enable_defillama: defillama,
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = fearGreed !== (md.enable_fear_greed ?? true)
    || cgTop !== (md.enable_coingecko_top ?? true)
    || cgTopN !== (md.coingecko_top_n ?? 100)
    || hyperliquid !== (md.enable_hyperliquid ?? true)
    || JSON.stringify([...comparisonVenues].sort()) !== JSON.stringify([...(md.derivatives_comparison_venues ?? ['binance', 'okx', 'bitget'])].sort())
    || defillama !== (md.enable_defillama ?? true)

  useSettingsDraftLifecycle({
    id: 'macro-signals',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      setFearGreed(md.enable_fear_greed ?? true)
      setCgTop(md.enable_coingecko_top ?? true)
      setCgTopN(md.coingecko_top_n ?? 100)
      setHyperliquid(md.enable_hyperliquid ?? true)
      setComparisonVenues(md.derivatives_comparison_venues ?? ['binance', 'okx', 'bitget'])
      setDefillama(md.enable_defillama ?? true)
    },
  })

  return (
    <>
      <Row label="Fear & Greed Index" hint="Pull Alternative.me's crypto fear/greed index (single 0-100 value, updated daily). No API key. Surfaces in the macro block of enriched_context fed to the LLM.">
        <Toggle checked={fearGreed} persistence="staged" onChange={v => { setFearGreed(v); setMsg(null) }} />
      </Row>
      <Row label="CoinGecko top markets" hint="Pull top-N coins by market cap every ~10 min. Builds a 'Top Gainers / Top Losers' string folded into the macro block. No API key.">
        <Toggle checked={cgTop} persistence="staged" onChange={v => { setCgTop(v); setMsg(null) }} />
      </Row>
      <Row label="Top-N count" hint="How many coins to pull per tick (10-250). Higher = broader universe context; CoinGecko free tier caps at 30 req/min.">
        <input type="number" min={10} max={250} value={cgTopN}
          onChange={e => { setCgTopN(Math.max(10, Math.min(250, parseInt(e.target.value) || 10))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">coins</span>
      </Row>
      <Row label="Hyperliquid cross-venue" hint="Pull perp open-interest + funding from Hyperliquid's public API (no key) every ~10 min. Tagged as a cross-venue feed (kept separate from the primary exchange's per-symbol read), surfaced as cross-venue derivatives context.">
        <Toggle checked={hyperliquid} persistence="staged" onChange={v => { setHyperliquid(v); setMsg(null) }} />
      </Row>
      <Row label="Derivatives comparison sources" hint="Public, read-only Funding/OI and price coverage. These sources do not change trading venues or strategy inputs. Clear all to stop additional comparison requests; existing primary and Hyperliquid feeds remain enabled independently.">
        {(['binance', 'okx', 'bitget'] as const).map(venue => (
          <label key={venue} style={{ marginRight: '1rem' }}>
            <input type="checkbox" checked={comparisonVenues.includes(venue)}
              onChange={e => {
                setComparisonVenues(e.target.checked ? [...comparisonVenues, venue] : comparisonVenues.filter(v => v !== venue))
                setMsg(null)
              }} /> {venue}
          </label>
        ))}
      </Row>
      <Row label="DefiLlama on-chain" hint="Pull total stablecoin market cap, DeFi TVL, and DEX volume from DefiLlama's public API (no key) every ~10 min. Folded into the macro block of the LLM context as on-chain regime signal.">
        <Toggle checked={defillama} persistence="staged" onChange={v => { setDefillama(v); setMsg(null) }} />
      </Row>
      {dirty && (
        <div className="flex justify-end" style={{ padding: '0.5rem 0' }}>
          <button onClick={() => mut.mutate()} disabled={mut.isPending}>
            {mut.isPending ? 'Saving…' : 'Save macro signals config'}
          </button>
        </div>
      )}
      <SaveMsg msg={msg} />
    </>
  )
}

// ─── Prediction Markets Toggle ───

function PredictionToggleCard({ qc }: { qc: SettingsProps['qc'] }) {
  const statusQuery = useQuery({
    queryKey: ['prediction-status'],
    queryFn: api.getPredictionStatus,
  })
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const mut = useMutation({
    mutationFn: (enabled: boolean) => api.togglePrediction(enabled),
    onSuccess: (data) => {
      setMsg({ ok: true, text: data.enabled ? '✓ enabled' : '✓ disabled' })
      qc.invalidateQueries({ queryKey: ['prediction-status'] })
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const statusState = statusQuery.data
    ? statusQuery.isError ? 'stale' : 'available'
    : statusQuery.isPending ? 'loading' : 'unavailable'
  const enabled = statusQuery.data?.enabled === true
  const canToggle = statusState === 'available'

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      {statusState === 'available' || statusState === 'stale' ? (
        <Row label="Prediction observer" hint="When ON, the scheduler fetches Polymarket data every 30 min. When OFF, all prediction-related agent work stops.">
          <Toggle
            checked={enabled}
            persistence="immediate"
            onChange={v => mut.mutate(v)}
            disabled={mut.isPending || !canToggle}
          />
        </Row>
      ) : (
        <DataStateMessage
          state={statusState}
          message={statusState === 'loading'
            ? 'Prediction observer status is loading; the toggle is unavailable until a successful read.'
            : 'Prediction observer status unavailable; the toggle is unavailable until a successful read.'}
        />
      )}
      {statusState === 'stale' && (
        <DataStateMessage state="stale" message="Prediction observer status is stale. The retained value is shown for context, but changes are disabled until live status is confirmed." />
      )}
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// SIGNALS TAB — market data, macro signals, prediction markets
// ─────────────────────────────────────────────────────────────────────────────

export function SignalsTab({ settings, qc, onDirty, sectionId }: SettingsDirtyProps & { sectionId?: string }) {
  const isVisible = (id: string) => !sectionId || sectionId === id
  return (
    <div>
      {isVisible(MARKET_DATA_SECTION.id) && (
        <SettingsFeatureSection descriptor={MARKET_DATA_SECTION}>
          <Collapsible title="Data Source Overrides">
            <MarketDataCard settings={settings} qc={qc} onDirty={onDirty} />
          </Collapsible>
        </SettingsFeatureSection>
      )}

      {isVisible(MACRO_SIGNALS_SECTION.id) && (
        <SettingsFeatureSection descriptor={MACRO_SIGNALS_SECTION}>
          <MacroSignalsCard settings={settings} qc={qc} onDirty={onDirty} />
        </SettingsFeatureSection>
      )}

      {isVisible(PREDICTION_MARKETS_SECTION.id) && (
        <SettingsFeatureSection descriptor={PREDICTION_MARKETS_SECTION}>
          <PredictionToggleCard qc={qc} />
        </SettingsFeatureSection>
      )}
    </div>
  )
}
