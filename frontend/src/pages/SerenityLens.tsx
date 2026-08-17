import { useState, useEffect, useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import type { TabDef } from '../components/Tabs'
import { StatusBadge } from '../components/DataTable'
import { Badge, Button, Card, StatusDot, StatusText, Switch, TableWrap, type StatusTone } from '../components/ui'
import { fmtTimestamp, fmtPct, humanize, fmtSymbol } from '../lib/format'
import ThesisText from '../components/ThesisText'

type Tab = 'overview' | 'runs' | 'themes' | 'evidence' | 'profile'

// U1: verdict recommendation tone mapping (AI Berkshire forced-verdict)
const VERDICT_TONE: Record<string, StatusTone> = {
  long:  'green',
  short: 'red',
  watch: 'yellow',
  avoid: 'muted',
}

// U1: researchability grade badge tone
const GRADE_TONE: Record<string, StatusTone> = {
  A: 'green',
  B: 'yellow',
  C: 'muted',
}

function parseVerdict(raw: string | null | undefined): Record<string, unknown> {
  if (!raw) return {}
  try { return JSON.parse(raw) ?? {} } catch { return {} }
}

const ENGINE_STATUS_TONE: Record<string, StatusTone> = {
  idle: 'muted',
  disabled: 'muted',
  healthy: 'green',
  running: 'yellow',
  rate_limited: 'yellow',
  error: 'red',
}

function EngineStatusDot({ status }: { status: string }) {
  const tone = ENGINE_STATUS_TONE[status] || 'muted'
  return <StatusDot tone={tone === 'blue' ? 'muted' : tone} />
}

// Run status uses the shared canonical StatusBadge (humanized label + consistent color
// across the whole dashboard). The old local RunStatusBadge disagreed with it (running→blue
// vs the shared running→yellow) and is removed.
function RunStatusBadge({ status }: { status: string }) {
  return <StatusBadge status={status} />
}

const shortDate = (s: string | null | undefined) => fmtTimestamp(s)

// Safely read one numeric field from a JSON string column; never throws on
// malformed data (a single bad row must not crash the whole table render).
function jsonCount(raw: string | null | undefined, key: string): number {
  if (!raw) return 0
  try {
    const v = JSON.parse(raw)?.[key]
    return typeof v === 'number' ? v : 0
  } catch { return 0 }
}

function ElapsedTimer({ startedAt }: { startedAt: string }) {
  const [elapsed, setElapsed] = useState(() => Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000))
  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [startedAt])
  const mins = Math.floor(elapsed / 60)
  const secs = elapsed % 60
  return <span className="text-muted">Elapsed: {mins}m {secs}s</span>
}

// --- Research Status Card ---
function ResearchStatusCard() {
  const { data: status } = useQuery({
    queryKey: ['serenity-status'],
    queryFn: api.getSerenityStatus,
    refetchInterval: 5000,
  })

  if (!status) return <div className="lens-engine-status">Loading status…</div>

  return (
    <div className="lens-engine-status">
      <div className="lens-status-grid">
        <div className="lens-status-item">
          <span className="lens-status-label">Engine</span>
          <span className="lens-status-value">
            <EngineStatusDot status={status.engineStatus} />
            <StatusText tone={ENGINE_STATUS_TONE[status.engineStatus] || 'muted'}>{status.engineStatus}</StatusText>
          </span>
        </div>
        <div className="lens-status-item">
          <span className="lens-status-label">Active Themes</span>
          <span className="lens-status-value">{status.artifactCounts?.activeThemes ?? 0}</span>
        </div>
        <div className="lens-status-item">
          <span className="lens-status-label">Last Run</span>
          <span className="lens-status-value">
            {status.lastSuccessfulRun
              ? shortDate(status.lastSuccessfulRun.finishedAt)
              : 'Never'}
          </span>
        </div>
        {status.currentRun && (
          <div className="lens-status-item">
            <span className="lens-status-label">Current</span>
            <span className="lens-status-value">
              <RunStatusBadge status={status.currentRun.status} />
              {status.currentRun.stage && <span className="text-muted"> ({status.currentRun.stage})</span>}
            </span>
          </div>
        )}
      </div>
      {status.engineStatus === 'rate_limited' && status.lastAttemptedRun?.errorMessage && (
        <div className="lens-status-warning">
          Rate limited: {status.lastAttemptedRun.errorMessage.slice(0, 100)}
          {status.lastAttemptedRun.nextRetryAt && (
            <span className="text-muted"> · Next retry: {shortDate(status.lastAttemptedRun.nextRetryAt)}</span>
          )}
        </div>
      )}
    </div>
  )
}

