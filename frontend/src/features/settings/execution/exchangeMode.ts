export function executionExchangeFromPaperMode(mode: string, configuredExchange = ''): string | null {
  if (mode === 'paper_internal') return null
  const byMode: Record<string, string> = {
    bitget_demo: 'bitget',
    binance_testnet: 'binance',
    binance_live: 'binance',
    okx_demo: 'okx',
    okx_live: 'okx',
  }
  return byMode[mode] || configuredExchange || null
}
