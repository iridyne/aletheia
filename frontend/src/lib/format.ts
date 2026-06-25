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
  usdt_futures: 'USDT Futures',
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
