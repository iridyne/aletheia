import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import {
  CollapsibleSection as Collapsible,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  Switch as Toggle,
} from '../../../components/ui'
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

export const NEWS_FEEDS_SECTION = {
  id: 'news-feeds', title: 'News Feeds', tab: 'signals',
  description: 'Automated RSS news ingestion. Feeds are fetched periodically and displayed on the News Intelligence page.',
  searchTerms: ['rss', 'ingestion'],
} satisfies SettingsFeatureDescriptor

export const PREDICTION_MARKETS_SECTION = {
  id: 'prediction-markets', title: 'Prediction Markets', tab: 'signals',
  description: 'Polymarket observer — fetches probability data for information-edge signals. Observation-only, never routed to execution.',
  searchTerms: ['polymarket', 'categories'],
} satisfies SettingsFeatureDescriptor

export const EQUITIES_SECTION = {
  id: 'observed-us-equities', title: 'Observed US Equities', tab: 'signals',
  description: 'US equity tickers for equity_observer (observation-only, not traded). Requires Bitget xStock support for the ticker.',
  searchTerms: ['xstocks', 'equity'],
} satisfies SettingsFeatureDescriptor

function MarketDataCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const md = settings?.market_data || {}
  const exchangeSettings = settings?.exchange || {}
  const EXCHANGES = ['binance', 'bybit', 'okx', 'bitget', 'gate', 'kucoin', 'htx']

  const [primary, setPrimary] = useState(md.ccxt_exchange || 'binance')
  const [fallback, setFallback] = useState(md.ccxt_fallback || 'bybit')
  const [cacheTtl, setCacheTtl] = useState(md.ohlcv_cache_ttl_min ?? 10)
  const [yfinance, setYfinance] = useState(!!md.enable_yfinance_macro)
  const [longShortRatio, setLongShortRatio] = useState(md.enable_long_short_ratio ?? true)
  const [historyHours, setHistoryHours] = useState(md.derivatives_history_hours ?? 24)
  const [stocks, setStocks] = useState(listToCsv(md.observed_stocks))
  const [stockTokens, setStockTokens] = useState(listToCsv(md.observed_stock_tokens))
  const [predMarkets, setPredMarkets] = useState(listToCsv(md.observed_prediction_markets))
  const [confirmExchangeDivergence, setConfirmExchangeDivergence] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const executionExchange = executionExchangeFromPaperMode(
    String(exchangeSettings.paper_execution_mode || ''),
    String(exchangeSettings.name || ''),
  )
  const createsDivergence = executionExchange !== null
    && primary !== executionExchange
    && primary !== String(md.ccxt_exchange || 'binance')

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      ccxt_exchange: primary,
      ccxt_fallback: fallback,
      ohlcv_cache_ttl_min: cacheTtl,
      enable_yfinance_macro: yfinance,
      enable_long_short_ratio: longShortRatio,
      derivatives_history_hours: historyHours,
      observed_stocks: parseCsvList(stocks),
      observed_stock_tokens: parseCsvList(stockTokens),
      observed_prediction_markets: parseCsvList(predMarkets),
      confirm_exchange_divergence: confirmExchangeDivergence,
    }),
    onSuccess: () => { setConfirmExchangeDivergence(false); setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = primary !== (md.ccxt_exchange || 'binance')
    || fallback !== (md.ccxt_fallback || 'bybit')
    || cacheTtl !== (md.ohlcv_cache_ttl_min ?? 10)
    || yfinance !== !!md.enable_yfinance_macro
    || longShortRatio !== (md.enable_long_short_ratio ?? true)
    || historyHours !== (md.derivatives_history_hours ?? 24)
    || stocks !== listToCsv(md.observed_stocks)
    || stockTokens !== listToCsv(md.observed_stock_tokens)
    || predMarkets !== listToCsv(md.observed_prediction_markets)

  useSettingsDraftLifecycle({
    id: 'market-data',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      setPrimary(md.ccxt_exchange || 'binance')
      setFallback(md.ccxt_fallback || 'bybit')
      setCacheTtl(md.ohlcv_cache_ttl_min ?? 10)
      setYfinance(!!md.enable_yfinance_macro)
      setLongShortRatio(md.enable_long_short_ratio ?? true)
      setHistoryHours(md.derivatives_history_hours ?? 24)
      setStocks(listToCsv(md.observed_stocks))
      setStockTokens(listToCsv(md.observed_stock_tokens))
      setPredMarkets(listToCsv(md.observed_prediction_markets))
      setConfirmExchangeDivergence(false)
    },
  })

  return (
    <>
      <Row label="Primary exchange" hint="CCXT exchange ID used for OHLCV and ticker data. Must match a supported CCXT exchange.">
        <select value={primary} onChange={e => { setPrimary(e.target.value); setConfirmExchangeDivergence(false); setMsg(null) }} className="w-auto">
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      <Row label="Fallback exchange" hint="Used when the primary exchange fails or returns stale data.">
        <select value={fallback} onChange={e => { setFallback(e.target.value); setMsg(null) }} className="w-auto">
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      {createsDivergence && (
        <div className="settings-save-msg err mb-2">
          <Toggle
            checked={confirmExchangeDivergence}
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
      <Row label="Yahoo Finance macro" hint="Enable macro indicator collection (DXY, VIX, S&P 500) via yfinance. Requires network access to Yahoo.">
        <Toggle checked={yfinance} onChange={v => { setYfinance(v); setMsg(null) }} />
      </Row>
      <Row label="Long/Short ratio" hint="Pull Binance Futures globalLongShortAccountRatio per observed symbol every 2 min. Filled into open_interest_snapshots.long_short_ratio and folded into crowding_score. USDT-margined linear perps only.">
        <Toggle checked={longShortRatio} onChange={v => { setLongShortRatio(v); setMsg(null) }} />
      </Row>
      <Row label="Derivatives backfill" hint="Backfill funding/OI history on startup if the local window is sparse (idempotent — re-running with ≥3 rows in window is a no-op). 0 disables. Bitget OI history is not supported by CCXT; the path silently skips that exchange.">
        <input type="number" min={0} max={168} value={historyHours}
          onChange={e => { setHistoryHours(Math.max(0, Math.min(168, parseInt(e.target.value) || 0))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">hours (0 = off)</span>
      </Row>
      <Row label="Observed stocks" hint="Yahoo Finance tickers for macro correlation. Comma-separated (e.g. SPY, QQQ, NVDA).">
        <input value={stocks} placeholder="SPY, QQQ, NVDA, AAPL"
          onChange={e => { setStocks(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      <Row label="Stock tokens" hint="Exchange-traded equity tokens for real-time price tracking. Supports Bitget r-series (rNVDA/USDT, rTSLA/USDT) and ON-series (AAPLON/USDT). Comma-separated. Used by equity_observer for cross-validation and Serenity for supply-chain research.">
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
  const [defillama, setDefillama] = useState(md.enable_defillama ?? true)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      enable_fear_greed: fearGreed,
      enable_coingecko_top: cgTop,
      coingecko_top_n: cgTopN,
      enable_hyperliquid: hyperliquid,
      enable_defillama: defillama,
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = fearGreed !== (md.enable_fear_greed ?? true)
    || cgTop !== (md.enable_coingecko_top ?? true)
    || cgTopN !== (md.coingecko_top_n ?? 100)
    || hyperliquid !== (md.enable_hyperliquid ?? true)
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
      setDefillama(md.enable_defillama ?? true)
    },
  })

  return (
    <>
      <Row label="Fear & Greed Index" hint="Pull Alternative.me's crypto fear/greed index (single 0-100 value, updated daily). No API key. Surfaces in the macro block of enriched_context fed to the LLM.">
        <Toggle checked={fearGreed} onChange={v => { setFearGreed(v); setMsg(null) }} />
      </Row>
      <Row label="CoinGecko top markets" hint="Pull top-N coins by market cap every ~10 min. Builds a 'Top Gainers / Top Losers' string folded into the macro block. No API key.">
        <Toggle checked={cgTop} onChange={v => { setCgTop(v); setMsg(null) }} />
      </Row>
      <Row label="Top-N count" hint="How many coins to pull per tick (10-250). Higher = broader universe context; CoinGecko free tier caps at 30 req/min.">
        <input type="number" min={10} max={250} value={cgTopN}
          onChange={e => { setCgTopN(Math.max(10, Math.min(250, parseInt(e.target.value) || 10))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">coins</span>
      </Row>
      <Row label="Hyperliquid cross-venue" hint="Pull perp open-interest + funding from Hyperliquid's public API (no key) every ~10 min. Tagged as a cross-venue feed (kept separate from the primary exchange's per-symbol read), surfaced as cross-venue derivatives context.">
        <Toggle checked={hyperliquid} onChange={v => { setHyperliquid(v); setMsg(null) }} />
      </Row>
      <Row label="DefiLlama on-chain" hint="Pull total stablecoin market cap, DeFi TVL, and DEX volume from DefiLlama's public API (no key) every ~10 min. Folded into the macro block of the LLM context as on-chain regime signal.">
        <Toggle checked={defillama} onChange={v => { setDefillama(v); setMsg(null) }} />
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
  const { data: status } = useQuery({
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

  const enabled = status?.enabled ?? false

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <Row label="Prediction observer" hint="When ON, the scheduler fetches Polymarket data every 30 min. When OFF, all prediction-related agent work stops.">
        <Toggle checked={enabled} onChange={v => mut.mutate(v)} disabled={mut.isPending} />
      </Row>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Equities ───

function EquitiesCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const serverTickers: string[] = settings?.market_data?.observed_equities ?? []
  const [draft, setDraft] = useState<string[]>(serverTickers)
  const [input, setInput] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const mut = useMutation({
    mutationFn: () => api.updateEquities(draft),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const add = () => {
    const t = input.trim().toUpperCase()
    if (!t || draft.includes(t)) return
    setDraft(prev => [...prev, t]); setInput(''); setMsg(null)
  }
  const remove = (t: string) => { setDraft(prev => prev.filter(x => x !== t)); setMsg(null) }

  const dirty = JSON.stringify(draft.slice().sort()) !== JSON.stringify(serverTickers.slice().sort())

  useSettingsDraftLifecycle({
    id: 'equities',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => setDraft(serverTickers),
  })

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="flex flex-wrap gap-1 mb-3" style={{ minHeight: '2rem' }}>
        {draft.map(t => (
          <span key={t} className="symbol-chip allow">
            {t}
            <button onClick={() => remove(t)} title={`Remove ${t}`}>×</button>
          </span>
        ))}
        {draft.length === 0 && (
          <span className="hint" style={{ margin: 0 }}>No tickers — equity_observer will not run.</span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={input} placeholder="e.g. AAPL"
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          style={{ width: '120px', fontFamily: 'var(--mono)', textTransform: 'uppercase' }}
        />
        <button className="secondary" onClick={add}>Add</button>
        {dirty && (
          <button onClick={() => mut.mutate()} disabled={mut.isPending}>
            {mut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        )}
      </div>
      <SaveMsg msg={msg} />
      <p className="hint">
        US equity tickers observed by equity_observer (yfinance daily bars + Bitget xStock price deviation).
        Observation-only — not traded. Changes take effect on next agent cycle (Mon–Fri market hours).
      </p>
    </div>
  )
}

// ─── News Feeds config ───

function NewsFeedsCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const news = settings?.news as any
  const [enabled, setEnabled] = useState(true)
  const [interval, setInterval_] = useState(15)
  const [feeds, setFeeds] = useState<any[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const mut = useMutation({
    mutationFn: () => api.updateNewsConfig({
      enabled,
      fetch_interval_minutes: interval,
      feeds: feeds.map((f: any) => ({ name: f.name, url: f.url, source: f.source, enabled: f.enabled })),
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = !!news && (
    enabled !== (news.enabled ?? true)
    || interval !== (news.fetch_interval_minutes ?? 15)
    || JSON.stringify(feeds) !== JSON.stringify(news.feeds ?? [])
  )

  useSettingsDraftLifecycle({
    id: 'news-feeds',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      if (!news) return
      setEnabled(news.enabled ?? true)
      setInterval_(news.fetch_interval_minutes ?? 15)
      setFeeds(news.feeds ?? [])
    },
  })

  const toggleFeed = (idx: number) => {
    setFeeds(prev => prev.map((f, i) => i === idx ? { ...f, enabled: !f.enabled } : f))
    setMsg(null)
  }

  if (!news) return null

  return (
    <div className="card mb-0">
      <Row label="News ingestion" hint="Master switch for automatic RSS feed collection.">
        <Toggle checked={enabled} onChange={setEnabled} />
      </Row>
      <Row label="Fetch interval" hint="Minutes between feed checks (5–120).">
        <input type="number" min={5} max={120} step={5} value={interval}
          onChange={e => { setInterval_(Number(e.target.value)); setMsg(null) }}
          className="mono" style={{ width: '70px' }}
        />
      </Row>
      <div className="section-label mt-4">Feeds</div>
      <div className="news-feeds-grid">
        {feeds.map((f: any, i: number) => (
          <div key={f.url} className="news-feed-row">
            <Toggle checked={f.enabled} aria-label={`Enable ${f.name}`} onChange={() => toggleFeed(i)} />
            <div className="news-feed-info">
              <span className="news-feed-name">{f.name}</span>
              <span className="news-feed-source text-muted text-xs">{f.source}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="settings-action-row">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save news config'}</button>
      </div>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// SIGNALS TAB — market data, news, macro signals, prediction markets, equities
// ─────────────────────────────────────────────────────────────────────────────

export function SignalsTab({ settings, qc, onDirty }: SettingsDirtyProps) {
  return (
    <div>
      <SettingsFeatureSection descriptor={MARKET_DATA_SECTION}>
        <Collapsible title="Data Source Overrides">
          <MarketDataCard settings={settings} qc={qc} onDirty={onDirty} />
        </Collapsible>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={MACRO_SIGNALS_SECTION}>
        <MacroSignalsCard settings={settings} qc={qc} onDirty={onDirty} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={NEWS_FEEDS_SECTION}>
        <NewsFeedsCard settings={settings} qc={qc} onDirty={onDirty} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={PREDICTION_MARKETS_SECTION}>
        <PredictionToggleCard qc={qc} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={EQUITIES_SECTION}>
        <EquitiesCard settings={settings} qc={qc} onDirty={onDirty} />
      </SettingsFeatureSection>
    </div>
  )
}
