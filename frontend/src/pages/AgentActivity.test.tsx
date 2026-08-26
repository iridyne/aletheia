import { describe, expect, it } from 'vitest'
import SRC from './AgentActivity.tsx?raw'

describe('AgentActivity exact-link routing', () => {
  it('uses URL identity for tab selection and exact drawer hydration', () => {
    expect(SRC).toContain('useSearchParams')
    expect(SRC).toContain("const runId = searchParams.get('run_id')")
    expect(SRC).toContain("const opportunityId = searchParams.get('opportunity_id')")
    expect(SRC).toContain("queryKey: ['agent-run', runId]")
    expect(SRC).toContain('api.getAgentRun(runId!)')
    expect(SRC).toContain("queryKey: ['opportunity', opportunityId]")
    expect(SRC).toContain('api.getOpportunity(opportunityId!)')
    expect(SRC).toContain('value={activeTab}')
    expect(SRC).toContain("setSearchParams({ tab: 'runs', run_id: id })")
    expect(SRC).toContain("setSearchParams({ tab: 'opps', opportunity_id: id })")
    expect(SRC).toContain("Agent run <span className=\"mono\">{selectedId}</span> is unavailable.")
    expect(SRC).toContain("Opportunity <span className=\"mono\">{selectedId}</span> is unavailable.")
  })
})
