/**
 * Meter — a data-driven horizontal bar fill.
 *
 * Encapsulates the one legitimate inline style a page needs for a progress/headroom
 * bar (a dynamic, computed `width: N%`). Keeping it in this primitive lets pages stay
 * free of ad-hoc `style={{}}` (enforced by test_p3_pages_use_shared_primitives) while
 * still rendering data-driven widths. The caller supplies the track via its own CSS
 * class; this renders the fill span with the clamped width.
 */
interface MeterProps {
  /** Fill percentage 0–100 (clamped). */
  pct: number
  /** CSS class for the fill span, e.g. "headroom-bar-fill momentum" or "exposure-bar pos". */
  className?: string
}

export function Meter({ pct, className }: MeterProps) {
  const clamped = Math.max(0, Math.min(100, pct))
  return <span className={className} style={{ width: `${clamped}%` }} />
}
