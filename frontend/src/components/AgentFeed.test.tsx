import { describe, expect, it } from 'vitest'
import SRC from './AgentFeed.tsx?raw'

describe('AgentFeed exact navigation identity', () => {
  it('preserves backend IDs in drawer payloads and exact destination links', () => {
    expect(SRC).toContain('type CompactAgentRun = {')
    expect(SRC).toContain('id: string | null')
    expect(SRC).toContain('id: r.id || null')
    expect(SRC).toContain('id: o.id || null')
    expect(SRC).toContain('id: p.id || null')
    expect(SRC).toContain('/agent-activity?tab=runs&run_id=')
    expect(SRC).toContain('/agent-activity?tab=opps&opportunity_id=')
    expect(SRC).toContain('/blotter?proposal_id=')
    expect(SRC).not.toContain("navigate('/agent-activity')")
    expect(SRC).not.toContain("navigate('/blotter')")
  })
})