// --- Overview Tab ---
function OverviewTab() {
  const qc = useQueryClient()
  const [brief, setBrief] = useState('')
  const [saveToProfile, setSaveToProfile] = useState(false)

  const createRun = useMutation({
    mutationFn: () => api.createSerenityRun({
      mode: 'standard',
      triggerType: 'manual',
      brief: brief || undefined,
      saveToProfile,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
      setBrief('')
    },
  })

  const { data: status } = useQuery({
    queryKey: ['serenity-status'],
    queryFn: api.getSerenityStatus,
    refetchInterval: 5000,
  })

  const hasActive = !!status?.currentRun

  return (
    <div>
      <Card>
        <h3>Trigger Research Run</h3>
        <div className="lens-brief-input">
          <textarea
            placeholder="Optional: describe what to focus on (e.g. 'AI semiconductor supply chain catalysts')…"
            value={brief}
            onChange={e => setBrief(e.target.value)}
            rows={3}
            maxLength={2000}
          />
          <div className="lens-brief-actions">
            <Switch
              checked={saveToProfile}
              persistence="staged"
              onCheckedChange={setSaveToProfile}
              label="Save preferences to profile"
            />
            <Button
              onClick={() => createRun.mutate()}
              disabled={createRun.isPending || hasActive}
              title={hasActive ? 'A run is already active' : 'Start a new research run'}
            >
              {createRun.isPending ? 'Starting…' : hasActive ? 'Run Active' : 'Run Research'}
            </Button>
          </div>
          {createRun.isError && (
            <div className="lens-status-warning">
              {(createRun.error as any)?.message || 'Failed to start run'}
            </div>
          )}
        </div>
        <p className="text-muted lens-disclaimer">Research only — does not execute trades or modify positions.</p>
      </Card>

      <Card>
        <h3>Recent Activity</h3>
        <RecentRunsList limit={5} />
      </Card>
    </div>
  )
}

// --- Runs Tab ---
function RunsTab() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  return (
    <div>
      {selectedRunId ? (
        <RunDetailPanel runId={selectedRunId} onBack={() => setSelectedRunId(null)} />
      ) : (
        <Card>
          <h3>Run History</h3>
          <RecentRunsList limit={20} onSelect={setSelectedRunId} />
        </Card>
      )}
    </div>
  )
}

