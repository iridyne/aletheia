import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { MeasuredPathSelector } from './MeasuredPathSelector'

describe('MeasuredPathSelector', () => {
  it('renders every measured path as one labelled native keyboard control', () => {
    const html = renderToStaticMarkup(
      <MeasuredPathSelector
        pathIds={['universe_family_calibration']}
        activePath="universe_family_calibration"
        generatedLabel="Jul 31"
        onChange={() => undefined}
      />,
    )

    expect(html).toContain('<label for="lco-measured-path">Measured path</label>')
    expect(html).not.toContain('aria-label=')
    expect(html).toContain('value="universe_family_calibration"')
    expect(html).toContain('value="universe_family_calibration" selected=""')
  })
})
