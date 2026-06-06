type MetricTileProps = {
  label: string
  value: string | number
  color?: string
}

export default function MetricTile({ label, value, color }: MetricTileProps) {
  return (
    <div className="metric-tile">
      <span className="metric-label">{label}</span>
      <span className="metric-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  )
}