function RecentRunsList({ limit, onSelect }: { limit: number; onSelect?: (id: string) => void }) {
  const { data } = useQuery({
    queryKey: ['serenity-runs', limit],
    queryFn: () => api.getSerenityRuns({ limit }),
    refetchInterval: 5000,
  })

  const items = data?.items || []
  if (!items.length) {
    return (
      <div className="lens-empty-state">
        <p>Research engine has not run yet.</p>
        <p className="text-muted">Start the first research cycle to generate themes, evidence, and opportunities.</p>
      </div>
    )
  }

  return (
    <TableWrap>
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Trigger</th>
            <th>Mode</th>
            <th>Started</th>
            <th>Finished</th>
            <th>Themes</th>
            <th>Claims</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r: any) => (
            <tr
              key={r.id}
              className={onSelect ? 'clickable' : ''}
              onClick={() => onSelect?.(r.id)}
            >
              <td><RunStatusBadge status={r.status} /></td>
              <td>{r.triggerType || r.trigger_type ? humanize(r.triggerType || r.trigger_type) : '—'}</td>
              <td>{r.mode ? humanize(r.mode) : '—'}</td>
              <td className="mono">{shortDate(r.startedAt || r.started_at)}</td>
              <td className="mono">{shortDate(r.finishedAt || r.finished_at)}</td>
              <td className="mono">{r.artifactCounts?.themes ?? jsonCount(r.artifact_counts, 'themes')}</td>
              <td>{r.artifactCounts?.claims ?? jsonCount(r.artifact_counts, 'claims')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  )
}

function RunDetailPanel({ runId, onBack }: { runId: string; onBack: () => void }) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['serenity-run-detail', runId],
    queryFn: () => api.getSerenityRunDetail(runId),
    refetchInterval: 3000,
  })

  const { data: artifacts } = useQuery({
    queryKey: ['serenity-run-artifacts', runId],
    queryFn: () => api.getSerenityRunArtifacts(runId),
    enabled: !!data?.run && !['queued', 'running', 'cancel_requested'].includes(data.run.status),
  })

  const retryMut = useMutation({
    mutationFn: () => api.retrySerenityRun(runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      onBack()
    },
  })

  const cancelMut = useMutation({
    mutationFn: () => api.cancelSerenityRun(runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      qc.invalidateQueries({ queryKey: ['serenity-run-detail', runId] })
    },
  })

  if (!data) return <p>Loading…</p>

  const { run, events } = data
  const canRetry = run.status === 'failed' || run.status === 'rate_limited'
  const canCancel = run.status === 'queued' || run.status === 'running'

  return (
    <Card>
      <div className="card-head card-head-space">
        <Button variant="secondary" onClick={onBack}>← Back</Button>
        <h3>Run Detail</h3>
        <div className="lens-run-actions">
          {canCancel && (
            <Button variant="secondary" onClick={() => cancelMut.mutate()} disabled={cancelMut.isPending}>
              {cancelMut.isPending ? 'Cancelling…' : 'Cancel'}
            </Button>
          )}
          {canRetry && (
            <Button onClick={() => retryMut.mutate()} disabled={retryMut.isPending}>
              {retryMut.isPending ? 'Retrying…' : 'Retry'}
            </Button>
          )}
        </div>
      </div>

      <div className="lens-status-grid">
        <div className="lens-status-item">
          <span className="lens-status-label">Status</span>
          <span className="lens-status-value"><RunStatusBadge status={run.status} /></span>
        </div>
        <div className="lens-status-item">
          <span className="lens-status-label">Trigger</span>
          <span className="lens-status-value">{humanize(run.triggerType || run.trigger_type)}</span>
        </div>
        <div className="lens-status-item">
          <span className="lens-status-label">Mode</span>
          <span className="lens-status-value">{humanize(run.mode)}</span>
        </div>
        <div className="lens-status-item">
          <span className="lens-status-label">Started</span>
          <span className="lens-status-value">{shortDate(run.startedAt || run.started_at)}</span>
        </div>
        {(run.finishedAt || run.finished_at) && (
          <div className="lens-status-item">
            <span className="lens-status-label">Finished</span>
            <span className="lens-status-value">{shortDate(run.finishedAt || run.finished_at)}</span>
          </div>
        )}
        {['running', 'cancel_requested'].includes(run.status) && (run.startedAt || run.started_at) && (
          <div className="lens-status-item">
            <span className="lens-status-label">Progress</span>
            <span className="lens-status-value">
              <ElapsedTimer startedAt={run.startedAt || run.started_at} />
              {run.stage && <span> · Stage: {run.stage}</span>}
            </span>
          </div>
        )}
      </div>

      {(run.errorMessage || run.error_message) && (
        <div className="lens-status-warning">{run.errorMessage || run.error_message}</div>
      )}
      {(run.briefSnapshot || run.brief_snapshot) && (
        <p><b>Brief:</b> {run.briefSnapshot || run.brief_snapshot}</p>
      )}

      {artifacts && (
        <div className="lens-artifacts-summary">
          <h4>Produced Artifacts</h4>
          <div className="lens-status-grid">
            <div className="lens-status-item">
              <span className="lens-status-label">Themes</span>
              <span className="lens-status-value">{artifacts.counts?.themes ?? 0}</span>
            </div>
            <div className="lens-status-item">
              <span className="lens-status-label">Claims</span>
              <span className="lens-status-value">{artifacts.counts?.claims ?? 0}</span>
            </div>
            <div className="lens-status-item">
              <span className="lens-status-label">Evidence</span>
              <span className="lens-status-value">{artifacts.counts?.evidence ?? 0}</span>
            </div>
          </div>
          {artifacts.themes?.length > 0 && (
            <div className="lens-run-themes">
              <h5>Themes from this run:</h5>
              {artifacts.themes.map((t: any) => (
                <Badge key={t.id} tone="green">{t.name}</Badge>
              ))}
            </div>
          )}
        </div>
      )}

      <h4>Events Log</h4>
      <div className="lens-events-log">
        {events.length > 0 ? events.map((e: any) => (
          <div key={e.id} className="lens-event-row">
            <span className="lens-event-time mono">{shortDate(e.createdAt || e.created_at)}</span>
            <span className="lens-event-type">{humanize(e.eventType || e.event_type)}</span>
            <span className="lens-event-msg">{e.message || ''}</span>
          </div>
        )) : <p className="text-muted">No events recorded yet.</p>}
      </div>
    </Card>
  )
}

