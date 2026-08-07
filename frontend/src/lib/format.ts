/**
 * Global display formatting.
 *
 * fmtNum picks the shape automatically from the value's magnitude:
 *
 *   |v| ≥ 1                    → thousands-separated, 2 fraction digits ("12,345.67", "1.00")
 *   0 < |v| < 1, leading 0s <3 → 4 significant digits, expanded ("0.5000", "0.0123", "0.001234")
 *   leading 0s ≥ 3             → subscript-zero notation ("0.0₃1234", "0.0₅3527")
 *   0                          → "0.00"
 *   NaN / null / ""            → "—" (or opts.dash)
 *
 * Subscript characters are Unicode U+2080–U+2089 (₀-₉). Multi-digit runs of
 * zeros use multi-character subscripts ("0.0₁₉1000" for 1e-20). This matches the
 * convention used by OKX / DEX Screener / CoinGecko / CoinMarketCap so extreme-
 * small memecoin prices ($0.0000000027) stay legible without falsely rendering
 * as "0.00".
 *
 * There is no user-configurable "decimal places" setting — the rule above IS the
 * setting. `fmtPct` keeps its own toFixed logic since percentages rarely need
 * subscript treatment.
 */

const SUBSCRIPT_DIGITS = '₀₁₂₃₄₅₆₇₈₉'
const SIG_DIGITS = 4
const SUBSCRIPT_THRESHOLD = 3

function subscriptOf(n: number): string {
  return String(n)
    .split('')
    .map(d => SUBSCRIPT_DIGITS[Number(d)] ?? d)
    .join('')
}

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

/**
 * Format a numeric value per the magnitude-adaptive rule described in the module header.
 * Returns the `dash` placeholder for null/undefined/NaN.
 */
