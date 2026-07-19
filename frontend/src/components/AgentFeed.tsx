import { useState, useEffect, useLayoutEffect, useReducer, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { AgentRun, Opportunity, TradeProposal } from '../types'
import { DetailDrawer, DrawerSection, DrawerField } from './DetailDrawer'
import ThesisText from './ThesisText'
import { ProposalRationaleDetail } from './ProposalRationaleDetail'
import { fmtSymbol } from '../lib/format'

/**
 * Agent activity feed (OVERVIEW_REDESIGN.md Decision 2).
 *
 * Three switchable modes, preference persisted to localStorage:
 *   - narrative: what the AI concluded (opportunities w/ thesis, proposals w/ rationale,
 *     dreaming outputs). OpenAlice agent-native lens.
 *   - ops: agent run metadata (who ran, status, trigger, duration). Operational lens.
 *   - both: run metadata + any narrative on one row.
 *
 * Honesty: the initial version interleaves two tracks by TIMESTAMP. It does NOT claim a
 * strict run->output causal binding (the backend has no reverse index yet — see
 * docs/OVERVIEW_REDESIGN.md backend TODO). We never fabricate causality.
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
  run?: AgentRun
  opportunity?: Opportunity
  proposal?: TradeProposal
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

function durationOf(run: AgentRun): string {
  if (!run.started_at || !run.ended_at) return ''
  const ms = safeDate(run.ended_at) - safeDate(run.started_at)
  if (Number.isNaN(ms) || ms < 0) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function outputsCount(run: AgentRun): number {
  try { return JSON.parse(run.outputs_created || '[]').length } catch { return 0 }
}

export default function AgentFeed() {
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

  // Overview owns the page scroll, but this card owns its activity-list scroll. Its
  // rendered top is dynamic because the setup banner and PnL data above it load
  // asynchronously, so derive the card height from the actual DOM position instead of
  // a hard-coded header offset. Browser zoom also emits a viewport resize and is
  // handled by the same measurement.
  useLayoutEffect(() => {
    const card = cardRef.current
    if (!card) return

    let animationFrame = 0
    const measure = () => {
      const top = card.getBoundingClientRect().top
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight
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

    // Data and setup state above the card can change its top without resizing the
    // browser. Observe those layout drivers and watch for the setup banner mounting.
    const resizeObserver = new ResizeObserver(scheduleMeasure)
    const observeLayoutDrivers = () => {
      const main = card.closest('main')
      if (!main) return
      // Observe main itself so any structural height change (setup-banner
      // mounting, PnL zone loading) triggers a re-measure without needing
      // a MutationObserver that fires on every text-content update.
      resizeObserver.observe(main)
      main.querySelectorAll('.page-title, .setup-banner, .pnl-zone')
        .forEach(element => resizeObserver.observe(element))
    }
    // MutationObserver is only used to pick up newly-mounted layout drivers
    // (e.g. setup-banner appearing after an async check). It must NOT call
    // scheduleMeasure() — that would re-measure on every feed text update
    // (agent-runs, timestamps) and cause the height to jitter every 10 s.
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

  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns, refetchInterval: 10000 })
  const { data: opportunities = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities, staleTime: 30000 })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals, staleTime: 30000 })

  const items: FeedItem[] = []

  // ----- ops/both track: agent runs -----
  if (mode === 'ops' || mode === 'both') {
    for (const r of (runs as AgentRun[])) {
      items.push({
        key: `run-${r.id}`,
        ts: r.started_at,
        kind: 'run',
        agent: r.agent_name,
        icon: agentIcon(r.agent_name),
        status: r.status,
        headline: `${r.agent_name} ${r.status === 'completed' ? 'completed' : r.status}`,
        run: r,
      })
    }
  }

  // ----- narrative/both track: outputs (opportunities + proposals) -----
  if (mode === 'narrative' || mode === 'both') {
    for (const o of (opportunities as Opportunity[])) {
      const conf = o.confidence_score != null ? ` (${o.confidence_score}%)` : o.confidence != null ? ` (${o.confidence})` : ''
      items.push({
        key: `opp-${o.id}`,
        ts: o.created_at,
        kind: 'opportunity',
        agent: o.source_agent || 'agent',
        icon: agentIcon(o.source_agent || ''),
        headline: `${o.source_agent || 'Agent'} found ${o.direction_bias} opportunity on ${fmtSymbol(o.symbol, o.market)}${conf}`,
        detail: o.thesis || undefined,
        opportunity: o,
      })
    }
    for (const p of (proposals as TradeProposal[])) {
      if (p.status === 'pending') continue
      items.push({
        key: `prop-${p.id}`,
        ts: p.created_at,
        kind: 'proposal',
        agent: 'strategy',
        icon: '📐',
        status: p.status,
        headline: `Proposal: ${p.direction} ${fmtSymbol(p.symbol, p.market_type)} (${p.execution_mode})`,
        detail: p.agent_rationale || undefined,
        proposal: p,
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
            <DrawerField label="Symbol" value={fmtSymbol(item.opportunity.symbol, item.opportunity.market)} mono />
            <DrawerField label="Bias" value={item.opportunity.direction_bias} />
            <DrawerField label="Confidence" value={item.opportunity.confidence_score != null ? `${item.opportunity.confidence_score}%` : item.opportunity.confidence ?? null} mono />
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
