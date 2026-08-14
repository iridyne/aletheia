import { describe, expect, it } from 'vitest'
import { INSTRUMENT_CATALOG, scoreInstrument, searchInstruments } from './instrumentCatalog'

describe('scoreInstrument', () => {
  it('scores exact base match highest', () => {
    const btcBitgetSpot = INSTRUMENT_CATALOG.find(entry =>
      entry.symbol === 'BTC/USDT' && entry.exchange === 'bitget' && entry.marketType === 'spot')!
    const ethBitgetSpot = INSTRUMENT_CATALOG.find(entry =>
      entry.symbol === 'ETH/USDT' && entry.exchange === 'bitget' && entry.marketType === 'spot')!
    expect(scoreInstrument(btcBitgetSpot, 'BTC', 'bitget')).toBeGreaterThan(scoreInstrument(ethBitgetSpot, 'BTC', 'bitget'))
  })

  it('boosts default exchange in a tie', () => {
    const btcBitget = INSTRUMENT_CATALOG.find(entry =>
      entry.symbol === 'BTC/USDT' && entry.exchange === 'bitget' && entry.marketType === 'spot')!
    const btcBinance = INSTRUMENT_CATALOG.find(entry =>
      entry.symbol === 'BTC/USDT' && entry.exchange === 'binance' && entry.marketType === 'spot')!
    expect(scoreInstrument(btcBitget, 'BTC', 'bitget')).toBeGreaterThan(scoreInstrument(btcBinance, 'BTC', 'bitget'))
  })

  it('resolves display-name and alias matches', () => {
    const btc = INSTRUMENT_CATALOG.find(entry =>
      entry.symbol === 'BTC/USDT' && entry.exchange === 'bitget' && entry.marketType === 'spot')!
    expect(scoreInstrument(btc, 'Bitcoin', 'bitget')).toBeGreaterThan(0)
    expect(scoreInstrument(btc, 'Ripple', 'bitget')).toBe(-1)
  })

  it('rejects entries missing the term entirely', () => {
    const eth = INSTRUMENT_CATALOG.find(entry => entry.symbol === 'ETH/USDT' && entry.exchange === 'bitget')!
    expect(scoreInstrument(eth, 'XRP', 'bitget')).toBe(-1)
  })
})

describe('searchInstruments', () => {
  it('returns [] for empty term', () => {
    expect(searchInstruments('', 'bitget')).toEqual([])
    expect(searchInstruments('   ', 'bitget')).toEqual([])
  })

  it('returns Bitget BTC entries at the top when Bitget is the default exchange', () => {
    const rows = searchInstruments('BTC', 'bitget', 10)
    expect(rows[0].symbol.startsWith('BTC/')).toBe(true)
    expect(rows[0].exchange).toBe('bitget')
  })

  it('resolves the AAPL alias to the Apple xStock entry', () => {
    const rows = searchInstruments('AAPL', 'bitget')
    expect(rows[0].symbol).toBe('AAPLX/USDT')
    expect(rows[0].exchange).toBe('bitget')
  })
})
