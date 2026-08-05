import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AgentModelsTab } from './AgentModelsTab'
import SRC from './AgentModelsTab.tsx?raw'

function renderAgentTab() {
  const client = new QueryClient()
  client.setQueryData(['model'], { providers: {} })
  return renderToStaticMarkup(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AgentModelsTab
          status={{ permission_mode: 'Observe' }}
          settings={{ agent: {}, runtime: {}, learning_path_options: [], economics: {} }}
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
    expect(SRC).toContain('<ModelProvidersSection qc={qc} />')
    expect(html).toContain('Inference Admission')
    expect(html).toContain('Enable economics harness')
  })
})
