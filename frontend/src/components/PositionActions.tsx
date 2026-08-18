import { Link } from 'react-router-dom'
import type { Position } from '../types'
import { Button, useToast } from './ui'

// Top-of-drawer action row for Position Detail: copy the raw source payload
// AND deep-link into a position-aware chart view. Kept as a small component so
// the copy path and the deep-link URL can be unit-tested without spinning up
// the whole TradesPositions page.

export function positionChartHref(position: Position): string {
  const params = new URLSearchParams()
  params.set('symbol', position.symbol)
  params.set('market_type', position.market_type || 'spot')
  params.set('position_id', position.id)
  return `/charts?${params.toString()}`
}

export function PositionActions({ position }: { position: Position }) {
  const { toast } = useToast()

  const copyFullJson = async () => {
    // Copy the SOURCE payload — decision_reasoning, exit_reasoning, IDs,
    // timestamps, pricing, PnL, costs — untouched. Never an AI-generated
    // summary; that would silently drop audit fields.
    const payload = JSON.stringify(position, null, 2)
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(payload)
      } else {
        throw new Error('clipboard unavailable')
      }
      toast('success', 'Position JSON copied to clipboard')
    } catch {
      toast('error', 'Could not copy — clipboard access blocked')
    }
  }

  return (
    <div className="drawer-top-actions">
      <Button variant="secondary" size="sm" onClick={copyFullJson}>
        Copy Full JSON
      </Button>
      <Link
        to={positionChartHref(position)}
        className="btn btn-secondary btn-sm"
        aria-label="Open position on chart"
      >
        View on Chart
      </Link>
    </div>
  )
}
