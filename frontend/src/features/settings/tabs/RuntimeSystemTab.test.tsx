import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RuntimeSystemTab } from './RuntimeSystemTab'
import SRC from './RuntimeSystemTab.tsx?raw'

function renderRuntimeTab() {
  const client = new QueryClient()
  client.setQueryData(['remote-control-interfaces'], { interfaces: [] })
  client.setQueryData(['update-status'], { ok: false })
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
    expect(SRC).toContain('<HistoricalDataCard settings={settings} qc={qc} />')
    expect(SRC).toContain('<RetentionCard settings={settings} qc={qc} />')
    expect(SRC).toContain('<DataExportCard />')
    expect(html).toContain('Save config')
    expect(html).toContain('Enforce (fail-closed)')
    expect(html).toContain('Test Connection')
  })

  it('projects Remote Control Token browser-storage success and failure', () => {
    expect(SRC).toContain('const [remoteTokenMessage, setRemoteTokenMessage]')
    expect(SRC).toContain('const result = api.setRemoteControlToken(remoteToken)')
    expect(SRC).toContain("role={remoteTokenMessage.ok ? 'status' : 'alert'}")
    expect(SRC).toContain("text: result.ok ? 'Token saved in this browser.' : result.error")
  })
})
