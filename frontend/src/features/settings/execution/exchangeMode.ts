export function executionExchangeFromPaperMode(mode: string): string | null {
  if (!mode) return null
  const byMode: Record<string, string> = {
    bitget_demo: 'bitget',
    binance_testnet: 'binance',
    bitget_live_limited: 'bitget',
    binance_live_limited: 'binance',
    okx_demo: 'okx',
    okx_live_limited: 'okx',
  }
  return byMode[mode] || null
}
