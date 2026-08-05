import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { AdvancedTab } from './AdvancedTab'

describe('AdvancedTab', () => {
  it('preserves the configuration matrix anchor and legacy table markup', () => {
    const html = renderToStaticMarkup(<AdvancedTab settings={{ health: { enabled: true } }} />)

    expect(html).toContain('id="settings-section-configuration-matrix"')
    expect(html).toContain('class="card mb-0"')
    expect(html).toContain('<th>Config key</th>')
    expect(html).toContain('<th>Setting surface</th>')
    expect(html).toContain('dedicated controls above remain the only editable surface')
  })

  it('classifies config rows and never renders sanitized credential fields', () => {
    const html = renderToStaticMarkup(<AdvancedTab settings={{
      health: { enabled: true },
      app: { theme: 'dark' },
      custom: { values: ['a', 'b'] },
      provider: { api_key: 'secret', oauth_token: 'token', oauth_refresh: 'refresh' },
    }} />)

    expect(html).toContain('health.enabled')
    expect(html).toContain('editable above')
    expect(html).toContain('app.theme')
    expect(html).toContain('read-only visible here')
    expect(html).toContain('custom.values')
    expect(html).toContain('read-only / config.yaml')
    expect(html).not.toContain('provider.api_key')
    expect(html).not.toContain('provider.oauth_token')
    expect(html).not.toContain('provider.oauth_refresh')
    expect(html).not.toContain('secret')
    expect(html).not.toContain('token')
    expect(html).not.toContain('refresh')
  })
})
