import { describe, expect, it } from 'vitest'
import SRC from './PaperTradingSettings.tsx?raw'

describe('CapitalEventsCard — read-failure semantic honesty', () => {
  it('extracts isError and isLoading from both queries (no silent ?? 0 fallback)', () => {
    expect(SRC).toContain('isLoading: eventsLoading')
    expect(SRC).toContain('isError: eventsError')
    expect(SRC).toContain('isLoading: netLoading')
    expect(SRC).toContain('isError: netError')
    // The old silent fallbacks must be gone
    expect(SRC).not.toContain('data: events = []')
    // Old pattern: const netDeposits = netData?.net_deposits_usdt ?? 0 (bare, not inside a ternary)
    expect(SRC).not.toMatch(/const netDeposits\s*=\s*netData\?\.net_deposits_usdt/)
  })

  it('net deposits shows not available on error, never a bare 0', () => {
    expect(SRC).toContain("netError\n      ? 'not available'")
    expect(SRC).toContain('netDepositsDisplay')
    // Displayed string is the computed variable, not a hardcoded toFixed(2) on a ?? 0
    expect(SRC).toContain('{netDepositsDisplay}')
  })

  it('exposes retry buttons for both failed queries', () => {
    expect(SRC).toContain('refetch: refetchNet')
    expect(SRC).toContain('refetch: refetchEvents')
    expect(SRC).toContain('onClick={() => refetchNet()}')
    expect(SRC).toContain('onClick={() => refetchEvents()}')
  })

  it('events table only renders on explicit success, not on error or loading', () => {
    expect(SRC).toContain('Capital events unavailable.')
    expect(SRC).toContain('Loading capital events…')
    // Table only rendered when not loading and not errored
    expect(SRC).toContain('!eventsLoading && !eventsError && (events as any[]).length > 0')
  })
})
