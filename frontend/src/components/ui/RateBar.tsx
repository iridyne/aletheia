/**
 * RateBar — 10-stick success-rate visualisation (AKRouter-style).
 *
 * Renders 10 vertical bars (3px × 16px each), coloured by the --rate-* tokens.
 * The number of filled bars is proportional to the rate value (0–100).
 * Tone is determined by the overall rate level:
 *   ≥99%  → ok    (green)
 *   ≥95%  → warn  (yellow)
 *   ≥90%  → bad   (orange)
 *   <90%  → crit  (red)
 */
type RateTone = 'ok' | 'warn' | 'bad' | 'crit'

function rateTone(rate: number): RateTone {
  if (rate >= 99) return 'ok'
  if (rate >= 95) return 'warn'
  if (rate >= 90) return 'bad'
  return 'crit'
}

export function RateBar({ rate, label }: { rate: number | null; label?: string }) {
  if (rate === null || rate === undefined) {
    return <span className="text-muted">—</span>
  }
  const filled = Math.max(0, Math.min(10, Math.round(rate / 10)))
  const tone = rateTone(rate)
  const title = label ?? `${rate.toFixed(1)}%`
  return (
    <span
      className="rate-bar"
      title={title}
      aria-label={title}
      role="img"
    >
      {Array.from({ length: 10 }, (_, i) => (
        <span
          key={i}
          className={`rate-bar-unit${i < filled ? ` ${tone}` : ''}`}
        />
      ))}
    </span>
  )
}
