import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RuntimeSystemTab } from './RuntimeSystemTab'

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
    expect(html).toContain('Save config')
    expect(html).toContain('Enforce (fail-closed)')
    expect(html).toContain('Test Connection')
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
  })

  it("shows a held source release without claiming it is deployed", () => {
    const html = renderRuntimeTab({
      mode: "source_release", status: "hold", phase: "observer_hold",
      hold: true, reason: "observer_advanced", target_tag: "v9.1.0", target_sha: "abcdef012345",
    })
    expect(html).toContain("observer_advanced")
    expect(html).toContain("v9.1.0")
    expect(html).toContain("abcdef01")
    expect(html).not.toContain("up to date")
    expect(html).not.toContain("Auto-update enabled")
    expect(html).not.toContain("Apply update</button>")
  })

  it('never offers an apply action, whatever the reported update mode', () => {
    // Updating is CLI-only: the API route refuses to mutate source and the
    // packaged staging flow it used to hand off to was removed. There is no mode
    // for which the dashboard applies an update itself.
    for (const mode of [undefined, 'unknown', 'development', 'source_release']) {
      const html = renderRuntimeTab({
        ok: true,
        mode,
        has_update: true,
        behind_count: 1,
        branch: 'master',
        current_commit: '11111111',
        remote_commit: '22222222',
      })

      expect(html).not.toContain('Apply update</button>')
      expect(html).toContain('Run <code>aletheia update</code> from the controlling terminal')
    }
  })
})
