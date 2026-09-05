import { Link } from 'react-router-dom'
import { fmtSymbol } from '../../lib/format'

interface SymbolLinkProps {
  symbol: string | null | undefined
  marketType?: string | null
  exchange?: string | null
  className?: string
  bold?: boolean
}

export function SymbolLink({ symbol, marketType = 'spot', exchange, className = '', bold = false }: SymbolLinkProps) {
  if (!symbol) return <span>—</span>

  const params = new URLSearchParams()
  params.set('symbol', symbol)
  if (marketType && marketType !== 'spot') params.set('market_type', marketType)
  if (exchange) params.set('exchange', exchange)

  const formatted = fmtSymbol(symbol, marketType)

  return (
    <Link
      to={`/charts?${params.toString()}`}
      className={`symbol-link mono ${bold ? 'font-bold' : ''} ${className}`}
      title={`Inspect ${formatted} on Markets`}
      onClick={e => e.stopPropagation()}
    >
      {formatted}
    </Link>
  )
}
