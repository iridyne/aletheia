// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { ConfirmProvider } from '../../../components/ui'
import { RuntimeSystemTab } from './RuntimeSystemTab'
import SRC from './RuntimeSystemTab.tsx?raw'

function testClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  })
}

function renderRuntimeTab() {
  const client = testClient()
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

let root: Root | undefined
let container: HTMLElement

function button(text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button')).find(
    candidate => candidate.textContent === text,
  )
  expect(found).toBeDefined()
  return found as HTMLButtonElement
}

function lastButton(text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button'))
    .filter(candidate => candidate.textContent === text)
    .at(-1)
  expect(found).toBeDefined()
  return found as HTMLButtonElement
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

async function renderRuntimeTabDom(client: QueryClient) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  client.setQueryData(['remote-control-interfaces'], { interfaces: [] })
  client.setQueryData(['export-catalog'], { datasets: [] })
  vi.spyOn(api, 'getUpdateStatus').mockResolvedValue(
    (client.getQueryData(['update-status']) as any) || { ok: false },
  )
  if (!vi.isMockFunction(api.getUpdateProgress)) {
    vi.spyOn(api, 'getUpdateProgress').mockImplementation(async () => (
      (client.getQueryData(['update-progress']) as any) || { mode: 'packaged', phase: 'idle' }
    ))
  }
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await act(async () => {
    root?.render(
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <RuntimeSystemTab
            status={{ version: '0.5.0', pid: 123, boot_commit: 'abc', current_commit: 'abc' }}
            settings={{ app: {}, runtime: {}, remote_control: {}, historical_data: {}, retention: {}, update: {}, network: {} }}
            qc={client}
          />
        </ConfirmProvider>
      </QueryClientProvider>,
    )
  })
  await flush()
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  container?.remove()
  root = undefined
  vi.restoreAllMocks()
})

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

  it('renders accepted packaged update as started and seeds progress', async () => {
    const client = testClient()
    client.setQueryData(['update-status'], {
      ok: true,
      mode: 'packaged',
      has_update: true,
      behind_count: 1,
      branch: 'stable',
      current_commit: '11111111',
      remote_commit: '22222222',
    })
    vi.spyOn(api, 'applyUpdate').mockResolvedValue({
      accepted: true,
      applied: false,
      reason: 'update_started_in_background',
      operation_id: 'op-123',
      message: 'Update download and validation started in the background.',
      state: { mode: 'packaged', operation_id: 'op-123', phase: 'checking', message: 'Checking signed release' },
    })
    vi.spyOn(api, 'getUpdateProgress').mockResolvedValue({
      mode: 'packaged',
      operation_id: 'op-123',
      phase: 'checking',
      message: 'Checking signed release',
    })
    await renderRuntimeTabDom(client)

    await act(async () => { button('Apply update').click() })
    await act(async () => { lastButton('Apply update').click() })
    await flush()

    expect(container.textContent).toContain('Update download and validation started in the background.')
    expect(container.textContent).toContain('op-123')
    expect(container.textContent).toContain('checking')
    expect(container.textContent).not.toContain('Not applied')
    expect(client.getQueryData(['update-progress'])).toEqual(
      expect.objectContaining({ operation_id: 'op-123', phase: 'checking' }),
    )
  })

  it('keeps packaged rejection distinct from active progress', async () => {
    const client = testClient()
    client.setQueryData(['update-status'], {
      ok: true,
      mode: 'packaged',
      has_update: true,
      behind_count: 1,
      branch: 'stable',
      current_commit: '11111111',
      remote_commit: '22222222',
    })
    client.setQueryData(['update-progress'], {
      mode: 'packaged',
      operation_id: 'op-active',
      phase: 'downloading',
      message: 'Downloading update',
    })
    vi.spyOn(api, 'applyUpdate').mockResolvedValue({
      accepted: false,
      applied: false,
      reason: 'update_already_in_progress',
    })
    await renderRuntimeTabDom(client)

    await act(async () => { button('Apply update').click() })
    await act(async () => { lastButton('Apply update').click() })
    await flush()

    expect(container.textContent).toContain('Not applied: update_already_in_progress')
    expect(container.textContent).toContain('op-active')
    expect(container.textContent).toContain('downloading')
  })
})
