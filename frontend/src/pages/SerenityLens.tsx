import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  SerenityEvidence,
  SerenityOpportunity,
  SerenityResearchCycle,
  SerenityRiskGate,
  SerenityRunSummary,
  SerenityRunEventItem,
  SerenityTheme,
  SerenityThemeOverview,
  SerenityTimelinePoint,
  SerenityWorkbenchStatus,
} from '../api/client'
import PageHeader from '../components/PageHeader'

type TabId = 'overview' | 'runs' | 'themes' | 'evidence' | 'profile'

const STATUS_LABELS: Record<string, string> = {
  idle: 'Idle',
  queued: 'Queued',
  running: 'Running',
  retrying: 'Retrying',
  rate_limited: 'Rate Limited',
  failed: 'Failed',
  healthy: 'Healthy',
}

const RUN_STATUS_COLORS: Record<string, string> = {
  queued: 'badge-yellow',
  running: 'badge-blue',
  waiting_retry: 'badge-yellow',
  rate_limited: 'badge-yellow',
  completed: 'badge-green',
  completed_empty: 'badge-green',
  partial: 'badge-yellow',
  failed: 'badge-red',
  cancelled: 'badge-red',
}

function StatusDot({ status }: { status: string }) {
  const color = status === 'healthy' || status === 'completed' || status === 'completed_empty'
    ? 'green'
    : status === 'running' || status === 'queued'
      ? 'blue'
      : status === 'rate_limited' || status === 'retrying' || status === 'waiting_retry'
        ? 'yellow'
        : status === 'failed'
          ? 'red'
          : 'muted'
  return <span className={`dot dot-${color}`} />
}

function shortDate(s: string | null | undefined) {
  if (!s) return '—'
  return s.slice(0, 16).replace('T', ' ')
}

