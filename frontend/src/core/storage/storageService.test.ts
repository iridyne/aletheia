// @vitest-environment happy-dom
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { StorageService } from './storageService'

describe('StorageService (Framework-Agnostic Core)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('reads fallback when key is not set', () => {
    expect(StorageService.get('aletheia.sidebar.rail.collapsed', false)).toBe(false)
    expect(StorageService.get('aletheia.sidebar.rail.collapsed', true)).toBe(true)
  })

  it('writes and reads boolean primitives safely', () => {
    StorageService.set('aletheia.sidebar.rail.collapsed', true)
    expect(StorageService.get('aletheia.sidebar.rail.collapsed', false)).toBe(true)

    StorageService.set('aletheia.sidebar.rail.collapsed', false)
    expect(StorageService.get('aletheia.sidebar.rail.collapsed', true)).toBe(false)
  })

  it('writes and reads complex objects/records safely', () => {
    const tableCols = { symbol: true, pnl: false }
    StorageService.set('dt-cols:trades', tableCols)
    expect(StorageService.get('dt-cols:trades', {})).toEqual(tableCols)
  })

  it('handles invalid JSON gracefully and returns fallback or raw string', () => {
    window.localStorage.setItem('aletheia.charts.settings.v1', '{invalid-json')
    expect(() => StorageService.get('aletheia.charts.settings.v1', { fallback: true })).not.toThrow()
  })

  it('removes keys properly', () => {
    StorageService.set('aletheia.prediction.enabled.cache', true)
    expect(StorageService.get('aletheia.prediction.enabled.cache', false)).toBe(true)

    StorageService.remove('aletheia.prediction.enabled.cache')
    expect(StorageService.get('aletheia.prediction.enabled.cache', false)).toBe(false)
  })

  it('supports raw string read/write without JSON quoting', () => {
    StorageService.setString('aletheia_remote_token', 'secret-token-123')
    expect(StorageService.getString('aletheia_remote_token')).toBe('secret-token-123')
    expect(window.localStorage.getItem('aletheia_remote_token')).toBe('secret-token-123')
  })

  it('handles storage exceptions gracefully when quota is exceeded or storage is disabled', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => StorageService.set('aletheia.sidebar.rail.collapsed', true)).not.toThrow()
  })
})
