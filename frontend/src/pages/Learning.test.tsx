import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const queryState = vi.hoisted(() => ({
  mode: 'error' as 'error' | 'loading' | 'stale' | 'partial' | 'success',
  semanticFailure: false,
  markdownData: { content: 'Retained markdown' } as { content?: string; error?: string },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    const key = queryKey[0]
    const staleData = key === 'knowledge' && queryKey[1] === 'all'
      ? { items: [
          { id: 'case-1', title: 'Existing case study', item_type: 'trade_case_study', status: 'active', created_at: '2026-08-01T00:00:00Z' },
          { id: 'action-1', title: 'Existing system action', item_type: 'system_action', status: 'active', created_at: '2026-08-02T00:00:00Z' },
        ], total: 2, limit: 100, offset: 0, returned: 2, has_more: false }
      : key === 'learning-contribution'
        ? queryState.semanticFailure
          ? { availability: 'unavailable', data_freshness: { last_evaluated_at: null, age_sec: null, stale_after_sec: 7200, state: 'unknown' }, items: [] }
          : { availability: 'available', data_freshness: { last_evaluated_at: null, age_sec: null, stale_after_sec: 7200, state: 'unknown' }, items: [] }
      : key === 'dreaming-summary'
        ? {
            recent_knowledge_total: 37,
            recent_knowledge_returned: 1,
            recent_knowledge_truncated: true,
            recent_knowledge_items: [{ id: 'guard-1', title: 'Produced guard', item_type: 'guard_candidate', status: 'active', created_at: '2026-08-02T00:00:00Z' }],
            recent_research_notes: [],
            recent_strategy_hypotheses: [],
            recent_dreaming_logs: [],
            recent_daily_reviews: [],
            recent_dreaming_runs: [],
            deprecated_memories: [],
            deprecated_memories_total: 0,
            deprecated_memories_truncated: false,
            dreaming_job: { status: 'healthy', last_started_at: '2026-08-12T00:00:00Z', last_succeeded_at: '2026-08-12T00:00:01Z', backlog_count: 0 },
            dreaming_job_availability: queryState.semanticFailure ? 'unavailable' : 'available',
          }
      : key === 'learning-funnel'
      ? {
          knowledge_items_by_type: [],
          pi_actual_visibility: { layer3_lesson_hits: 0, layer3_guards_available: 0, note: '' },
          ps_lessons: { availability: 'available', total: 0, by_status: [], latest_created_at: null },
          serenity_opportunities_active: 0,
          contradictions: { checked: true, count: 0, items: [], note: '' },
          active_guards_total: 0,
          query_errors: { pi_guards_window: 'read_failed' },
        }
      : key === 'filter-controller'
        ? { persistent_recommendations: [], persistent_config: { min_consecutive_runs: 3, max_runs_lookback: 10 }, runs_available: 0, last_run_at: null, schedule: { enabled: false, observe_interval_hours: 6 }, empty_reason: 'controller_disabled' }
        : key === 'knowledge-content'
          ? queryState.markdownData
        : [{ id: `${key}-1`, title: 'Retained result', created_at: '2026-07-31T00:00:00Z' }]
    return {
      data: ['stale', 'partial', 'success'].includes(queryState.mode) ? staleData : undefined,
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
  LearningOverview,
  MarkdownContent,
  ReportsTab,
} from './Learning'

describe('Learning workbench query honesty', () => {
  beforeEach(() => {
    queryState.mode = 'error'
    queryState.semanticFailure = false
    queryState.markdownData = { content: 'Retained markdown' }
  })

  it('renders failed reads as unavailable rather than benign empty or zero states', () => {
    const html = renderToStaticMarkup(<>
      <LearningOverview onNavigate={() => {}} />
      <DreamingTab />
      <KnowledgeTab />
      <ReportsTab />
      <LearningFunnelPanel />
      <MarkdownContent itemId="knowledge-1" />
    </>)

    expect(html).toContain('Unavailable')
    expect(html).toContain('Knowledge content unavailable')
    expect(html).not.toContain('No dreaming runs')
    expect(html).not.toContain('No research notes')
    expect(html).not.toContain('No daily reviews')
    expect(html).not.toContain('No content available')
    expect(html).not.toContain('No open items')
    expect(html).toContain('some counts unavailable')
    expect(html).not.toContain('Contradictions needing review: <strong>0</strong>')
  })

  it('renders initial reads as loading rather than empty collections', () => {
    queryState.mode = 'loading'

    const html = renderToStaticMarkup(<>
      <LearningOverview onNavigate={() => {}} />
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
      <LearningOverview onNavigate={() => {}} />
      <DreamingTab />
      <KnowledgeTab />
      <ReportsTab />
      <MarkdownContent itemId="knowledge-1" />
    </>)

    expect(html).toContain('Retained result')
    expect(html).toContain('Retained markdown')
    expect(html).toContain('37 · Stale')
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

  it('honors semantic unavailability inside successful response envelopes', () => {
    queryState.mode = 'success'
    queryState.semanticFailure = true

    const html = renderToStaticMarkup(<LearningOverview onNavigate={() => {}} />)

    expect(html).toContain('Impact verdict')
    expect(html).toContain('Learning job')
    expect(html.match(/Unavailable/g)?.length).toBeGreaterThanOrEqual(2)
    expect(html).not.toContain('Not measured')
    expect(html).not.toContain('Healthy')
  })

  it('defaults to an all-knowledge view so populated types are not hidden by empty curated tabs', () => {
    queryState.mode = 'success'

    const html = renderToStaticMarkup(<KnowledgeTab />)

    expect(html).toContain('All Knowledge')
    expect(html).toContain('Existing case study')
    expect(html).toContain('Trade Case Study')
    expect(html).toContain('Existing system action')
    expect(html).toContain('System Action')
  })

  it('counts every recent knowledge type and separates scheduler health from agent sessions', () => {
    queryState.mode = 'success'

    const html = renderToStaticMarkup(<><LearningOverview onNavigate={() => {}} /><DreamingTab /></>)

    expect(html).toContain('New knowledge')
    expect(html).toContain('>37<')
    expect(html).toContain('Scheduled consolidation')
    expect(html).toContain('no Dreaming Agent session is recorded')
    expect(html).toContain('Produced guard')
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
