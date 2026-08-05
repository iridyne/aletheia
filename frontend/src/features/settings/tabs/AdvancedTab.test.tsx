import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  RISK_LEGACY_POLICY_KEYS, RISK_POLICY_BOOLS,
  RISK_POLICY_LIVE_FIELDS, RISK_POLICY_NUM_FIELDS,
} from '../risk/riskFields'
import { AdvancedTab } from './AdvancedTab'

function valueTree(paths: string[]): Record<string, unknown> {
  const root: Record<string, unknown> = {}
  for (const path of paths) {
    const parts = path.split('.')
    let cursor = root
    for (const part of parts.slice(0, -1)) {
      const child = (cursor[part] ?? {}) as Record<string, unknown>
      cursor[part] = child
      cursor = child
    }
    cursor[parts[parts.length - 1]] = 1
  }
  return root
}

function configRow(html: string, key: string): string | undefined {
  return html.match(new RegExp(
    `<tr><td class="mono">${key.replace(/\./g, '\\.')}</td>[\\s\\S]*?</tr>`,
  ))?.[0]
}

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

  it('classifies the current v2 risk-policy controls instead of dead legacy fields', () => {
    const commonPaths = [...RISK_POLICY_NUM_FIELDS, ...RISK_POLICY_BOOLS].map(field => field.key)
    const livePaths = [...commonPaths, ...RISK_POLICY_LIVE_FIELDS.map(field => field.key)]
    const editablePaths = [
      ...commonPaths.map(path => `risk.demo.${path}`),
      ...livePaths.map(path => `risk.live.${path}`),
    ]
    const risk = {
      demo: valueTree(commonPaths),
      live: valueTree(livePaths),
      ...Object.fromEntries(RISK_LEGACY_POLICY_KEYS.map(key => [key, 1])),
    }
    const html = renderToStaticMarkup(<AdvancedTab settings={{ risk }} />)

    for (const path of editablePaths) {
      const row = configRow(html, path)
      expect(row, `missing Configuration Matrix row for ${path}`).toBeDefined()
      expect(row).toContain('editable above')
      expect(row).not.toContain('config.yaml')
    }

    for (const legacyKey of RISK_LEGACY_POLICY_KEYS) {
      const key = `risk.${legacyKey}`
      const row = configRow(html, key)
      expect(row, `missing Configuration Matrix row for ${key}`).toBeDefined()
      expect(row).toContain('legacy compatibility / not authoritative')
      expect(row).not.toContain('editable above')
    }
  })
})