function elapsed(start: string | null | undefined, end: string | null | undefined): string {
  if (!start) return '—'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const sec = Math.floor((e - s) / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  return `${min}m ${sec % 60}s`
}

export default function SerenityLens() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<TabId>('overview')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [brief, setBrief] = useState('')
  const [saveToProfile, setSaveToProfile] = useState(false)

  const { data: status } = useQuery({
    queryKey: ['serenity-status'],
    queryFn: api.getSerenityStatus,
    refetchInterval: 5000,
  })

  const { data: themes = [] } = useQuery({
    queryKey: ['serenity-themes'],
    queryFn: api.getSerenityThemes,
  })

  const { data: runsData } = useQuery({
    queryKey: ['serenity-runs'],
    queryFn: () => api.getSerenityRuns({ limit: 20 }),
    refetchInterval: 5000,
  })
  const runs = runsData?.items || []

  const { data: runDetail } = useQuery({
    queryKey: ['serenity-run-detail', selectedRunId],
    queryFn: () => api.getSerenityRunDetail(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: selectedRunId ? 3000 : false,
  })

  const triggerMut = useMutation({
    mutationFn: () => api.createSerenityRun({
      mode: 'standard',
      triggerType: 'manual',
      brief: brief || null,
      saveToProfile,
    }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
      setSelectedRunId(data.runId)
      setBrief('')
    },
  })

  const retryMut = useMutation({
    mutationFn: (runId: string) => api.retrySerenityRun(runId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      setSelectedRunId(data.runId)
    },
  })

  const themeStatusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.updateSerenityThemeStatus(id, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-themes'] })
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
    },
  })

  const deepDiveMut = useMutation({
    mutationFn: (themeId: string) => api.createSerenityThemeDeepDive(themeId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['serenity-runs'] })
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      setSelectedRunId(data.runId)
      setTab('runs')
    },
  })

  const isRunning = status?.currentRun != null
  const canTrigger = !isRunning && !triggerMut.isPending

  return (
    <div>
      <PageHeader
        title="Serenity Lens"
        role="Research workbench: observe agent runs, manage themes, track evidence, guide research direction."
      />

      {/* Research Engine Status Card */}
      <ResearchStatusCard status={status} />

      {/* Research Brief + Trigger */}
      <section className="card">
        <div className="card-head card-head-space">
          <h3>Research Brief</h3>
          <button
            className="primary"
            disabled={!canTrigger}
            onClick={() => triggerMut.mutate()}
          >
            {triggerMut.isPending ? 'Queuing…' : isRunning ? 'Run in progress…' : 'Run Research Cycle'}
          </button>
        </div>
        <textarea
          className="lens-brief-input"
          value={brief}
          onChange={e => setBrief(e.target.value)}
          placeholder="Enter research focus (e.g. 关注 AI 半导体供应链, avoid meme coins, prioritize 1-7d catalysts)"
          rows={3}
          maxLength={4000}
        />
        <div className="lens-brief-options">
          <label>
            <input type="checkbox" checked={saveToProfile} onChange={e => setSaveToProfile(e.target.checked)} />
            Save as default profile
          </label>
          <span className="notice-text">Research guidance only. Does not execute trades.</span>
        </div>
        {triggerMut.isError && (
          <p className="error-text">{(triggerMut.error as Error).message}</p>
        )}
      </section>

      {/* Tabs */}
      <div className="lens-tabs">
        {(['overview', 'runs', 'themes', 'evidence', 'profile'] as TabId[]).map(t => (
          <button
            key={t}
            className={`lens-tab ${tab === t ? 'lens-tab-active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {tab === 'overview' && (
        <OverviewTab
          themes={themes}
          runs={runs}
          status={status}
          onSelectRun={id => { setSelectedRunId(id); setTab('runs') }}
          onThemeAction={(id, action) => themeStatusMut.mutate({ id, status: action })}
          onDeepDive={id => deepDiveMut.mutate(id)}
        />
      )}
      {tab === 'runs' && (
        <RunsTab
          runs={runs}
          selectedRunId={selectedRunId}
          runDetail={runDetail}
          onSelectRun={setSelectedRunId}
          onRetry={id => retryMut.mutate(id)}
        />
      )}
      {tab === 'themes' && (
        <ThemesTab
          themes={themes}
          onStatusChange={(id, s) => themeStatusMut.mutate({ id, status: s })}
          onDeepDive={id => deepDiveMut.mutate(id)}
        />
      )}
      {tab === 'evidence' && <EvidenceTab themes={themes} />}
      {tab === 'profile' && <ProfileTab />}
    </div>
  )
}

function ResearchStatusCard({ status }: { status: SerenityWorkbenchStatus | undefined }) {
  if (!status) {
    return (
      <section className="card">
        <h3>Research Engine</h3>
        <p>Loading status…</p>
      </section>
    )
  }

  const engineLabel = STATUS_LABELS[status.engineStatus] || status.engineStatus

  return (
    <section className="card">
      <div className="card-head">
        <h3>Research Engine</h3>
        <span className="lens-engine-status">
          <StatusDot status={status.engineStatus} />
          <b>{engineLabel}</b>
        </span>
      </div>
      <div className="lens-status-grid">
        <div>
          <span className="lens-status-label">Last attempted</span>
          <span>{status.lastAttemptedRun ? shortDate(status.lastAttemptedRun.startedAt || status.lastAttemptedRun.createdAt) : '—'}</span>
          {status.lastAttemptedRun?.errorType && (
            <span className="badge badge-red">{status.lastAttemptedRun.errorType.replace(/_/g, ' ')}</span>
          )}
        </div>
        <div>
          <span className="lens-status-label">Last successful</span>
          <span>{status.lastSuccessfulRun ? shortDate(status.lastSuccessfulRun.finishedAt) : '—'}</span>
        </div>
        <div>
          <span className="lens-status-label">Active themes</span>
          <span>{status.artifactCounts.activeThemes}</span>
        </div>
        <div>
          <span className="lens-status-label">Profile</span>
          <span>{status.profileSummary || 'No profile set'}</span>
        </div>
      </div>
      {status.currentRun && (
        <div className="lens-progress-bar">
          <StatusDot status="running" />
          <span>Running: {status.currentRun.stage || 'initializing'}…</span>
          <span className="mono">{elapsed(status.currentRun.startedAt, null)}</span>
        </div>
      )}
    </section>
  )
}

function OverviewTab({
  themes, runs, status, onSelectRun, onThemeAction, onDeepDive,
}: {
  themes: SerenityTheme[]
  runs: SerenityRunSummary[]
  status: SerenityWorkbenchStatus | undefined
  onSelectRun: (id: string) => void
  onThemeAction: (id: string, action: string) => void
  onDeepDive: (id: string) => void
}) {
  const activeThemes = themes.filter(t => t.status === 'active' || t.status === 'new')

  if (themes.length === 0 && runs.length === 0) {
    return (
      <section className="card">
        <h3>Welcome to Serenity Lens</h3>
        <EmptyState status={status} />
      </section>
    )
  }

  return (
    <>
      {themes.length === 0 && <section className="card"><EmptyState status={status} /></section>}

      {activeThemes.length > 0 && (
        <section className="card">
          <h3>Active Themes</h3>
          <div className="lens-theme-cards">
            {activeThemes.slice(0, 8).map(theme => (
              <div key={theme.id} className="lens-theme-card">
                <div className="lens-theme-card-head">
                  <b>{theme.name}</b>
                  <span className={`badge ${theme.status === 'new' ? 'badge-blue' : 'badge-green'}`}>{theme.status}</span>
                </div>
                {theme.objective && <p>{theme.objective}</p>}
                {theme.demand_wave && <p className="notice-text">Wave: {theme.demand_wave}</p>}
                <div className="lens-theme-actions">
                  <button className="secondary" onClick={() => onThemeAction(theme.id, 'watching')}>Watch</button>
                  <button className="secondary" onClick={() => onThemeAction(theme.id, 'dismissed')}>Dismiss</button>
                  <button className="secondary" onClick={() => onDeepDive(theme.id)}>Deep Dive</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {runs.length > 0 && (
        <section className="card">
          <h3>Recent Runs</h3>
          <div className="lens-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th>Mode</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Artifacts</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 5).map(r => (
                  <tr key={r.id} className="clickable" onClick={() => onSelectRun(r.id)}>
                    <td><span className={`badge ${RUN_STATUS_COLORS[r.status || ''] || ''}`}>{r.status}</span></td>
                    <td>{r.triggerType}</td>
                    <td>{r.mode}</td>
                    <td>{shortDate(r.startedAt || r.createdAt)}</td>
                    <td>{elapsed(r.startedAt, r.finishedAt)}</td>
                    <td className="mono">
                      {r.artifactCounts ? Object.entries(r.artifactCounts).filter(([, v]) => v > 0).map(([k, v]) => `${k}:${v}`).join(' ') || '—' : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  )
}

function EmptyState({ status }: { status: SerenityWorkbenchStatus | undefined }) {
  if (!status) return <p>Loading…</p>

  if (!status.lastAttemptedRun) {
    return (
      <div>
        <p><b>Research engine has not run yet.</b></p>
        <p>Start the first research cycle to generate themes, evidence, and opportunities.</p>
      </div>
    )
  }

  const last = status.lastAttemptedRun
  if (last.status === 'failed' || last.status === 'rate_limited') {
    return (
      <div>
        <p><b>No research themes yet.</b></p>
        <p>Last run: <span className={`badge ${RUN_STATUS_COLORS[last.status || '']}`}>{last.status}</span></p>
        {last.errorType && <p>Reason: {last.errorType.replace(/_/g, ' ')}</p>}
        {last.errorMessage && <p className="notice-text">{last.errorMessage}</p>}
        {last.artifactCounts && Object.values(last.artifactCounts).some(v => v > 0) && (
          <p>Partial artifacts: {Object.entries(last.artifactCounts).filter(([, v]) => v > 0).map(([k, v]) => `${v} ${k}`).join(', ')}</p>
        )}
      </div>
    )
  }

  if (last.status === 'completed_empty') {
    return (
      <div>
        <p><b>No research themes found in the last cycle.</b></p>
        <p>The run completed successfully, but no themes passed confidence and evidence thresholds.</p>
        <p className="notice-text">Try broadening the research brief or running a deeper cycle.</p>
      </div>
    )
  }

  return <p>No themes found. Try running a research cycle.</p>
}

function RunsTab({
  runs, selectedRunId, runDetail, onSelectRun, onRetry,
}: {
  runs: SerenityRunSummary[]
  selectedRunId: string | null
  runDetail: { run: SerenityRunSummary; events: SerenityRunEventItem[] } | undefined
  onSelectRun: (id: string | null) => void
  onRetry: (id: string) => void
}) {
  return (
    <>
      <section className="card">
        <h3>Run History</h3>
        <div className="lens-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Trigger</th>
                <th>Mode</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Error</th>
                <th>Artifacts</th>
              </tr>
            </thead>
            <tbody>
              {runs.length > 0 ? runs.map(r => (
                <tr
                  key={r.id}
                  className={`clickable ${selectedRunId === r.id ? 'selected-row' : ''}`}
                  onClick={() => onSelectRun(r.id)}
                >
                  <td><span className={`badge ${RUN_STATUS_COLORS[r.status || ''] || ''}`}>{r.status}</span></td>
                  <td>{r.triggerType}</td>
                  <td>{r.mode}</td>
                  <td>{shortDate(r.startedAt || r.createdAt)}</td>
                  <td>{elapsed(r.startedAt, r.finishedAt)}</td>
                  <td>{r.errorType ? r.errorType.replace(/_/g, ' ') : '—'}</td>
                  <td className="mono">
                    {r.artifactCounts ? Object.entries(r.artifactCounts).filter(([, v]) => v > 0).map(([k, v]) => `${k}:${v}`).join(' ') || '—' : '—'}
                  </td>
                </tr>
              )) : (
                <tr><td colSpan={7}>No runs yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {runDetail && (
        <RunDetailPanel run={runDetail.run} events={runDetail.events} onRetry={onRetry} />
      )}
    </>
  )
}

function RunDetailPanel({ run, events, onRetry }: {
  run: SerenityRunSummary
  events: SerenityRunEventItem[]
  onRetry: (id: string) => void
}) {
  const canRetry = run.status === 'failed' || run.status === 'rate_limited' || run.status === 'partial'

  return (
    <section className="card">
      <div className="card-head card-head-space">
        <h3>Run Detail</h3>
        {canRetry && (
          <button className="secondary" onClick={() => onRetry(run.id)}>Retry</button>
        )}
      </div>
      <div className="lens-run-meta">
        <p><b>ID:</b> <span className="mono">{run.id}</span></p>
        <p><b>Status:</b> <span className={`badge ${RUN_STATUS_COLORS[run.status || ''] || ''}`}>{run.status}</span></p>
        <p><b>Trigger:</b> {run.triggerType} / <b>Mode:</b> {run.mode}</p>
        <p><b>Stage:</b> {run.stage || '—'}</p>
        <p><b>Started:</b> {shortDate(run.startedAt)} / <b>Finished:</b> {shortDate(run.finishedAt)}</p>
        <p><b>Duration:</b> {elapsed(run.startedAt, run.finishedAt)}</p>
        {run.errorType && <p><b>Error:</b> {run.errorType.replace(/_/g, ' ')} — {run.errorMessage}</p>}
      </div>

      <div className="serenity-divider" />
      <h4>Events</h4>
      <div className="lens-events-log">
        {events.length > 0 ? events.map(evt => (
          <div key={evt.id} className="lens-event-row">
            <span className="mono lens-event-time">{shortDate(evt.created_at)}</span>
            <span className={`badge ${evt.event_type.includes('failed') ? 'badge-red' : evt.event_type.includes('completed') ? 'badge-green' : 'badge-blue'}`}>
              {evt.event_type}
            </span>
            <span>{evt.message || ''}</span>
          </div>
        )) : <p>No events recorded.</p>}
      </div>
    </section>
  )
}

function ThemesTab({ themes, onStatusChange, onDeepDive }: {
  themes: SerenityTheme[]
  onStatusChange: (id: string, status: string) => void
  onDeepDive: (id: string) => void
}) {
  const [filter, setFilter] = useState<string>('')
  const filtered = themes.filter(t => !filter || t.status === filter)

  return (
    <section className="card">
      <div className="card-head card-head-space">
        <h3>Themes</h3>
        <div className="lens-controls">
          <select value={filter} onChange={e => setFilter(e.target.value)}>
            <option value="">All statuses</option>
            <option value="new">New</option>
            <option value="active">Active</option>
            <option value="watching">Watching</option>
            <option value="promoted">Promoted</option>
            <option value="dismissed">Dismissed</option>
            <option value="expired">Expired</option>
          </select>
        </div>
      </div>
      <div className="lens-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Wave</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length > 0 ? filtered.map(t => (
              <tr key={t.id}>
                <td><b>{t.name}</b></td>
                <td><span className={`badge ${t.status === 'active' ? 'badge-green' : t.status === 'new' ? 'badge-blue' : t.status === 'dismissed' ? 'badge-red' : 'badge-yellow'}`}>{t.status}</span></td>
                <td>{t.demand_wave || '—'}</td>
                <td>{shortDate(t.created_at)}</td>
                <td>
                  <div className="lens-theme-actions">
                    {t.status !== 'watching' && <button className="secondary" onClick={() => onStatusChange(t.id, 'watching')}>Watch</button>}
                    {t.status !== 'active' && t.status !== 'dismissed' && <button className="secondary" onClick={() => onStatusChange(t.id, 'active')}>Activate</button>}
                    {t.status !== 'dismissed' && <button className="secondary" onClick={() => onStatusChange(t.id, 'dismissed')}>Dismiss</button>}
                    <button className="secondary" onClick={() => onDeepDive(t.id)}>Deep Dive</button>
                  </div>
                </td>
              </tr>
            )) : (
              <tr><td colSpan={5}>No themes match filter.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function EvidenceTab({ themes }: { themes: SerenityTheme[] }) {
  const [selectedThemeId, setSelectedThemeId] = useState('')

  useEffect(() => {
    if (!selectedThemeId && themes.length > 0) {
      setSelectedThemeId(themes[0].id)
    }
  }, [themes, selectedThemeId])

  const { data: evidence = [] } = useQuery({
    queryKey: ['serenity-evidence', selectedThemeId],
    queryFn: () => api.getSerenityEvidence(selectedThemeId || undefined),
    enabled: !!selectedThemeId,
  })

  return (
    <section className="card">
      <div className="card-head card-head-space">
        <h3>Evidence</h3>
        <select value={selectedThemeId} onChange={e => setSelectedThemeId(e.target.value)}>
          {themes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </div>
      <div className="lens-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Source</th>
              <th>Tier</th>
              <th>Confidence</th>
              <th>Symbol</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {evidence.length > 0 ? (evidence as SerenityEvidence[]).map(e => (
              <tr key={e.id}>
                <td>{e.title}</td>
                <td>{e.source}</td>
                <td>{e.tier}</td>
                <td>{e.confidence}</td>
                <td>{e.symbol || '—'}</td>
                <td>{shortDate(e.created_at)}</td>
              </tr>
            )) : (
              <tr><td colSpan={6}>No evidence for this theme.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ProfileTab() {
  const qc = useQueryClient()
  const { data: profileData } = useQuery({
    queryKey: ['serenity-profile'],
    queryFn: api.getSerenityProfile,
  })

  const profile = profileData?.profile
  const [editBrief, setEditBrief] = useState('')
  const [parseResult, setParseResult] = useState<{ parsed: Record<string, unknown>; warnings: string[] } | null>(null)

  const parseMut = useMutation({
    mutationFn: (brief: string) => api.parseSerenityBrief(brief),
    onSuccess: (data) => setParseResult(data),
  })

  const saveMut = useMutation({
    mutationFn: () => api.createSerenityRun({
      mode: 'standard',
      triggerType: 'manual',
      brief: editBrief,
      saveToProfile: true,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['serenity-profile'] })
      qc.invalidateQueries({ queryKey: ['serenity-status'] })
      setEditBrief('')
      setParseResult(null)
    },
  })

  return (
    <section className="card">
      <h3>Research Profile</h3>
      {profile ? (
        <div className="lens-profile-display">
          <p><b>Include topics:</b> {(profile.include_topics as unknown as string[] || []).join(', ') || '—'}</p>
          <p><b>Exclude topics:</b> {(profile.exclude_topics as unknown as string[] || []).join(', ') || '—'}</p>
          <p><b>Time horizon:</b> {(profile as any).time_horizon || '—'}</p>
          <p><b>Risk bias:</b> {(profile as any).risk_bias || '—'}</p>
          <p><b>Notes:</b> {(profile as any).raw_notes || '—'}</p>
        </div>
      ) : (
        <p>No profile saved yet. Enter a research brief below to create one.</p>
      )}

      <div className="serenity-divider" />
      <h4>Update Profile</h4>
      <textarea
        className="lens-brief-input"
        value={editBrief}
        onChange={e => setEditBrief(e.target.value)}
        placeholder="Enter new research preferences…"
        rows={3}
        maxLength={4000}
      />
      <div className="lens-brief-options">
        <button className="secondary" onClick={() => parseMut.mutate(editBrief)} disabled={!editBrief || parseMut.isPending}>
          Preview Parse
        </button>
        <button className="primary" onClick={() => saveMut.mutate()} disabled={!editBrief || saveMut.isPending}>
          Save to Profile & Run
        </button>
      </div>

      {parseResult && (
        <div className="lens-parse-preview">
          <h5>Parsed Preview</h5>
          <pre>{JSON.stringify(parseResult.parsed, null, 2)}</pre>
          {parseResult.warnings.length > 0 && (
            <div className="lens-warnings">
              {parseResult.warnings.map((w, i) => <p key={i} className="error-text">{w}</p>)}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
