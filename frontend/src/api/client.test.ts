import { afterEach, describe, expect, expectTypeOf, it, vi } from 'vitest'
import type { components } from './generated/api'
import { ApiError, api, decodeOhlcvResponse, extractApiError, parseApiError, queryRetryDelay } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

it('uses the canonical writable Guard settings type', () => {
  expectTypeOf<Parameters<typeof api.updateGuardConfig>[0]>().toEqualTypeOf<components['schemas']['GuardConfigUpdate']>()
})

it("preserves opaque cursors and zero limits in query strings", async () => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")))
  vi.stubGlobal("fetch", fetchMock)
  const before = "2026-08-28T00:00:00+00:00|generation&next=1"
  await api.getPlanningHistory({ limit: 2, before })
  await api.getEntryWorkbench({ limit: 0 })
  const history = new URL(fetchMock.mock.calls[0][0], "http://fixture.invalid")
  expect(history.searchParams.get("before")).toBe(before)
  expect(history.searchParams.get("limit")).toBe("2")
  expect(new URL(fetchMock.mock.calls[1][0], "http://fixture.invalid").searchParams.get("limit")).toBe("0")
})

describe('Remote Control Token browser storage', () => {
  it('reports a successful write without returning the token', () => {
    const setItem = vi.fn()
    vi.stubGlobal('window', {
      localStorage: {
        getItem: vi.fn(),
        removeItem: vi.fn(),
        setItem,
      },
    })

    const result = api.setRemoteControlToken('  owner-token  ')

    expect(setItem).toHaveBeenCalledWith('aletheia.remoteControlToken', 'owner-token')
    expect(result).toEqual({ ok: true })
    expect(JSON.stringify(result)).not.toContain('owner-token')
  })

  it('reports browser storage rejection without exposing the token', () => {
    vi.stubGlobal('window', {
      localStorage: {
        getItem: vi.fn(),
        removeItem: vi.fn(),
        setItem: vi.fn(() => {
          throw new Error('storage disabled')
        }),
      },
    })

    const result = api.setRemoteControlToken('owner-token')

    expect(result).toEqual({
      ok: false,
      error: 'Browser storage is unavailable; the Remote Control Token was not saved.',
    })
    expect(JSON.stringify(result)).not.toContain('owner-token')
  })
})

describe('API errors', () => {
  it('honors a bounded Retry-After delay for query retries', () => {
    const error = new ApiError(503, 'capacity')
    error.retryAfter = '3'
    expect(queryRetryDelay(0, error)).toBe(3000)
    error.retryAfter = '999'
    expect(queryRetryDelay(0, error)).toBe(30000)
    expect(queryRetryDelay(0, new Error('other'))).toBe(1000)
  })

  it('requests usage insights for the selected supported window', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ window: { days: 30 } }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.getUsageInsights(30, 'summary')).resolves.toEqual({ window: { days: 30 } })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/economics/usage-insights?days=30&group=summary',
      expect.objectContaining({ headers: expect.objectContaining({ 'Content-Type': 'application/json' }) }),
    )
  })

  it('starts a pricing refresh through the dedicated economics endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'running' }), { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.refreshPricing()).resolves.toEqual({ status: 'running' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/economics/pricing/refresh',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('throws ApiError from the shared transport', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: 'rate_limited', message: 'Try later' },
    }), { status: 429, headers: { 'Retry-After': '3' } })))

    await expect(api.get('/test')).rejects.toMatchObject({
      name: 'ApiError',
      status: 429,
      code: 'rate_limited',
      message: 'Try later',
      retryAfter: '3',
    })
  })

  it('keeps the legacy message while retaining structured FastAPI detail', () => {
    const error = parseApiError(422, JSON.stringify({
      detail: {
        code: 'risk_gate_failed',
        message: 'Risk gate failed',
        details: { gate: 'exposure' },
        field_errors: { quantity: 'too large' },
      },
    }), new Headers({ 'X-Request-ID': 'request-42', 'Retry-After': '5' }))

    expect(error).toBeInstanceOf(Error)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe(extractApiError(422, JSON.stringify({ detail: { message: 'Risk gate failed' } })))
    expect(error).toMatchObject({
      status: 422,
      code: 'risk_gate_failed',
      details: { gate: 'exposure' },
      fieldErrors: { quantity: 'too large' },
      retryAfter: '5',
      correlationId: 'request-42',
    })
  })

  it('bounds long non-JSON bodies without retaining them as details', () => {
    const error = parseApiError(502, 'x'.repeat(81))

    expect(error.message).toBe('502 error')
    expect(error.details).toBeUndefined()
  })
})

describe('OHLCV response metadata', () => {
  it('retains source and computes as-of freshness at the client boundary', () => {
    const result = decodeOhlcvResponse(
      [{ time: 1_800, open: 1, high: 2, low: 0.5, close: 1.5, volume: 4 }],
      new Headers({
        'X-Aletheia-Data-Source': 'database_fallback',
        'X-Aletheia-Data-Instrument': 'bybit:BTC/USDT:spot:5m',
      }),
      '5m',
      2_500_000,
    )

    expect(result.meta).toMatchObject({
      source: 'database_fallback',
      instrument: 'bybit:BTC/USDT:spot:5m',
      asOf: '1970-01-01T00:30:00.000Z',
      ageMs: 700_000,
      stale: true,
    })
    expect(() => decodeOhlcvResponse({ candles: [] }, new Headers(), '5m')).toThrow(TypeError)
    expect(() => decodeOhlcvResponse([
      { time: 1_800, open: null, high: 2, low: 0.5, close: 1.5, volume: 4 },
    ], new Headers(), '5m')).toThrow(TypeError)
  })
})
