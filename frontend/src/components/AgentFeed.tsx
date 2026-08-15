import { useState, useEffect, useLayoutEffect, useReducer, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { OverviewBootstrap } from '../api/boot'
import { DetailDrawer, DrawerSection, DrawerField } from './DetailDrawer'
import ThesisText from './ThesisText'
import { ProposalRationaleDetail } from './ProposalRationaleDetail'
import { fmtOpportunityScore, fmtScore100, fmtSymbol, opportunityScoreLabel } from '../lib/format'

/**
 * Agent activity feed (docs/design/frontend/overview_redesign.md Decision 2).
 *
 * Three switchable modes, preference persisted to localStorage:
 *   - narrative: what the AI concluded (opportunities w/ thesis, proposals w/ rationale,
 *     dreaming outputs). OpenAlice agent-native lens.
 *   - ops: agent run metadata (who ran, status, trigger, duration). Operational lens.
 *   - both: run metadata + any narrative on one row.
 *
 * Honesty: the initial version interleaves two tracks by TIMESTAMP. It does NOT claim a
 * strict run->output causal binding (the backend has no reverse index yet — see
 * docs/design/frontend/overview_redesign.md backend TODO). We never fabricate causality.
 */

type FeedMode = 'narrative' | 'ops' | 'both'
const MODE_KEY = 'aletheia.feedMode'
const MODES: FeedMode[] = ['narrative', 'ops', 'both']
const MODE_LABEL: Record<FeedMode, string> = { narrative: 'Narrative', ops: 'Ops', both: 'Both' }
const VIEWPORT_BOTTOM_GAP = 16
const MIN_FEED_HEIGHT = 240

// #120 fix: the run/proposal status vocabulary carries several failure
// values that used to slip through as accent/yellow — collect them so the
// feed row and its badge agree on what counts as a failure to highlight.
const FAILURE_STATUSES = new Set<string>([
  'error', 'failed', 'timed_out', 'stale', 'completed_with_errors', 'rejected',
])

type CompactAgentRun = {
  agent_name: string | null
  trigger_type: string | null
  status: string | null
  started_at: string | null
  ended_at: string | null
  model_provider: string | null
  outputs_created: string | null
  errors: string | null
}

type CompactOpportunity = {
  symbol: string | null
  market: string | null
  direction_bias: string | null
  thesis: string | null
  excerpt: string | null
  confidence: string | null
  confidence_score: number | null
  source_agent: string | null
}

type CompactTradeProposal = {
  symbol: string | null
  market_type: string | null
  direction: string | null
  execution_mode: string | null
  agent_rationale: string | null
  status: string | null
}

type FeedItem = {
  key: string
  ts: string
  kind: 'run' | 'opportunity' | 'proposal'
  agent: string
  icon: string
  status?: string
  // narrative
  headline?: string
  detail?: string
  // raw payloads for the drawer
  run?: CompactAgentRun
  opportunity?: CompactOpportunity
  proposal?: CompactTradeProposal
}

const AGENT_ICON: Record<string, string> = {
  market_observer: '🔍',
  research_agent: '📑',
  strategy_designer: '📐',
  risk_critic: '🛡',
  portfolio_agent: '📊',
  post_trade_reviewer: '🔧',
  dreaming_agent: '🌙',
}

function agentIcon(name: string): string {
  return AGENT_ICON[name] || '◈'
}

function relTime(ts?: string | null): string {
  if (!ts) return ''
  const t = new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z').getTime()
  if (Number.isNaN(t)) return ts.slice(5, 16)
  const mins = Math.floor((Date.now() - t) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function safeDate(ts: string): number {
  return new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z').getTime()
}

function durationOf(run: Pick<CompactAgentRun, 'started_at' | 'ended_at'>): string {
  if (!run.started_at || !run.ended_at) return ''
  const ms = safeDate(run.ended_at) - safeDate(run.started_at)
  if (Number.isNaN(ms) || ms < 0) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function outputsCount(run: Pick<CompactAgentRun, 'outputs_created'>): number {
  try { return JSON.parse(run.outputs_created || '[]').length } catch { return 0 }
}

export default function AgentFeed({ bootstrap }: { bootstrap?: OverviewBootstrap }) {
  const navigate = useNavigate()
  const cardRef = useRef<HTMLDivElement>(null)
  const [availableHeight, setAvailableHeight] = useState<number | null>(null)
  const [mode, setMode] = useState<FeedMode>(() => {
    try {
      const v = localStorage.getItem(MODE_KEY) as FeedMode | null
      return v && MODES.includes(v) ? v : 'both'
    } catch { return 'both' }
  })
  const [selected, setSelected] = useState<FeedItem | null>(null)

  function pickMode(m: FeedMode) {
    setMode(m)
    try { localStorage.setItem(MODE_KEY, m) } catch { /* ignore */ }
  }

  // Re-render every 30s to update relative timestamps
  const [, forceUpdate] = useReducer(x => x + 1, 0)
  useEffect(() => { const t = setInterval(forceUpdate, 30000); return () => clearInterval(t) }, [])

  // Height is set once from the card's document position and changes only with
  // viewport size or initial layout drivers above the card—not page scrolling or
  // activity updates inside the card.
  useLayoutEffect(() => {
    const card = cardRef.current
    if (!card) return

    let animationFrame = 0
    const measure = () => {
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight
      const top = card.getBoundingClientRect().top + window.scrollY
      const nextHeight = Math.max(
        MIN_FEED_HEIGHT,
        Math.floor(viewportHeight - Math.max(0, top) - VIEWPORT_BOTTOM_GAP),
      )
      setAvailableHeight(current => current === nextHeight ? current : nextHeight)
    }
    const scheduleMeasure = () => {
      window.cancelAnimationFrame(animationFrame)
      animationFrame = window.requestAnimationFrame(measure)
    }

    const resizeObserver = new ResizeObserver(scheduleMeasure)
    const observeLayoutDrivers = () => {
      const main = card.closest('main')
      if (!main) return
      main.querySelectorAll('.page-title, .setup-banner, .pnl-zone')
        .forEach(element => resizeObserver.observe(element))
    }
    const mutationRoot = card.closest('main')
    const mutationObserver = new MutationObserver(observeLayoutDrivers)

    observeLayoutDrivers()
    if (mutationRoot) {
      mutationObserver.observe(mutationRoot, { childList: true, subtree: false })
    }
    window.addEventListener('resize', scheduleMeasure)
    window.visualViewport?.addEventListener('resize', scheduleMeasure)
    measure()

    return () => {
      window.cancelAnimationFrame(animationFrame)
      window.removeEventListener('resize', scheduleMeasure)
      window.visualViewport?.removeEventListener('resize', scheduleMeasure)
      resizeObserver.disconnect()
      mutationObserver.disconnect()
    }
  }, [])

  const items: FeedItem[] = []
  const activity = (bootstrap?.activity || []) as Array<Record<string, any>>

  // ----- ops/both track: agent runs -----
  if (mode === 'ops' || mode === 'both') {
    for (const r of activity.filter(item => item.kind === 'run') as any[]) {
      items.push({
        key: `run-${r.id}`,
        ts: String(r.timestamp || ''),
        kind: 'run',
        agent: String(r.agent || 'agent'),
        icon: agentIcon(String(r.agent || '')),
        status: r.status,
        headline: r.headline || `${r.agent || 'agent'} ${r.status || ''}`,
        run: {
          agent_name: r.agent_name || r.agent || null,
          trigger_type: r.trigger_type || null,
          status: r.status || null,
          started_at: r.started_at || r.timestamp || null,
          ended_at: r.ended_at || null,
          model_provider: r.model_provider || null,
          outputs_created: r.outputs_created || null,
          errors: r.errors || null,
        },
      })
    }
  }

  // ----- narrative/both track: outputs (opportunities + proposals) -----
  if (mode === 'narrative' || mode === 'both') {
    for (const o of activity.filter(item => item.kind === 'opportunity') as any[]) {
      const score = o.confidence_score != null
        ? ` (${fmtOpportunityScore(o.agent, o.confidence_score, null)})`
        : ''
      items.push({
        key: `opp-${o.id}`,
        ts: String(o.timestamp || ''),
        kind: 'opportunity',
        agent: o.agent || 'agent',
        icon: agentIcon(o.agent || ''),
        headline: o.headline || `${o.agent || 'Agent'} found ${o.direction_bias || 'unknown'} opportunity on ${fmtSymbol(o.symbol, o.market)}${score}`,
        detail: o.excerpt || undefined,
        opportunity: {
          symbol: o.symbol || null,
          market: o.market || null,
          direction_bias: o.direction_bias || null,
          thesis: o.thesis || o.excerpt || null,
          excerpt: o.excerpt || null,
          confidence: o.confidence || null,
          confidence_score: typeof o.confidence_score === 'number' ? o.confidence_score : null,
          source_agent: o.source_agent || o.agent || null,
        },
      })
    }
    for (const p of activity.filter(item => item.kind === 'proposal') as any[]) {
      if (p.status === 'pending') continue
      items.push({
        key: `prop-${p.id}`,
        ts: String(p.timestamp || ''),
        kind: 'proposal',
        agent: 'strategy',
        icon: '📐',
        status: p.status,
        headline: p.headline || `Proposal: ${p.direction || 'unknown'} ${fmtSymbol(p.symbol, p.market_type)} (${p.execution_mode || 'unknown'})`,
        detail: p.excerpt || undefined,
        proposal: {
          symbol: p.symbol || null,
          market_type: p.market_type || null,
          direction: p.direction || null,
          execution_mode: p.execution_mode || null,
          agent_rationale: p.agent_rationale || p.excerpt || null,
          status: p.status || null,
        },
      })
    }
  }

  items.sort((a, b) => safeDate(b.ts) - safeDate(a.ts))
  const visible = items.slice(0, 40)

  return (
    <div
      ref={cardRef}
      className="card agent-feed agent-feed-viewport"
      style={availableHeight == null ? undefined : { height: `${availableHeight}px` }}
    >
      <div className="flex items-center justify-between mb-3">
        <h3>Agent Activity</h3>
        <div className="seg-toggle">
          {MODES.map(m => (
            <button
              key={m}
              className={`seg-btn${m === mode ? ' active' : ''}`}
              onClick={() => pickMode(m)}
            >
              {MODE_LABEL[m]}
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="text-muted text-sm">No agent activity yet.</p>
      ) : (
        <div className="feed-list feed-list-scroll">
          {visible.map(it => (
            <button key={it.key} className="feed-row" onClick={() => setSelected(it)}>
              <span className="feed-ts mono">{relTime(it.ts)}</span>
              {/* #120 fix: only status=='error' was rendered red; failed /
                  timed_out / stale / completed_with_errors used to slip
                  through as accent-blue (looks fine) despite being failures. */}
              <span className="feed-icon" style={{ color: FAILURE_STATUSES.has(String(it.status)) ? 'var(--red)' : 'var(--accent)' }}>{it.icon}</span>
              <span className="feed-body">
                <span className="feed-headline">{it.headline}</span>
                {(mode === 'narrative' || mode === 'both') && it.detail && (
                  <span className="feed-detail">{it.detail.length > 120 ? it.detail.slice(0, 120) + '…' : it.detail}</span>
                )}
                {(mode === 'ops' || mode === 'both') && it.run && (
                  <span className="feed-meta mono">
                    {it.run.trigger_type}{it.run.model_provider ? ` · ${it.run.model_provider}` : ''}
                    {durationOf(it.run) && ` · ${durationOf(it.run)}`}
                    {` · ${outputsCount(it.run)} out`}
                    {it.run.errors && it.run.errors !== '[]' && ' · errored'}
                  </span>
                )}
              </span>
              {it.status && (
                <span className={`badge ${it.status === 'completed' || it.status === 'approved' ? 'badge-green' : FAILURE_STATUSES.has(String(it.status)) ? 'badge-red' : 'badge-yellow'}`}>
                  {it.status}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      <FeedDrawer item={selected} onClose={() => setSelected(null)} navigate={navigate} />
    </div>
  )
}

function FeedDrawer({ item, onClose, navigate }: { item: FeedItem | null; onClose: () => void; navigate: (p: string) => void }) {
  return (
    <DetailDrawer open={!!item} onClose={onClose} title={item?.headline || 'Activity'}>
      {item?.kind === 'run' && item.run && (
        <>
          <DrawerSection label="Agent Run">
            <DrawerField label="Agent" value={item.run.agent_name} />
            <DrawerField label="Status" value={item.run.status} />
            <DrawerField label="Trigger" value={item.run.trigger_type} />
            <DrawerField label="Model" value={item.run.model_provider} />
            <DrawerField label="Started" value={item.run.started_at?.slice(0, 16)} />
            <DrawerField label="Duration" value={durationOf(item.run) || null} mono />
          </DrawerSection>
          <div className="flex gap-2 mt-3">
            <button className="btn-control secondary" onClick={() => { onClose(); navigate('/agent-activity') }}>Open in Agent Activity →</button>
          </div>
        </>
      )}
      {item?.kind === 'opportunity' && item.opportunity && (
        <>
          <DrawerSection label="Opportunity">
            <DrawerField label="Symbol" value={fmtSymbol(item.opportunity.symbol || '', item.opportunity.market || '')} mono />
            <DrawerField label="Bias" value={item.opportunity.direction_bias} />
            <DrawerField
              label={opportunityScoreLabel(item.opportunity.source_agent || '')}
              value={item.opportunity.confidence_score != null
                ? fmtScore100(item.opportunity.confidence_score)
                : item.opportunity.confidence ?? null}
              mono
            />
            <DrawerField label="Source" value={item.opportunity.source_agent} />
          </DrawerSection>
          <DrawerSection label="Thesis">
            {item.opportunity.thesis
              ? <ThesisText text={item.opportunity.thesis} />
              : <p className="feed-thesis">No thesis recorded.</p>}
          </DrawerSection>
          <div className="flex gap-2 mt-3">
            <button className="btn-control secondary" onClick={() => { onClose(); navigate('/agent-activity') }}>Open in Agent Activity →</button>
          </div>
        </>
      )}
      {item?.kind === 'proposal' && item.proposal && (
        <>
          <DrawerSection label="Proposal">
            <DrawerField label="Symbol" value={fmtSymbol(item.proposal.symbol, item.proposal.market_type)} mono />
            <DrawerField label="Direction" value={item.proposal.direction} />
            <DrawerField label="Mode" value={item.proposal.execution_mode} />
            <DrawerField label="Status" value={item.proposal.status} />
          </DrawerSection>
          <DrawerSection label="Agent Rationale">
            {item.proposal.agent_rationale
              ? <ProposalRationaleDetail rationale={item.proposal.agent_rationale} />
              : <p className="feed-thesis">No rationale recorded.</p>}
          </DrawerSection>
          <div className="flex gap-2 mt-3">
            <button className="btn-control" onClick={() => { onClose(); navigate('/blotter') }}>Review in Blotter →</button>
          </div>
        </>
      )}
    </DetailDrawer>
  )
}
