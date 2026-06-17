export function ConvictionMeter({ conviction, debateRan, bull, bear }: {
  conviction: number; debateRan: boolean; bull: number | null; bear: number | null
}) {
  const color = conviction >= 70 ? 'var(--green)' : conviction >= 55 ? 'var(--yellow)' : 'var(--red)'
  return (
    <div className="conviction-meter">
      <div className="conviction-bar-wrap">
        <div className="conviction-bar" style={{ width: `${conviction}%`, background: color }} />
      </div>
      <span className="conviction-label mono">{conviction}/100</span>
      {debateRan && (
        <span className="conviction-debate mono">
          Bull {bull ?? '?'} / Bear {bear ?? '?'}
        </span>
      )}
    </div>
  )
}
