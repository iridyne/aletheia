export type AssetClass = 'crypto_spot' | 'crypto_perp' | 'dex' | 'equity_token' | 'commodity'

export interface CatalogEntry {
  symbol: string
  marketType: string
  exchange: string
  assetClass: AssetClass
  displayName: string
  aliases?: string[]
}

const CRYPTO_MAJORS: Array<Omit<CatalogEntry, 'exchange' | 'marketType' | 'assetClass'> & { base: string; kinds: Array<'spot' | 'perp'> }> = [
  { symbol: 'BTC/USDT', displayName: 'Bitcoin', aliases: ['Bitcoin', 'XBT'], base: 'BTC', kinds: ['spot', 'perp'] },
  { symbol: 'ETH/USDT', displayName: 'Ethereum', aliases: ['Ethereum', 'Ether'], base: 'ETH', kinds: ['spot', 'perp'] },
  { symbol: 'SOL/USDT', displayName: 'Solana', aliases: ['Solana'], base: 'SOL', kinds: ['spot', 'perp'] },
  { symbol: 'BNB/USDT', displayName: 'BNB', aliases: ['BNB', 'Binance Coin'], base: 'BNB', kinds: ['spot'] },
  { symbol: 'XRP/USDT', displayName: 'XRP', aliases: ['Ripple', 'XRP'], base: 'XRP', kinds: ['spot'] },
  { symbol: 'DOGE/USDT', displayName: 'Dogecoin', aliases: ['Doge', 'Dogecoin'], base: 'DOGE', kinds: ['spot'] },
  { symbol: 'ADA/USDT', displayName: 'Cardano', aliases: ['Cardano', 'ADA'], base: 'ADA', kinds: ['spot'] },
  { symbol: 'AVAX/USDT', displayName: 'Avalanche', aliases: ['Avalanche', 'AVAX'], base: 'AVAX', kinds: ['spot'] },
  { symbol: 'LINK/USDT', displayName: 'Chainlink', aliases: ['Chainlink', 'LINK'], base: 'LINK', kinds: ['spot', 'perp'] },
  { symbol: 'MATIC/USDT', displayName: 'Polygon', aliases: ['Polygon', 'MATIC'], base: 'MATIC', kinds: ['spot', 'perp'] },
  { symbol: 'APT/USDT', displayName: 'Aptos', aliases: ['Aptos'], base: 'APT', kinds: ['perp'] },
  { symbol: 'ARB/USDT', displayName: 'Arbitrum', aliases: ['Arbitrum', 'ARB'], base: 'ARB', kinds: ['perp'] },
  { symbol: 'OP/USDT', displayName: 'Optimism', aliases: ['Optimism', 'OP'], base: 'OP', kinds: ['perp'] },
  { symbol: 'SUI/USDT', displayName: 'Sui', aliases: ['Sui'], base: 'SUI', kinds: ['perp'] },
  { symbol: 'SHIB/USDT', displayName: 'Shiba Inu', aliases: ['Shiba', 'SHIB'], base: 'SHIB', kinds: ['perp'] },
  { symbol: 'DOT/USDT', displayName: 'Polkadot', aliases: ['Polkadot', 'DOT'], base: 'DOT', kinds: ['spot'] },
  { symbol: 'TON/USDT', displayName: 'Toncoin', aliases: ['Toncoin', 'TON'], base: 'TON', kinds: ['spot'] },
]

const EXCHANGES = ['bitget', 'binance', 'okx'] as const

function toPerpSymbol(spot: string): string {
  const [base, quote] = spot.split('/')
  return `${base}/${quote}:${quote}`
}