export function fmtNum(
  value: number | string | null | undefined,
  opts?: { dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash

  if (n === 0) return '0.00'

  const abs = Math.abs(n)

  if (abs >= 1) {
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  // 0 < abs < 1 — compute leading-zero count via toExponential.
  // toExponential(SIG_DIGITS - 1) → "3.527e-6" for 0.0000035 (4 sig figs).
  const [mantissaStr, expPartStr] = abs.toExponential(SIG_DIGITS - 1).split('e')
  const exp = Number.parseInt(expPartStr, 10)

  // Rounding up can push a sub-1 value to ≥1 (e.g. 0.99999 → 1.000e+0). Route to Case A.
  if (exp >= 0) {
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  const sign = n < 0 ? '-' : ''
  const leadingZeros = -exp - 1
  const mantissaDigits = mantissaStr.replace('.', '')

  if (leadingZeros < SUBSCRIPT_THRESHOLD) {
    return sign + abs.toFixed(leadingZeros + SIG_DIGITS)
  }

  return `${sign}0.0${subscriptOf(leadingZeros)}${mantissaDigits}`
}

/** Same as fmtNum but with an explicit + sign for non-negative values (PnL deltas). */
export function fmtSigned(
  value: number | string | null | undefined,
  opts?: { dash?: string },
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
 * finer micro-values. Percentages don't participate in subscript-zero notation.
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

/**
 * Format a bounded 0-100 score without implying that it is a probability.
 * Unlike fmtPct, this never appends "%": model confidence and scanner rank
 * scores are ordinal evidence summaries unless a separate calibration contract
 * explicitly proves probabilistic semantics.
 */
export function fmtScore100(
  value: number | string | null | undefined,
  opts?: { dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  const rounded = Math.round((n + Number.EPSILON) * 100) / 100
  const rendered = Number.isInteger(rounded)
    ? String(rounded)
    : rounded.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
  return `${rendered}/100`
}

/** Universe emits a composite ranking score; observer agents emit confidence scores. */
export function opportunityScoreLabel(sourceAgent: string | null | undefined): string {
  return sourceAgent === 'universe_scanner' ? 'Scanner score' : 'Confidence score'
}

/** Complete, source-aware Opportunity score text for compact surfaces. */
export function fmtOpportunityScore(
  sourceAgent: string | null | undefined,
  score: number | null | undefined,
  categorical?: string | null,
): string {
  if (score != null) return `${opportunityScoreLabel(sourceAgent)} ${fmtScore100(score)}`
  return categorical ? `${opportunityScoreLabel(sourceAgent)} ${categorical}` : 'Score not available'
}

/**
 * Space-constrained numeric formatter for metric tiles where the display
 * budget is a fixed ~7 character cell (roughly "6 digits + 1 decimal point"
 * in mono at 20.8px). Uses SI unit suffixes (k / M / B / T) so a tile
 * never overflows when the integer part grows; keeps the subscript-zero
 * notation for very small decimals.
 *
 * Budget by magnitude (numeric part only; sign is separate):
 *
 *   0                    → "0.00"        (4 chars)
 *   0 < |v| < 1
 *     leading zeros < 3  → "0.234"       (3 sig figs, expanded)
 *                        → "0.0234"
 *                        → "0.00234"
 *     leading zeros ≥ 3  → "0.0₃234"     (3 sig figs, subscript)
 *   1 ≤ |v| < 10         → "1.2346"      (4 decimals)
 *   10 ≤ |v| < 100       → "12.346"      (3 decimals)
 *   100 ≤ |v| < 1_000    → "123.46"      (2 decimals)
 *   1_000 ≤ |v| < 10_000 → "1.235k"      (k suffix)
 *   ... etc for M (10^6), B (10^9), T (10^12)
 *
 * The precision loss versus fmtNum is deliberate — a metric tile is a
 * glance value; the drill-down tables use fmtNum for full precision.
 *
 * Companion: fmtCompactSigned prepends `+` on non-negative values (the
 * `−` sign is already baked in by JS number → string).
 */
const COMPACT_SI: [number, string][] = [
  [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k'],
]

export function fmtCompact(
  value: number | string | null | undefined,
  opts?: { dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  if (n === 0) return '0.00'

  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''

  // Large magnitudes: SI suffix + 3 sig figs (yields 5-6 chars incl. suffix).
  for (const [threshold, suffix] of COMPACT_SI) {
    if (abs >= threshold) {
      const scaled = abs / threshold
      // 3 significant figures: 1.23 / 12.3 / 123
      const decimals = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2
      return `${sign}${scaled.toFixed(decimals)}${suffix}`
    }
  }

  // 1 ≤ |v| < 1000: allocate decimals inversely to integer width so the
  // total digit count stays at 5 (matches "6 digits + 1 point" budget).
  if (abs >= 1) {
    const intDigits = Math.floor(Math.log10(abs)) + 1
    const decimals = Math.max(0, 5 - intDigits)  // 1.xxxx / 12.xxx / 123.xx
    return `${sign}${abs.toFixed(decimals)}`
  }

  // 0 < |v| < 1 — reuse fmtNum's leading-zero rule but with 3 sig figs
  // (fmtNum uses 4; tightening by 1 saves a character on the tile).
  const SIG = 3
  const [mantissaStr, expPartStr] = abs.toExponential(SIG - 1).split('e')
  const exp = Number.parseInt(expPartStr, 10)
  if (exp >= 0) {
    // Rounded up to ≥1, e.g. 0.9999 → 1.00. Route via decimal-allocation.
    const rounded = Number(mantissaStr) * 10 ** exp
    const intDigits = Math.floor(Math.log10(rounded)) + 1
    const decimals = Math.max(0, 5 - intDigits)
    return `${sign}${rounded.toFixed(decimals)}`
  }
  const leadingZeros = -exp - 1
  const mantissaDigits = mantissaStr.replace('.', '')
  if (leadingZeros < SUBSCRIPT_THRESHOLD) {
    return sign + abs.toFixed(leadingZeros + SIG)
  }
  return `${sign}0.0${subscriptOf(leadingZeros)}${mantissaDigits}`
}

/** fmtCompact with an explicit `+` on non-negative values (delta/PnL style). */
export function fmtCompactSigned(
  value: number | string | null | undefined,
  opts?: { dash?: string },
): string {
  const dash = opts?.dash ?? '—'
  if (value == null || value === '') return dash
  const n = Number(value)
  if (!Number.isFinite(n)) return dash
  return `${n >= 0 ? '+' : ''}${fmtCompact(n, opts)}`
}

/** fmtSigned with ▲/▼ direction arrow prepended (WCAG 1.4.1 — not color-only). */
export function fmtDelta(
  value: number | string | null | undefined,
  opts?: { dash?: string },
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
