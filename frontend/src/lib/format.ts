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