function buildCryptoEntries(): CatalogEntry[] {
  const out: CatalogEntry[] = []
  for (const asset of CRYPTO_MAJORS) {
    for (const kind of asset.kinds) {
      const symbol = kind === 'perp' ? toPerpSymbol(asset.symbol) : asset.symbol
      const assetClass: AssetClass = kind === 'perp' ? 'crypto_perp' : 'crypto_spot'
      const marketType = kind === 'perp' ? 'perp' : 'spot'
      for (const exchange of EXCHANGES) {
        out.push({
          symbol,
          marketType,
          exchange,
          assetClass,
          displayName: asset.displayName,
          aliases: asset.aliases,
        })
      }
    }
  }
  return out
}

const DEX_ENTRIES: CatalogEntry[] = [
  { symbol: 'WBTC/USDC', marketType: 'spot', exchange: 'uniswap', assetClass: 'dex', displayName: 'Wrapped Bitcoin', aliases: ['WBTC'] },
  { symbol: 'SOL/USDC', marketType: 'spot', exchange: 'jupiter', assetClass: 'dex', displayName: 'Solana on Jupiter', aliases: ['Solana'] },
]

const EQUITY_ENTRIES: CatalogEntry[] = [
  { symbol: 'AAPLX/USDT', marketType: 'spot', exchange: 'bitget', assetClass: 'equity_token', displayName: 'Apple xStock', aliases: ['Apple', 'AAPL'] },
  { symbol: 'TSLAX/USDT', marketType: 'spot', exchange: 'binance', assetClass: 'equity_token', displayName: 'Tesla xStock', aliases: ['Tesla', 'TSLA'] },
  { symbol: 'NVDAX/USDT', marketType: 'spot', exchange: 'okx', assetClass: 'equity_token', displayName: 'Nvidia xStock', aliases: ['Nvidia', 'NVDA'] },
]

const COMMODITY_ENTRIES: CatalogEntry[] = [
  { symbol: 'PAXG/USDT', marketType: 'spot', exchange: 'bitget', assetClass: 'commodity', displayName: 'PAX Gold', aliases: ['Gold', 'PAXG'] },
  { symbol: 'PAXG/USDT', marketType: 'spot', exchange: 'binance', assetClass: 'commodity', displayName: 'PAX Gold', aliases: ['Gold', 'PAXG'] },
  { symbol: 'XAUT/USDT', marketType: 'spot', exchange: 'bitget', assetClass: 'commodity', displayName: 'Tether Gold', aliases: ['Gold', 'XAUT'] },
]

export const INSTRUMENT_CATALOG: readonly CatalogEntry[] = Object.freeze([
  ...buildCryptoEntries(),
  ...DEX_ENTRIES,
  ...EQUITY_ENTRIES,
  ...COMMODITY_ENTRIES,
])

export function scoreInstrument(entry: CatalogEntry, term: string, defaultExchange: string): number {
  const query = term.trim().toLowerCase()
  const [rawBase] = entry.symbol.split('/')
  const base = rawBase.toLowerCase()
  const symbol = entry.symbol.toLowerCase()
  const name = entry.displayName.toLowerCase()
  const aliases = (entry.aliases || []).map(alias => alias.toLowerCase())

  let score = 0
  if (query) {
    if (base === query) score += 1000
    else if (aliases.some(alias => alias === query)) score += 800
    else if (symbol.startsWith(query)) score += 500
    else if (name.includes(query)) score += 300
    else if (symbol.includes(query)) score += 100
    else if (aliases.some(alias => alias.includes(query))) score += 60
    else return -1
  } else {
    score += 200
  }
  if (entry.exchange === defaultExchange.toLowerCase()) score += 50
  if (entry.marketType === 'perp') score += 20
  if (entry.assetClass === 'crypto_spot' || entry.assetClass === 'crypto_perp') score += 10
  score -= entry.symbol.length
  return score
}

export function searchInstruments(term: string, defaultExchange: string, limit = 20): CatalogEntry[] {
  if (!term.trim()) return []
  return INSTRUMENT_CATALOG
    .map(entry => ({ entry, score: scoreInstrument(entry, term, defaultExchange) }))
    .filter(row => row.score >= 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, limit)
    .map(row => row.entry)
}
