import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const queryState = vi.hoisted(() => ({
  mode: 'error' as 'error' | 'loading' | 'stale' | 'partial',
  markdownData: { content: 'Retained markdown' } as { content?: string; error?: string },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    const key = queryKey[0]
    const staleData = key === 'learning-funnel'
      ? {
          knowledge_items_by_type: [],
          pi_actual_visibility: { layer3_lesson_hits: 0, layer3_guards_available: 0, note: '' },
          ps_lessons: { availability: 'available', total: 0, by_status: [], latest_created_at: null },
          serenity_opportunities_active: 0,
          contradictions: { checked: true, count: 0, items: [], note: '' },
          query_errors: { pi_guards_window: 'read_failed' },
        }
      : key === 'filter-controller'
        ? { persistent_recommendations: [], persistent_config: { min_consecutive_runs: 3, max_runs_lookback: 10 } }
        : key === 'knowledge-content'
          ? queryState.markdownData
        : [{ id: `${key}-1`, title: 'Retained result', created_at: '2026-07-31T00:00:00Z' }]
    return {
      data: queryState.mode === 'stale' || queryState.mode === 'partial' ? staleData : undefined,
      isLoading: queryState.mode === 'loading',
      isError: queryState.mode === 'error' || queryState.mode === 'stale',
    }
  },
  useMutation: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock('../charts/PnLChart', () => ({
  default: () => <div>PnL chart state</div>,
}))

import {
  DreamingTab,
  KnowledgeTab,
  LearningFunnelPanel,
  LearningPulse,
  MarkdownContent,
  ReportsTab,
} from './Learning'

describe('Learning workbench query honesty', () => {
  beforeEach(() => {
    queryState.mode = 'error'
    queryState.markdownData = { content: 'Retained markdown' }
  })

  it('renders failed reads as unavailable rather than benign empty or zero states', () => {
    const html = renderToStaticMarkup(<>
      <LearningPulse />
      <DreamingTab />
      <KnowledgeTab />
      <ReportsTab />
      <LearningFunnelPanel />
      <MarkdownContent itemId="knowledge-1" />
    </>)

    expect(html).toContain('Unavailable')
    expect(html).toContain('Portfolio context · not Learning attribution')
    expect(html).toContain('Knowledge content unavailable')
    expect(html).not.toContain('No dreaming runs')
    expect(html).not.toContain('No research notes')
    expect(html).not.toContain('No daily reviews')
    expect(html).not.toContain('No content available')
    expect(html).not.toContain('Contradictions needing review: <strong>0</strong>')
  })

  it('renders initial reads as loading rather than empty collections', () => {
    queryState.mode = 'loading'

    const html = renderToStaticMarkup(<>
      <LearningPulse />
      <DreamingTab />
      <KnowledgeTab />
      <ReportsTab />
      <LearningFunnelPanel />
    </>)

    expect(html).toContain('Loading')
    expect(html).not.toContain('No dreaming runs')
    expect(html).not.toContain('No lessons yet')
    expect(html).not.toContain('No daily reviews')
  })

  it('keeps retained data visible and labels stale or partial sections', () => {
    queryState.mode = 'stale'

    const html = renderToStaticMarkup(<>
      <LearningPulse />
      <DreamingTab />
      <KnowledgeTab />
      <ReportsTab />
      <MarkdownContent itemId="knowledge-1" />
    </>)

    expect(html).toContain('Retained result')
    expect(html).toContain('Retained markdown')
    expect(html).toContain('0 · Stale')
    expect(html).toContain('is stale')
    expect(html).toContain('Knowledge funnel is stale')
    expect(html).toContain('Unavailable sections: Pi Guards Window')
    expect(html).toContain('Lessons applied: <strong>0</strong>')
    expect(html).not.toContain('Lessons applied: <strong>Unavailable</strong>')
  })

  it('renders successful responses with failed optional sections as partial', () => {
    queryState.mode = 'partial'

    const html = renderToStaticMarkup(<LearningFunnelPanel />)

    expect(html).toContain('Knowledge funnel is partial')
    expect(html).toContain('Unavailable sections: Pi Guards Window')
  })

  it('labels retained empty and missing markdown states as stale', () => {
    queryState.mode = 'stale'
    queryState.markdownData = { content: '' }
    const emptyHtml = renderToStaticMarkup(<MarkdownContent itemId="empty" />)

    queryState.markdownData = { error: 'file_not_found' }
    const missingHtml = renderToStaticMarkup(<MarkdownContent itemId="missing" />)

    expect(emptyHtml).toContain('Knowledge content is stale')
    expect(emptyHtml).toContain('Empty file')
    expect(missingHtml).toContain('Knowledge content is stale')
    expect(missingHtml).toContain('File not found on disk')
  })
})