// --- Themes Tab ---
function ThemesTab() {
  const qc = useQueryClient()
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('active')

  const { data: themes = [] } = useQuery({
    queryKey: ['serenity-themes', statusFilter],
    queryFn: () => api.getSerenityThemes(statusFilter),
  })

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.updateSerenityThemeStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['serenity-themes'] }),
  })

  const deepDiveMut = useMutation({
    mutationFn: (themeId: string) => api.createSerenityThemeDeepDive(themeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
    },
  })

  const promoteMut = useMutation({
    mutationFn: (themeId: string) => api.promoteSerenityTheme(themeId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['serenity-themes'] }),
  })

  const feedbackMut = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      api.createSerenityThemeFeedback(id, action, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['serenity-themes'] }),
  })

  if (selectedTheme) {
    return <ThemeDetailPanel themeId={selectedTheme} onBack={() => setSelectedTheme(null)} />
  }

  const STATUS_FILTERS = ['active', 'watching', 'dismissed', 'promoted'] as const

  return (
    <div>
      <div className="lens-filter-bar">
        {STATUS_FILTERS.map(s => (
          <Button
            key={s}
            className={`lens-filter-btn${statusFilter === s ? ' active' : ''}`}
            onClick={() => setStatusFilter(s)}
          >
            {humanize(s)}
          </Button>
        ))}
      </div>
      <p className="text-muted">
        More/Less records owner feedback in theme history only; it does not change future research selection or prompts.
      </p>

      {!themes.length && (
        <div className="lens-empty-state">
          <p>No {humanize(statusFilter).toLowerCase()} themes.</p>
        </div>
      )}

    <div className="lens-theme-cards">
      {themes.map((t: any) => (
        <Card key={t.id} className="lens-theme-card">
          <div className="lens-theme-header">
            <h4 className="clickable" onClick={() => setSelectedTheme(t.id)}>{t.name}</h4>
            <StatusText tone={t.status === 'active' ? 'green' : t.status === 'watching' || t.status === 'promoted' ? 'blue' : 'yellow'}>
              {humanize(t.status)}
            </StatusText>
          </div>
          {t.thesis && <ThesisText text={t.thesis} className="lens-theme-thesis" />}
          {!t.thesis && t.description && <p>{t.description}</p>}
          {t.demand_wave && <p className="text-muted">Wave: {t.demand_wave}</p>}
          {(t.confidence != null || t.novelty_score != null || t.actionability_score != null) && (
            <div className="lens-theme-scores">
              {t.confidence != null && <span className="lens-score">Conf: {fmtPct(t.confidence)}</span>}
              {t.novelty_score != null && <span className="lens-score">Novel: {fmtPct(t.novelty_score)}</span>}
              {t.actionability_score != null && <span className="lens-score">Action: {fmtPct(t.actionability_score)}</span>}
            </div>
          )}
          <div className="lens-theme-actions">
            {t.status === 'active' && (
              <>
                <Button variant="secondary" className="btn-sm" onClick={() => feedbackMut.mutate({ id: t.id, action: 'more_like_this' })}>
                  +More
                </Button>
                <Button variant="secondary" className="btn-sm" onClick={() => feedbackMut.mutate({ id: t.id, action: 'not_interested' })}>
                  -Less
                </Button>
                <Button variant="secondary" className="btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'watching' })}>
                  Watch
                </Button>
                <Button variant="secondary" className="btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'dismissed' })}>
                  Dismiss
                </Button>
                <Button className="btn-sm" onClick={() => deepDiveMut.mutate(t.id)} disabled={deepDiveMut.isPending}>
                  Deep Dive
                </Button>
                <Button className="btn-sm btn-promote" onClick={() => promoteMut.mutate(t.id)} disabled={promoteMut.isPending}>
                  Promote
                </Button>
              </>
            )}
            {t.status === 'watching' && (
              <>
                <Button variant="secondary" className="btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'active' })}>
                  Reactivate
                </Button>
                <Button variant="secondary" className="btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'dismissed' })}>
                  Dismiss
                </Button>
              </>
            )}
            {t.status === 'dismissed' && (
              <Button variant="secondary" className="btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'active' })}>
                Reactivate
              </Button>
            )}
          </div>
        </Card>
      ))}
    </div>
    </div>
  )
}

