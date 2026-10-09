import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

// #1675: while a permission-mode change is in flight the select must not accept
// a second confirmed change — two overlapping POSTs can land out of order and
// persist the earlier intent.
vi.mock('@tanstack/react-query', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-query')>('@tanstack/react-query')
  return { ...actual, useMutation: () => ({ isPending: true, mutate: vi.fn() }) }
})

import { AgentModelsTab } from './AgentModelsTab'

describe('AgentModelsTab permission mode', () => {
  it('locks the permission select while its save is pending', () => {
    const client = new QueryClient()
    client.setQueryData(['model'], { providers: {} })
    const html = renderToStaticMarkup(
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
    const select = html.match(/<select[^>]*>/)?.[0] ?? ''
    expect(select).toContain('disabled')
    expect(select).toContain('aria-busy="true"')
  })
})
