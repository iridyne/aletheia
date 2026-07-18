import { useSyncExternalStore } from 'react'

/**
 * Global display settings + number formatting.
 *
 * Owner rule (2026-06-07): every number that has no integer-only requirement is shown with
 * a fixed number of decimals (default 2) — so 0 renders "0.00", 8 renders "8.00". The
 * decimal count is a user-adjustable setting (Settings → Display), persisted to localStorage.
 *
 * Counts (positions, runs, items) keep their integer rendering — pass {decimals: 0} or use
 * the raw value for those; this module is for monetary / continuous values.
 */

/**
 * Strip HTML tags and decode entities from text fields that may come from RSS feeds.
 * Used as a display-layer fallback for data already in the DB before the collector was fixed.
 */
export function stripHtml(text: string | null | undefined): string {
  if (!text) return ''
  return text
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

const DECIMALS_KEY = 'aletheia.display.decimals'
const DEFAULT_DECIMALS = 2
const MIN_DECIMALS = 0
const MAX_DECIMALS = 8

function clampDecimals(n: number): number {
  if (!Number.isFinite(n)) return DEFAULT_DECIMALS
  return Math.min(MAX_DECIMALS, Math.max(MIN_DECIMALS, Math.round(n)))
}

function readDecimals(): number {
  try {
    const raw = localStorage.getItem(DECIMALS_KEY)
    if (raw == null) return DEFAULT_DECIMALS
    return clampDecimals(Number(raw))
  } catch {
    return DEFAULT_DECIMALS
  }
}

// --- tiny external store so every component re-renders when the setting changes ---
const listeners = new Set<() => void>()
function emit() { listeners.forEach(l => l()) }

export function getDecimals(): number {
  return readDecimals()
}

export function setDecimals(n: number): void {
  const v = clampDecimals(n)
  try { localStorage.setItem(DECIMALS_KEY, String(v)) } catch { /* ignore */ }
  emit()
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

/** React hook — current decimal places, reactive to changes from the Settings page. */
export function useDecimals(): number {
  return useSyncExternalStore(subscribe, getDecimals, () => DEFAULT_DECIMALS)
}

export const DECIMAL_BOUNDS = { min: MIN_DECIMALS, max: MAX_DECIMALS, default: DEFAULT_DECIMALS }

/**
 * Format a numeric value with the configured fixed decimals (thousands-separated).
 * Returns the `dash` placeholder for null/undefined/NaN. Fixed decimals means 0 -> "0.00".
 */
export function fmtNum(
  value: number | string | null | undefined,
  opts?: { decimals?: number; dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  const d = opts?.decimals ?? getDecimals()
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
}

/** Same as fmtNum but with an explicit +/- sign for non-negative values (PnL deltas). */
export function fmtSigned(
  value: number | string | null | undefined,
  opts?: { decimals?: number; dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  return `${n >= 0 ? '+' : ''}${fmtNum(n, opts)}`
}

/**
 * Format a 0-1 ratio (e.g. `0.83`) OR an already-scaled percent (e.g. `83`) as `83%`.
 * Auto-detects by magnitude (|v| ≤ 1 ⇒ ratio, ×100; |v| > 1 ⇒ already %). Returns dash
 * for null/NaN. Decimals default to 0 (terminal density); pass `{decimals: 1}` for
 * finer micro-values.
 */
export function fmtPct(
  value: number | string | null | undefined,
  opts?: { decimals?: number; dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  const pct = Math.abs(n) <= 1 ? n * 100 : n
  const d = opts?.decimals ?? 0
  return `${pct.toFixed(d)}%`
}

/** fmtSigned with ▲/▼ direction arrow prepended (WCAG 1.4.1 — not color-only). */
export function fmtDelta(
  value: number | string | null | undefined,
  opts?: { decimals?: number; dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  const arrow = n > 0 ? '▲' : n < 0 ? '▼' : ''
  return `${arrow}${n >= 0 ? '+' : ''}${fmtNum(n, opts)}`
}

// ---------- Machine-code humanization ----------
// Backend enums/states arrive as snake_case machine tokens (e.g. "paper_running",
// "live_disabled", "mean_reversion"). The design system requires humanized display.
// A small override map handles domain acronyms/phrases; everything else gets a generic
// snake_case → Title Case transform. One source of truth for the whole dashboard.

const HUMANIZE_OVERRIDES: Record<string, string> = {
  // lifecycle states
  draft: 'Draft',
  paper_candidate: 'Paper Candidate',
  paper_running: 'Paper Running',
  paper_passed: 'Paper Passed',
  live_candidate: 'Live Candidate',
  live_limited: 'Live Limited',
  retired: 'Retired',
  deprecated: 'Deprecated',
  // live / permission / exchange modes
  live_disabled: 'Disabled',
  live_enabled: 'Enabled',
  paper_internal: 'Paper (internal)',
  bitget_demo: 'Bitget Demo',
  bitget_live_limited: 'Bitget Live (limited)',
  okx_demo: 'OKX Demo',
  okx_live: 'OKX Live',
  usdt_futures: 'USDT-M Perp',
  spot: 'Spot',
  // run / task statuses
  completed_empty: 'Completed (no output)',
  completed_with_errors: 'Completed (errors)',
  rate_limited: 'Rate Limited',
  in_progress: 'In Progress',
}

/**
 * Humanize a raw machine code/enum for display.
 * `paper_running` → "Paper Running", `live_disabled` → "Disabled", `RSI` stays upper if all-caps.
 * Returns the dash placeholder for null/empty.
 */
export function humanize(value: string | null | undefined, dash = '—'): string {
  if (value == null || value === '') return dash
  const key = String(value).trim()
  if (HUMANIZE_OVERRIDES[key]) return HUMANIZE_OVERRIDES[key]
  // Preserve already-spaced/cased free text but still title-case snake_case tokens.
  return key
    .replace(/_/g, ' ')
    .split(' ')
    .map(w => (w.length > 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ')
}

/** ON/OFF for booleans (design rule: never show literal true/false). */
export function humanizeBool(value: unknown): string {
  return value ? 'ON' : 'OFF'
}

// ---------- Exchange / venue from execution_mode ----------
// execution_mode encodes venue + account flavor ("okx_demo", "bitget_live_limited").
// The blotter surfaces the venue as its own column (UTA: positions across multiple
// exchanges coexist), so we map mode → exchange here. demo/live stays visible in the
// account snapshot + position detail drawer.
const EXCHANGE_OF_MODE: Record<string, string> = {
  paper_internal: 'Paper',
  bitget_demo: 'Bitget',
  bitget_live_limited: 'Bitget',
  okx_demo: 'OKX',
  okx_live: 'OKX',
  okx_live_limited: 'OKX',
  binance_testnet: 'Binance',
  binance_live_limited: 'Binance',
}

/** Exchange/venue name derived from a position/order execution_mode. */
export function exchangeOf(executionMode: string | null | undefined, dash = '—'): string {
  if (!executionMode) return dash
  const k = String(executionMode).trim()
  if (EXCHANGE_OF_MODE[k]) return EXCHANGE_OF_MODE[k]
  const head = k.split('_')[0]
  return head ? head.charAt(0).toUpperCase() + head.slice(1) : dash
}

// ---------- Instrument symbol display ----------
// Project-wide convention (owner rule, 2026-06-26): the symbol's slash IS the
// spot-vs-derivative signal, so it must never contradict the instrument type.
//   - Perpetual / futures  → NO slash   (BTCUSDT)
//   - Spot                 → slash       (BTC/USDT)
// Symbols are stored canonically with a slash (BTC/USDT) everywhere internally
// (aletheia/data/symbols.py); this is a pure DISPLAY transform — strip the slash
// only when we positively know the instrument is a derivative. Unknown market
// type keeps the canonical (slashed) form rather than guessing.

const PERP_MARKET_TYPES = new Set([
  'usdt_futures', 'usdc_futures', 'coin_futures',
  'perp', 'perpetual', 'futures', 'swap',
])

/** True when a backend market_type / market string denotes a derivative (no-slash display). */
export function isPerpMarket(marketType: string | null | undefined): boolean {
  if (!marketType) return false
  return PERP_MARKET_TYPES.has(String(marketType).trim().toLowerCase())
}

/**
 * Display an instrument symbol per the project's spot/derivative convention.
 * Perp/futures → concatenated (BTCUSDT); spot or unknown → canonical slash (BTC/USDT).
 * Returns the dash placeholder for null/empty.
 */
export function fmtSymbol(
  symbol: string | null | undefined,
  marketType?: string | null,
  dash = '—',
): string {
  if (symbol == null || symbol === '') return dash
  const s = String(symbol).trim()
  if (!s) return dash
  return isPerpMarket(marketType) ? s.replace(/\//g, '') : s
}

/**
 * Badge tone for a market_type chip. Categorical (not a status): spot = blue
 * (calm — no leverage/funding), perp/futures = yellow (carries leverage + funding,
 * a mild attention cue). Used by the Trades & Positions blotter Market column.
 */
export function marketBadgeTone(marketType: string | null | undefined): 'blue' | 'yellow' | 'muted' {
  if (!marketType) return 'muted'
  return isPerpMarket(marketType) ? 'yellow' : 'blue'
}

/**
 * Consistent timestamp formatting across the whole dashboard.
 * Backend sends ISO strings (sometimes without timezone). Output: "MMM D, HH:mm"
 * (local), e.g. "Jun 14, 09:32". Returns dash for null/invalid.
 * Pass {dateOnly:true} for "MMM D, YYYY".
 */
/**
 * Normalize a backend ISO timestamp so JS Date parses it as UTC.
 *
 * Backend timestamps come in two shapes: tz-aware (`...+00:00` or `...Z`) and
 * naive (`...T14:03:25`, implicitly UTC). Only the naive form needs a `Z`
 * appended. The historical bug: appending `Z` to an already-`+00:00` string
 * produces `...+00:00Z`, which Date parses to NaN — silently dropping every
 * tz-aware point (the Equity-Curve "no data" bug). Mirror the guard every other
 * caller uses: `endsWith('Z') || includes('+')`.
 */
export function normalizeIso(iso: string): string {
  return iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`
}

/** Parse a backend ISO timestamp to epoch SECONDS, or null if unparseable. */
export function isoToEpochSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = new Date(normalizeIso(iso)).getTime()
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000)
}

export function fmtTimestamp(
  iso: string | null | undefined,
  opts?: { dash?: string; dateOnly?: boolean },
): string {
  const dash = opts?.dash ?? '—'
  if (!iso) return dash
  const norm = normalizeIso(iso)
  const d = new Date(norm)
  if (Number.isNaN(d.getTime())) return dash
  if (opts?.dateOnly) {
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** Relative age: "just now", "5m ago", "2h ago"; falls back to fmtTimestamp beyond 24h. */
export function fmtAge(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ms = new Date(normalizeIso(iso)).getTime()
  if (Number.isNaN(ms)) return '—'
  const diffSec = Math.floor((Date.now() - ms) / 1000)
  if (diffSec < 60) return 'just now'
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  return fmtTimestamp(iso)
}
