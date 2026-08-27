import { describe, expect, it } from 'vitest'
import APP from '../App.tsx?raw'
import AGENT_ACTIVITY from './AgentActivity.tsx?raw'
import LEARNING from './Learning.tsx?raw'
import TRADES from './TradesPositions.tsx?raw'

describe('legacy route and tab URL contracts', () => {
  it('redirects merged legacy routes to their canonical tab queries', () => {
    expect(APP).toContain('<LegacyRedirect to="/agent-activity" tab="runs" />')
    expect(APP).toContain('<LegacyRedirect to="/agent-activity" tab="opps" />')
    expect(APP).toContain('<LegacyRedirect to="/learning" tab="dreaming" />')
    expect(APP).toContain('<LegacyRedirect to="/learning" tab="knowledge" />')
    expect(APP).toContain('<LegacyRedirect to="/learning" tab="operations" />')
    expect(APP).toContain('<LegacyRedirect to="/trades" tab="blotter" />')
  })

  it('keeps merged workbench tabs controlled by the tab query', () => {
    for (const src of [AGENT_ACTIVITY, LEARNING, TRADES]) {
      expect(src).toContain('useSearchParams')
      expect(src).toContain('setSearchParams(next')
      expect(src).toContain('value={')
      expect(src).toContain('onChange={')
    }
    expect(AGENT_ACTIVITY).toContain("searchParams.get('tab')")
    expect(LEARNING).toContain("searchParams.get('tab')")
    expect(TRADES).toContain('searchParams.get("tab")')
    expect(AGENT_ACTIVITY).toContain("const AGENT_ACTIVITY_TABS = ['runs', 'opps'] as const")
    expect(LEARNING).toContain("const LEARNING_TABS = ['overview', 'contribution', 'dreaming', 'knowledge', 'operations'] as const")
    expect(TRADES).toContain('const TRADES_TABS = ["blotter", "analytics"] as const')
  })
})
