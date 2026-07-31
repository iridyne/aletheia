import { humanize } from '../lib/format'

export interface MeasuredPathSelectorProps {
  pathIds: string[]
  activePath: string
  generatedLabel: string
  onChange: (pathId: string) => void
}

export function MeasuredPathSelector({
  pathIds,
  activePath,
  generatedLabel,
  onChange,
}: MeasuredPathSelectorProps) {
  return (
    <div className="lco-path-selector">
      <label htmlFor="lco-measured-path">Measured path</label>
      <select
        id="lco-measured-path"
        value={activePath}
        onChange={event => onChange(event.target.value)}
      >
        {pathIds.map(pathId => (
          <option key={pathId} value={pathId}>{humanize(pathId)}</option>
        ))}
      </select>
      <span>Generated {generatedLabel}</span>
    </div>
  )
}