// U1: Opportunity row with expandable bear_case
function OpportunityRow({
  symbol, direction, rec, grade, evidenceTier, status, bearCase
}: {
  symbol: string
  direction: string
  rec?: string
  grade: string
  evidenceTier: string
  status: string
  bearCase?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const hasBearCase = !!bearCase

  return (
    <>
      <tr
        style={hasBearCase ? { cursor: 'pointer' } : undefined}
        onClick={hasBearCase ? () => setExpanded(v => !v) : undefined}
        title={hasBearCase ? (expanded ? 'Hide bear case' : 'Show bear case') : undefined}
      >
        <td className="mono">{symbol}</td>
        <td>{direction}</td>
        <td>
          {rec ? (
            <Badge tone={VERDICT_TONE[rec] || 'muted'}>{rec.toUpperCase()}</Badge>
          ) : <span className="text-muted">—</span>}
        </td>
        <td>
          <Badge tone={GRADE_TONE[grade] || 'muted'}>{grade}</Badge>
        </td>
        <td>{evidenceTier}</td>
        <td><StatusBadge status={status} /></td>
      </tr>
      {expanded && bearCase && (
        <tr>
          <td colSpan={6} className="serenity-bearcase-cell">
            <div className="serenity-bearcase-box">
              <span className="serenity-bearcase-label">Bear Case:</span>
              <span className="serenity-bearcase-text">{bearCase}</span>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}


// --- Theme Detail Panel ---
function ThemeDetailPanel({ themeId, onBack }: { themeId: string; onBack: () => void }) {
  const { data } = useQuery({
    queryKey: ['serenity-theme-detail', themeId],
    queryFn: () => api.getSerenityThemeDetail(themeId),
  })

  if (!data) return <p>Loading…</p>

  const { theme, claims, evidence, feedback, opportunities } = data

  return (
    <Card>
      <div className="card-head card-head-space">
        <Button variant="secondary" onClick={onBack}>← Back</Button>
        <h3>{theme.name}</h3>
        <StatusText tone={theme.status === 'active' ? 'green' : 'yellow'}>{theme.status}</StatusText>
      </div>

      {theme.thesis && <ThesisText text={theme.thesis} className="lens-theme-thesis" />}
      {theme.description && <p>{theme.description}</p>}

      {(theme.confidence || theme.novelty_score || theme.actionability_score || theme.evidence_strength) && (
        <div className="lens-theme-scores">
          {theme.confidence != null && <span className="lens-score">Confidence: {fmtPct(theme.confidence)}</span>}
          {theme.novelty_score != null && <span className="lens-score">Novelty: {fmtPct(theme.novelty_score)}</span>}
          {theme.actionability_score != null && <span className="lens-score">Actionability: {fmtPct(theme.actionability_score)}</span>}
          {theme.evidence_strength != null && <span className="lens-score">Evidence: {fmtPct(theme.evidence_strength)}</span>}
        </div>
      )}

      <h4>Claims ({claims.length})</h4>
      {claims.length > 0 ? (
        <div className="lens-claims-list">
          {claims.map((c: any) => (
            <div key={c.id} className="lens-claim-item">
              <span className="lens-claim-text">{c.claim_text}</span>
              <span className="lens-claim-meta">
                {c.support_level && <StatusText tone={c.support_level === 'strong' ? 'green' : c.support_level === 'medium' ? 'blue' : 'yellow'}>{c.support_level}</StatusText>}
                {c.price_relevant ? <StatusText tone="green">price-relevant</StatusText> : null}
                <span className="text-muted">{fmtPct(c.confidence)}</span>
              </span>
            </div>
          ))}
        </div>
      ) : <p className="text-muted">No claims linked to this theme.</p>}

      <h4>Evidence ({evidence.length})</h4>
      {evidence.length > 0 ? (
        <TableWrap>
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Source</th>
                <th>Tier</th>
                <th>Symbol</th>
                <th>Lens</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((e: any) => (
                <tr key={e.id}>
                  <td>{e.title}</td>
                  <td>{humanize(e.source)}</td>
                  <td>{humanize(e.assessed_tier || e.tier)}</td>
                  <td className="mono">{e.symbol ? fmtSymbol(e.symbol, e.market_type) : '—'}</td>
                  <td>
                    {e.agent_lens
                      ? <Badge tone="muted">{e.agent_lens}</Badge>
                      : <span className="text-muted">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      ) : <p className="text-muted">No evidence linked to this theme.</p>}

      {opportunities.length > 0 && (
        <>
          <h4>Opportunities ({opportunities.length})</h4>
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th>Verdict</th>
                  <th>Grade</th>
                  <th>Tier</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {opportunities.map((o: any) => {
                  const verdict = parseVerdict(o.verdict_json)
                  const rec = verdict.recommendation as string | undefined
                  const bearCase = verdict.bear_case as string | undefined
                  const grade = (o.researchability_grade as string | undefined)?.toUpperCase() || 'C'
                  return (
                    <OpportunityRow
                      key={o.id}
                      symbol={fmtSymbol(o.symbol, o.market_type)}
                      direction={humanize(o.direction)}
                      rec={rec}
                      grade={grade}
                      // #167 fix: previously showed `evidence_tier` — that's the
                      // PROPOSED tier the caller submitted, advisory only. The
                      // authoritative tier the risk engine actually uses is
                      // `assessed_tier` (set by the serenity evidence pipeline).
                      // Prefer assessed; fall back to proposed when the pipeline
                      // hasn't scored it yet.
                      evidenceTier={humanize((o.assessed_tier as string | undefined) || (o.evidence_tier as string | undefined))}
                      status={o.status}
                      bearCase={bearCase}
                    />
                  )
                })}
              </tbody>
            </table>
          </TableWrap>
        </>
      )}

      {feedback.length > 0 && (
        <>
          <h4>Feedback History ({feedback.length})</h4>
          <div className="lens-events-log">
            {feedback.map((f: any) => (
              <div key={f.id} className="lens-event-row">
                <span className="lens-event-time mono">{shortDate(f.created_at)}</span>
                <span className="lens-event-type">{humanize(f.action)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

// --- Evidence Tab ---
function EvidenceTab() {
  const { data: evidence = [] } = useQuery({
    queryKey: ['serenity-evidence-all'],
    queryFn: () => api.getSerenityEvidence(),
  })

  if (!evidence.length) {
    return (
      <div className="lens-empty-state">
        <p>No evidence collected yet.</p>
        <p className="text-muted">Evidence appears after research runs complete their collection stage.</p>
      </div>
    )
  }

  return (
    <Card>
      <h3>Evidence ({evidence.length})</h3>
      <TableWrap>
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Title</th>
              <th>Tier</th>
              <th>Source</th>
              <th>Confidence</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((e: any) => (
              <tr key={e.id}>
                <td className="mono">{e.symbol ? fmtSymbol(e.symbol, e.market_type) : '—'}</td>
                <td>
                  {e.title}
                  {e.source === 'prediction_market' && (
                    <a href="/prediction" className="lens-cross-link" title="View in Prediction Lens">
                      → Prediction Lens
                    </a>
                  )}
                </td>
                <td>{e.tier}</td>
                <td>
                  {e.source === 'prediction_market'
                    ? <Badge tone="blue">prediction</Badge>
                    : e.source}
                </td>
                <td>{e.confidence || '—'}</td>
                <td>{shortDate(e.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  )
}

// --- Profile Tab ---
function ProfileTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['serenity-profile'],
    queryFn: api.getSerenityProfile,
  })

  const [editBrief, setEditBrief] = useState('')
  const [parseResult, setParseResult] = useState<any>(null)

  const parseMut = useMutation({
    mutationFn: () => api.parseSerenityBrief(editBrief),
    onSuccess: (d) => setParseResult(d),
  })

  const saveMut = useMutation({
    mutationFn: (body: any) => api.updateSerenityProfile(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-profile'] })
      setParseResult(null)
      setEditBrief('')
    },
  })

  const profile = data?.profile

  return (
    <div>
      <Card>
        <h3>Research Preferences</h3>
        {profile ? (
          <div className="lens-status-grid">
            <div className="lens-status-item">
              <span className="lens-status-label">Include</span>
              <span className="lens-status-value">{(profile.includeTopics || []).join(', ') || '—'}</span>
            </div>
            <div className="lens-status-item">
              <span className="lens-status-label">Exclude</span>
              <span className="lens-status-value">{(profile.excludeTopics || []).join(', ') || '—'}</span>
            </div>
            <div className="lens-status-item">
              <span className="lens-status-label">Time Horizon</span>
              <span className="lens-status-value">{profile.timeHorizon || '—'}</span>
            </div>
          </div>
        ) : (
          <p className="text-muted">No profile set. Use the brief below to define your research preferences.</p>
        )}
      </Card>

      <Card>
        <h3>Update Preferences</h3>
        <div className="lens-brief-input">
          <textarea
            placeholder="Describe your research focus in natural language (e.g. '关注 AI 半导体供应链\navoid meme coins')…"
            value={editBrief}
            onChange={e => setEditBrief(e.target.value)}
            rows={3}
            maxLength={2000}
          />
          <div className="lens-brief-actions">
            <Button variant="secondary" onClick={() => parseMut.mutate()} disabled={!editBrief || parseMut.isPending}>
              {parseMut.isPending ? 'Parsing…' : 'Preview'}
            </Button>
            {parseResult && (
              <Button onClick={() => saveMut.mutate({
                includeTopics: parseResult.parsed?.includeTopics,
                excludeTopics: parseResult.parsed?.excludeTopics,
                timeHorizon: parseResult.parsed?.timeHorizon,
                rawBrief: editBrief,
              })} disabled={saveMut.isPending}>
                {saveMut.isPending ? 'Saving…' : 'Save to Profile'}
              </Button>
            )}
          </div>
        </div>

        {parseResult && (
          <div className="lens-parse-result">
            <h4>Parsed Result</h4>
            <p><b>Include:</b> {(parseResult.parsed?.includeTopics || []).join(', ') || '(none)'}</p>
            <p><b>Exclude:</b> {(parseResult.parsed?.excludeTopics || []).join(', ') || '(none)'}</p>
            <p><b>Time Horizon:</b> {parseResult.parsed?.timeHorizon || '(not specified)'}</p>
            {parseResult.warnings?.length > 0 && (
              <div className="lens-status-warning">
                {parseResult.warnings.map((w: string, i: number) => <div key={i}>{w}</div>)}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

// --- Main Page ---
export default function SerenityLens() {
  const [activeTab, setActiveTab] = useState<Tab>('overview')

  return (
    <div>
      <PageHeader
        title="Serenity Lens"
        role="Research workbench: observe agent runs, trigger research, manage themes and preferences."
      />

      <ResearchStatusCard />

      <Tabs
        value={activeTab}
        onChange={(id) => setActiveTab(id as Tab)}
        tabs={([
          { id: 'overview', label: 'Overview', render: () => <OverviewTab /> },
          { id: 'runs',     label: 'Runs',     render: () => <RunsTab /> },
          { id: 'themes',   label: 'Themes',   render: () => <ThemesTab /> },
          { id: 'evidence', label: 'Evidence', render: () => <EvidenceTab /> },
          { id: 'profile',  label: 'Profile',  render: () => <ProfileTab /> },
        ]) as TabDef[]}
      />
    </div>
  )
}
