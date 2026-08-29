import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AgentModelsTab } from './AgentModelsTab'
import SRC from './AgentModelsTab.tsx?raw'

function renderAgentTab(settings: Record<string, unknown> = {}) {
  const client = new QueryClient()
  client.setQueryData(['model'], { providers: {} })
  return renderToStaticMarkup(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AgentModelsTab
          status={{ permission_mode: 'Observe' }}
          settings={{ agent: {}, runtime: {}, learning_path_options: [], economics: {}, ...settings }}
          qc={client}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('AgentModelsTab', () => {
  it('preserves section order and deep-link anchors', () => {
    const html = renderAgentTab()
    const ids = ['permission-mode', 'learning', 'regime-detection', 'serenity-research', 'scheduler', 'ai-model-providers', 'inference-admission', 'llm-cost-usage']
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
  })

  it('keeps autonomy, learning, provider, inference, and economics controls composed', () => {
    const html = renderAgentTab()

    expect(html).toContain('Permission mode')
    expect(SRC).toContain('Paper Learning Experiments')
    expect(html).toContain('Regime detection enabled')
    expect(html).toContain('Serenity Lens enabled')
    expect(html).toContain('Scheduler enabled')
    expect(SRC).toContain('<ModelProvidersSection qc={qc} onDirty={onDirty} />')
    expect(html).toContain('Inference Admission')
    expect(html).toContain('Enable economics harness')
  })

  it('shows configured daemon state separately from a stopped runtime', () => {
    const html = renderAgentTab({
      pi_daemon: { enabled: true, debounce_sec: 2, cooldown_sec: 45, heartbeat_sec: 60 },
      pi_daemon_runtime: {
        running: false,
        task_generation: 7,
        active_params: null,
      },
    })

    expect(html).toContain('Configured ON; runtime stopped (generation 7).')
    expect(html).toContain('Configured values are not active.')
    expect(html).toContain('data-runtime-state="stopped"')
  })

  it('shows the daemon parameters that are actually active', () => {
    const html = renderAgentTab({
      pi_daemon: { enabled: true, debounce_sec: 4, cooldown_sec: 90, heartbeat_sec: 120 },
      pi_daemon_runtime: {
        running: true,
        task_generation: 8,
        active_params: { debounce_sec: 4, cooldown_sec: 90, heartbeat_sec: 120 },
      },
    })

    expect(html).toContain('Configured ON; runtime running (generation 8).')
    expect(html).toContain('Active: debounce 4s, cooldown 90s, heartbeat 120s.')
    expect(html).not.toContain('Configured values are not active.')
  })
})
