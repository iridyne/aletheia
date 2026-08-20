/**
 * Risk violation formatting — single source of truth.
 *
 * The backend (`aletheia/risk/engine.py` + the `/api/risk/status` route) emits machine
 * violation codes such as `max_daily_loss_exceeded:-5.97399875<=-1.0` or
 * `stale_market_data:age=3394.675959`. Rendering them raw leaks internal codes and
 * full-precision floats and wraps mid-token. Every surface that shows violations
 * (Overview risk rail, Risk & Controls page, Proposals verdict/drawer) routes through
 * `formatViolation` so the wording, rounding, and severity stay consistent.
 */

export type RiskSeverity = 'block' | 'warn'

export interface FormattedViolation {
  /** Human-readable headline, e.g. "Daily loss limit hit". */
  label: string
  /** Compact, rounded detail, e.g. "−$5.97 vs −$1 limit". Null when the code carries no argument. */
  detail: string | null
  /** `block` = trading-halting (kill switch / daily loss / mode mismatch); `warn` = advisory. */
  severity: RiskSeverity
}

const RISK_LABELS: Record<string, string> = {
  kill_switch_active: 'Kill switch engaged',
  spot_market_disabled: 'Spot market disabled',
  futures_market_disabled: 'Futures market disabled',
  live_disabled: 'Live trading disabled',
  live_disabled_while_exchange_live: 'Live disabled while exchange is live',
  short_not_allowed_live: 'Shorting not allowed (live)',
  short_not_allowed_demo: 'Shorting not allowed (demo)',
  max_single_trade_risk_missing_stop_price: 'Missing stop-loss price',
}

const RISK_STATE_LABELS: Record<string, string> = {
  blocked: 'Blocked',
  warning: 'Warning',
  healthy: 'Healthy',
  not_evaluated: 'Not evaluated',
  not_available: 'Unavailable',
  unknown: 'Unknown',
}

/** Title-case a raw risk status code for display (e.g. "not_evaluated" → "Not evaluated"). */
export function riskStateLabel(state: string): string {
  return RISK_STATE_LABELS[state] || state
}

function fmtUsd(n: number): string {
  if (!Number.isFinite(n)) return '—'
  const sign = n < 0 ? '−' : '' // U+2212 minus, not hyphen, for aligned monospace
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}

function fmtAge(sec: number): string {
  if (!Number.isFinite(sec)) return 'age unknown'
  if (sec < 90) return `${Math.round(sec)}s old`
  const m = sec / 60
  if (m < 90) return `${Math.round(m)}m old`
  return `${(m / 60).toFixed(1)}h old`
}

function humanizeCode(code: string): string {
  const s = code.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Parse one raw risk violation code into a display label + compact detail + severity. */
export function formatViolation(raw: string): FormattedViolation {
  const idx = raw.indexOf(':')
  const code = idx === -1 ? raw : raw.slice(0, idx)
  const arg = idx === -1 ? '' : raw.slice(idx + 1)
  const severity: RiskSeverity =
    code === 'kill_switch_active' || code === 'max_daily_loss_exceeded' || code.startsWith('account_mode_mismatch')
      ? 'block'
      : 'warn'

  const gt = (s: string): [number, number] => {
    const [a, b] = s.split('>')
    return [Number(a), Number(b)]
  }

  if (code.startsWith('stale_market_data')) {
    const m = arg.match(/age=([\d.]+|None)/)
    const age = m?.[1]
    return { label: 'Market data stale', detail: age && age !== 'None' ? fmtAge(Number(age)) : 'age unknown', severity }
  }
  if (code === 'max_daily_loss_exceeded') {
    const [realized, limit] = arg.split('<=').map(Number)
    return { label: 'Daily loss limit hit', detail: `${fmtUsd(realized)} vs ${fmtUsd(limit)} limit`, severity }
  }
  if (code === 'symbol_blocked') return { label: 'Symbol blocked', detail: arg, severity }
  if (code === 'symbol_not_allowed') return { label: 'Symbol not in whitelist', detail: arg, severity }
  if (code === 'permission_mode_insufficient' || code === 'permission_mode_not_live_eligible')
    return { label: 'Permission mode insufficient', detail: arg, severity }
  if (code === 'exceeds_max_position_margin' || code === 'exceeds_demo_position_margin') {
    const [n, cap] = gt(arg)
    return { label: 'Position size over cap', detail: `${fmtUsd(n)} > ${fmtUsd(cap)} cap`, severity }
  }
  if (code === 'max_live_positions_reached' || code === 'max_demo_positions_reached')
    return { label: 'Max open positions reached', detail: `${arg} open`, severity }
  if (code === 'account_mode_mismatch')
    return { label: 'Account mode mismatch', detail: arg.replace(/,/g, ' · '), severity }
  if (code === 'exceeds_max_leverage') {
    const [lev, cap] = gt(arg)
    return { label: 'Leverage over cap', detail: `${lev}× > ${cap}× cap`, severity }
  }
  if (code === 'max_total_live_exposure_exceeded') {
    const [proj, cap] = gt(arg)
    return { label: 'Live exposure over cap', detail: `${fmtUsd(proj)} > ${fmtUsd(cap)} cap`, severity }
  }
  if (code === 'exceeds_single_trade_risk') {
    const [amt, max] = gt(arg)
    return { label: 'Single-trade risk over cap', detail: `${fmtUsd(amt)} > ${fmtUsd(max)} cap`, severity }
  }
  return { label: RISK_LABELS[code] || humanizeCode(code), detail: arg || null, severity }
}

/** Compact one-line form, e.g. "Daily loss limit hit — −$5.97 vs −$1 limit". For inline/badge use. */
export function formatViolationText(raw: string): string {
  const f = formatViolation(raw)
  return f.detail ? `${f.label} — ${f.detail}` : f.label
}
