import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { StatusBadge } from '../components/DataTable'
import { fmtTimestamp, humanize } from '../lib/format'

type Tab = 'overview' | 'runs' | 'themes' | 'evidence' | 'profile'

const STATUS_COLORS: Record<string, string> = {
  idle: 'var(--muted)',
  healthy: 'var(--green)',
  running: 'var(--blue)',
  rate_limited: 'var(--yellow)',
  error: 'var(--red)',
}

function StatusDot({ status }: { status: string }) {
  return <span className="dot" style={{ background: STATUS_COLORS[status] || 'var(--muted)' }} />
}

// Run status uses the shared canonical StatusBadge (humanized label + consistent color
// across the whole dashboard). The old local RunStatusBadge disagreed with it (running→blue
// vs the shared running→yellow) and is removed.
function RunStatusBadge({ status }: { status: string }) {
  return <StatusBadge status={status} />
}

const shortDate = (s: string | null | undefined) => fmtTimestamp(s)

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
            <StatusDot status={status.engineStatus} />
            {status.engineStatus}
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
      <section className="card">
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
            <label className="lens-checkbox">
              <input type="checkbox" checked={saveToProfile} onChange={e => setSaveToProfile(e.target.checked)} />
              Save preferences to profile
            </label>
            <button
              onClick={() => createRun.mutate()}
              disabled={createRun.isPending || hasActive}
              title={hasActive ? 'A run is already active' : 'Start a new research run'}
            >
              {createRun.isPending ? 'Starting…' : hasActive ? 'Run Active' : 'Run Research'}
            </button>
          </div>
          {createRun.isError && (
            <div className="lens-status-warning">
              {(createRun.error as any)?.message || 'Failed to start run'}
            </div>
          )}
        </div>
        <p className="text-muted lens-disclaimer">Research only — does not execute trades or modify positions.</p>
      </section>

      <section className="card">
        <h3>Recent Activity</h3>
        <RecentRunsList limit={5} />
      </section>
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
        <section className="card">
          <h3>Run History</h3>
          <RecentRunsList limit={20} onSelect={setSelectedRunId} />
        </section>
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
    <div className="lens-table-wrap">
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
              <td className="mono">{r.artifactCounts?.themes ?? JSON.parse(r.artifact_counts || '{}').themes ?? 0}</td>
              <td>{r.artifactCounts?.claims ?? JSON.parse(r.artifact_counts || '{}').claims ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
    enabled: !!data?.run && data.run.status !== 'running' && data.run.status !== 'queued',
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
    <section className="card">
      <div className="card-head card-head-space">
        <button className="secondary" onClick={onBack}>← Back</button>
        <h3>Run Detail</h3>
        <div className="lens-run-actions">
          {canCancel && (
            <button className="secondary" onClick={() => cancelMut.mutate()} disabled={cancelMut.isPending}>
              {cancelMut.isPending ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {canRetry && (
            <button onClick={() => retryMut.mutate()} disabled={retryMut.isPending}>
              {retryMut.isPending ? 'Retrying…' : 'Retry'}
            </button>
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
        {run.status === 'running' && (run.startedAt || run.started_at) && (
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
                <span key={t.id} className="badge badge-green">{t.name}</span>
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
    </section>
  )
}

// --- Themes Tab ---
function ThemesTab() {
  const qc = useQueryClient()
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null)

  const { data: themes = [] } = useQuery({
    queryKey: ['serenity-themes'],
    queryFn: api.getSerenityThemes,
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

  if (!themes.length) {
    return (
      <div className="lens-empty-state">
        <p>No themes discovered yet.</p>
        <p className="text-muted">Run research to generate themes from market evidence and claims.</p>
      </div>
    )
  }

  return (
    <div className="lens-theme-cards">
      {themes.map((t: any) => (
        <div key={t.id} className="lens-theme-card">
          <div className="lens-theme-header">
            <h4 className="clickable" onClick={() => setSelectedTheme(t.id)}>{t.name}</h4>
            <span className={`badge badge-${t.status === 'active' ? 'green' : t.status === 'watching' || t.status === 'promoted' ? 'blue' : 'yellow'}`}>
              {humanize(t.status)}
            </span>
          </div>
          {t.thesis && <p className="lens-theme-thesis">{t.thesis}</p>}
          {!t.thesis && t.description && <p>{t.description}</p>}
          {t.demand_wave && <p className="text-muted">Wave: {t.demand_wave}</p>}
          {(t.confidence || t.novelty_score || t.actionability_score) && (
            <div className="lens-theme-scores">
              {t.confidence && <span className="lens-score">Conf: {(t.confidence * 100).toFixed(0)}%</span>}
              {t.novelty_score && <span className="lens-score">Novel: {(t.novelty_score * 100).toFixed(0)}%</span>}
              {t.actionability_score && <span className="lens-score">Action: {(t.actionability_score * 100).toFixed(0)}%</span>}
            </div>
          )}
          <div className="lens-theme-actions">
            {t.status === 'active' && (
              <>
                <button className="secondary btn-sm" onClick={() => feedbackMut.mutate({ id: t.id, action: 'more_like_this' })}>
                  +More
                </button>
                <button className="secondary btn-sm" onClick={() => feedbackMut.mutate({ id: t.id, action: 'not_interested' })}>
                  -Less
                </button>
                <button className="secondary btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'watching' })}>
                  Watch
                </button>
                <button className="secondary btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'dismissed' })}>
                  Dismiss
                </button>
                <button className="btn-sm" onClick={() => deepDiveMut.mutate(t.id)} disabled={deepDiveMut.isPending}>
                  Deep Dive
                </button>
                <button className="btn-sm btn-promote" onClick={() => promoteMut.mutate(t.id)} disabled={promoteMut.isPending}>
                  Promote
                </button>
              </>
            )}
            {t.status === 'watching' && (
              <>
                <button className="secondary btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'active' })}>
                  Reactivate
                </button>
                <button className="secondary btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'dismissed' })}>
                  Dismiss
                </button>
              </>
            )}
            {t.status === 'dismissed' && (
              <button className="secondary btn-sm" onClick={() => statusMut.mutate({ id: t.id, status: 'active' })}>
                Reactivate
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
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
    <section className="card">
      <div className="card-head card-head-space">
        <button className="secondary" onClick={onBack}>← Back</button>
        <h3>{theme.name}</h3>
        <span className={`badge badge-${theme.status === 'active' ? 'green' : 'yellow'}`}>{theme.status}</span>
      </div>

      {theme.thesis && <p className="lens-theme-thesis"><b>Thesis:</b> {theme.thesis}</p>}
      {theme.description && <p>{theme.description}</p>}

      {(theme.confidence || theme.novelty_score || theme.actionability_score || theme.evidence_strength) && (
        <div className="lens-theme-scores">
          {theme.confidence != null && <span className="lens-score">Confidence: {(theme.confidence * 100).toFixed(0)}%</span>}
          {theme.novelty_score != null && <span className="lens-score">Novelty: {(theme.novelty_score * 100).toFixed(0)}%</span>}
          {theme.actionability_score != null && <span className="lens-score">Actionability: {(theme.actionability_score * 100).toFixed(0)}%</span>}
          {theme.evidence_strength != null && <span className="lens-score">Evidence: {(theme.evidence_strength * 100).toFixed(0)}%</span>}
        </div>
      )}

      <h4>Claims ({claims.length})</h4>
      {claims.length > 0 ? (
        <div className="lens-claims-list">
          {claims.map((c: any) => (
            <div key={c.id} className="lens-claim-item">
              <span className="lens-claim-text">{c.claim_text}</span>
              <span className="lens-claim-meta">
                {c.support_level && <span className={`badge badge-${c.support_level === 'strong' ? 'green' : c.support_level === 'medium' ? 'blue' : 'yellow'}`}>{c.support_level}</span>}
                {c.price_relevant ? <span className="badge badge-green">price-relevant</span> : null}
                <span className="text-muted">{(c.confidence * 100).toFixed(0)}%</span>
              </span>
            </div>
          ))}
        </div>
      ) : <p className="text-muted">No claims linked to this theme.</p>}

      <h4>Evidence ({evidence.length})</h4>
      {evidence.length > 0 ? (
        <div className="lens-table-wrap">
          <table>
            <thead><tr><th>Title</th><th>Source</th><th>Tier</th><th>Symbol</th></tr></thead>
            <tbody>
              {evidence.map((e: any) => (
                <tr key={e.id}>
                  <td>{e.title}</td>
                  <td>{e.source}</td>
                  <td>{e.tier}</td>
                  <td>{e.symbol || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="text-muted">No evidence linked to this theme.</p>}

      {opportunities.length > 0 && (
        <>
          <h4>Opportunities ({opportunities.length})</h4>
          <div className="lens-table-wrap">
            <table>
              <thead><tr><th>Symbol</th><th>Direction</th><th>Tier</th><th>Status</th></tr></thead>
              <tbody>
                {opportunities.map((o: any) => (
                  <tr key={o.id}>
                    <td>{o.symbol}</td>
                    <td>{o.direction}</td>
                    <td>{o.evidence_tier}</td>
                    <td>{o.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
    </section>
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
    <section className="card">
      <h3>Evidence ({evidence.length})</h3>
      <div className="lens-table-wrap">
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
                <td>{e.symbol || '—'}</td>
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
                    ? <span className="badge badge-blue">prediction</span>
                    : e.source}
                </td>
                <td>{e.confidence}</td>
                <td>{shortDate(e.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
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
      <section className="card">
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
      </section>

      <section className="card">
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
            <button className="secondary" onClick={() => parseMut.mutate()} disabled={!editBrief || parseMut.isPending}>
              {parseMut.isPending ? 'Parsing…' : 'Preview'}
            </button>
            {parseResult && (
              <button onClick={() => saveMut.mutate({
                includeTopics: parseResult.parsed?.includeTopics,
                excludeTopics: parseResult.parsed?.excludeTopics,
                timeHorizon: parseResult.parsed?.timeHorizon,
                rawBrief: editBrief,
              })} disabled={saveMut.isPending}>
                {saveMut.isPending ? 'Saving…' : 'Save to Profile'}
              </button>
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
      </section>
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

      <div className="lens-tabs">
        {(['overview', 'runs', 'themes', 'evidence', 'profile'] as Tab[]).map(tab => (
          <button
            key={tab}
            className={`lens-tab ${activeTab === tab ? 'lens-tab-active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      <div className="lens-tab-content">
        {activeTab === 'overview' && <OverviewTab />}
        {activeTab === 'runs' && <RunsTab />}
        {activeTab === 'themes' && <ThemesTab />}
        {activeTab === 'evidence' && <EvidenceTab />}
        {activeTab === 'profile' && <ProfileTab />}
      </div>
    </div>
  )
}
