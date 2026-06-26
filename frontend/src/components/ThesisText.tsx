/**
 * Formats Market Observer thesis/synthesis text into structured, readable blocks.
 *
 * Thesis texts follow the pattern:
 *   "Main thesis paragraph. Observations: point1 | point2 | point3. score=N/100"
 *
 * This component splits observations into a list and extracts the score badge.
 */
export default function ThesisText({ text, className }: { text: string; className?: string }) {
  if (!text) return null

  const scoreMatch = text.match(/\bscore\s*=\s*(\d+)\/100\s*$/)
  const score = scoreMatch ? parseInt(scoreMatch[1], 10) : null
  const body = scoreMatch ? text.slice(0, scoreMatch.index).trim() : text

  const obsIdx = body.search(/\bObservations?:\s*/i)
  let mainText: string
  let observations: string[] = []

  if (obsIdx >= 0) {
    mainText = body.slice(0, obsIdx).trim()
    const obsRaw = body.slice(obsIdx).replace(/^Observations?:\s*/i, '')
    observations = obsRaw
      .split('|')
      .map(s => s.trim())
      .filter(Boolean)
  } else {
    mainText = body
  }

  return (
    <div className={`thesis-text ${className || ''}`}>
      <p className="thesis-main">{mainText}</p>
      {observations.length > 0 && (
        <ul className="thesis-observations">
          {observations.map((obs, i) => (
            <li key={i}>{obs}</li>
          ))}
        </ul>
      )}
      {score != null && (
        <span className={`thesis-score ${score >= 70 ? 'high' : score >= 50 ? 'mid' : 'low'}`}>
          Score: {score}/100
        </span>
      )}
    </div>
  )
}
