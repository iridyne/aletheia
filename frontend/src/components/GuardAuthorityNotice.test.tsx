import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { GuardAuthorityNotice } from './GuardAuthorityNotice'

describe('GuardAuthorityNotice', () => {
  it('names learned guards as experiment-gated observation even with legacy auto-promote enabled', () => {
    const html = renderToStaticMarkup(<GuardAuthorityNotice legacyAutoPromoteEnabled />)

    expect(html).toContain('Learned guards remain observe-only by default')
    expect(html).toContain('exact prospective experiment treatment assignment')
    expect(html).toContain('cannot promote or unshadow learned guard candidates')
    expect(html).not.toContain('block future trades without human review')
  })
})
