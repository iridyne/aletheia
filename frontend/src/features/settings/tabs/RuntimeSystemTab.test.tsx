import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RuntimeSystemTab } from './RuntimeSystemTab'
import SRC from './RuntimeSystemTab.tsx?raw'

function renderRuntimeTab(updateStatus: Record<string, unknown> = { ok: false }) {
  const client = new QueryClient()
  client.setQueryData(['remote-control-interfaces'], { interfaces: [] })
  client.setQueryData(['update-status'], updateStatus)
  client.setQueryData(['export-catalog'], { datasets: [] })
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <RuntimeSystemTab
        status={{ version: '0.5.0', pid: 123, boot_commit: 'abc', current_commit: 'abc' }}
        settings={{ app: {}, runtime: {}, remote_control: {}, historical_data: {}, retention: {}, update: {}, network: {} }}
        qc={client}
      />
    </QueryClientProvider>,
  )
}

describe('RuntimeSystemTab', () => {
  it('preserves section order and deep-link anchors', () => {
    const html = renderRuntimeTab()
    const ids = ['app', 'data-retention', 'data-export', 'software-updates', 'network-proxy']
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
  })

  it('keeps runtime, retention, export, update, and proxy controls', () => {
    const html = renderRuntimeTab()

    expect(html).toContain('Running code commit')
    expect(html).toContain('Remote Control Token')
    expect(html).toContain('Historical Data')
    expect(html).toContain('Data Retention')
    expect(html).toContain('Export')
    expect(html).toContain('Auto-update enabled')
    expect(SRC).toContain('<HistoricalDataCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(SRC).toContain('<RetentionCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(SRC).toContain('<DataExportCard />')
    expect(html).toContain('Save config')
    expect(html).toContain('Enforce (fail-closed)')
    expect(html).toContain('Test Connection')
    expect(SRC).toContain('mutationFn: api.checkForUpdate')
    expect(SRC).toContain("onSuccess: result => qc.setQueryData(['update-status'], result)")
  })

  it('limits Usage-dependent retention to 90 days', () => {
    expect(SRC).toContain("key: 'agent_runs_days',          label: 'Agent runs',          unit: 'days', min: 1, max: 90")
    expect(SRC).toContain("key: 'tool_calls_days',          label: 'Tool calls',          unit: 'days', min: 1, max: 90")
    expect(SRC).toContain("key: 'runtime_sessions_days',    label: 'Runtime sessions',    unit: 'days', min: 1, max: 90")
  })

  it('projects Remote Control Token browser-storage success and failure', () => {
    expect(SRC).toContain('const [remoteTokenMessage, setRemoteTokenMessage]')
    expect(SRC).toContain('const result = api.setRemoteControlToken(remoteToken)')
    expect(SRC).toContain("role={remoteTokenMessage.ok ? 'status' : 'alert'}")
    expect(SRC).toContain("text: result.ok ? 'Token saved in this browser.' : result.error")
  })

  it('keeps immediate mutation errors isolated by source', () => {
    expect(SRC).toContain("type ImmediateErrorSource = 'app' | 'runtime' | 'remote'")
    expect(SRC).toContain("clearImmediateError('app')")
    expect(SRC).toContain("clearImmediateError('runtime')")
    expect(SRC).toContain("clearImmediateError('remote')")
  })

  it('renders source update evidence as CLI guidance without browser apply', () => {
    const html = renderRuntimeTab({
      ok: true,
      mode: 'development',
      has_update: true,
      behind_count: 2,
      branch: 'master',
      current_commit: '11111111',
      remote_commit: '22222222',
    })

    expect(html).toContain('2 behind')
    expect(html).toContain('master · 11111111')
    expect(html).toContain('22222222')
    expect(html).toContain('Run <code>aletheia update</code> from the controlling terminal')
    expect(html).not.toContain('Apply update</button>')
    expect(SRC).not.toContain('Pull latest code?')
  })

  it('keeps packaged update apply available', () => {
    const html = renderRuntimeTab({
      ok: true,
      mode: 'packaged',
      has_update: true,
      behind_count: 1,
      branch: 'stable',
      current_commit: 'aaaaaaaa',
      remote_commit: 'bbbbbbbb',
    })

    expect(html).toContain('Apply update</button>')
    expect(SRC).toContain('Apply packaged update?')
    expect(SRC).toContain('api.applyUpdate')
  })

  it('fails closed when update mode is missing or unknown', () => {
    for (const mode of [undefined, 'unknown']) {
      const html = renderRuntimeTab({
        ok: true,
        mode,
        has_update: true,
        behind_count: 1,
        branch: 'master',
        current_commit: '11111111',
        remote_commit: '22222222',
      })

      expect(html).toContain('Update apply action unavailable')
      expect(html).not.toContain('Apply update</button>')
    }
  })
})
