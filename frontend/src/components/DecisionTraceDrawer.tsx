import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { DetailDrawer, DrawerSection, DrawerField } from './DetailDrawer'
import { fmtSymbol, fmtTimestamp, humanize } from '../lib/format'

/**
 * Right-slide drawer showing the full lifecycle trace of one opportunity.
 * Used both from the Blotter Order-History table (row click) and from the
 * legacy Overview `DecisionHistoryCard` — extracted so the two callers share
 * one implementation instead of duplicating the query + rendering.
 */
export function DecisionTraceDrawer({ opportunityId, onClose }: {
  opportunityId: string | null
  onClose: () => void
}) {
  const { data } = useQuery({
    queryKey: ['opportunity-trace', opportunityId],
    queryFn: () => api.getOpportunityTrace(opportunityId!),
    enabled: !!opportunityId,
  })
  // AI committee opinions: persisted explainability records for this
  // opportunity. Absence renders as honest "not available" — never invented.
  const { data: decisionReport, isError: committeeQueryFailed } = useQuery({
    queryKey: ['decision-report', opportunityId],
    queryFn: () => api.getDecisionReport(opportunityId!),
    enabled: !!opportunityId,
  })
  const committee = decisionReport?.committee_report
  const title = data?.symbol
    ? `${fmtSymbol(data.symbol, data.market || undefined)}  ·  ${humanize(data.direction || '')}`
    : 'Decision trace'
  return (
    <DetailDrawer open={!!opportunityId} onClose={onClose} title={title}>
      {!data && opportunityId && <p className="text-muted text-sm">Loading trace…</p>}
      {data && data.found === false && (
        <p className="text-muted text-sm">{data.final_reason || 'Opportunity trace not found.'}</p>
      )}
      {data && data.found !== false && (
        <>
          <DrawerSection label="Summary">
            <DrawerField label="Final state" value={data.final_state || '—'} />
            <DrawerField label="Final stage" value={data.final_stage || '—'} />
            <DrawerField label="Final reason" value={data.final_reason || '—'} />
            <DrawerField label="Scanner family" value={data.scanner_family || '—'} />
            <DrawerField label="Emitted at" value={data.created_at ? fmtTimestamp(data.created_at) : '—'} />
          </DrawerSection>
          <DrawerSection label="Committee opinion">
            {committeeQueryFailed && (
              <p className="text-muted text-sm">Not available — committee data could not be read.</p>
            )}
            {!committee && !committeeQueryFailed && (
              <p className="text-muted text-sm">Loading…</p>
            )}
            {committee && committee.status !== 'ok' && (
              <p className="text-muted text-sm">
                Not available{committee.reason ? ` — ${committee.reason}` : ''}
              </p>
            )}
            {committee?.status === 'ok' && committee.evaluations?.map((ev, i) => (
              <div key={i} className="drawer-field" style={{ display: 'block', marginBottom: 12 }}>
                <div className="text-xs text-muted">
                  {ev.created_at ? fmtTimestamp(ev.created_at) : '—'}
                  {ev.model_used ? ` · ${ev.model_used}` : ''}
                  {ev.fallback_used ? ' · fallback model' : ''}
                  {ev.blocked ? ' · blocked by rules' : ''}
                </div>
                <DrawerField
                  label="Direction"
                  value={`${humanize(ev.direction)} · confidence ${(ev.confidence * 100).toFixed(0)}% · ES ${ev.explainability_score.toFixed(2)}`}
                />
                {ev.agent_opinions.map((op, j) => (
                  <div key={j} style={{ marginTop: 6 }}>
                    {op.reasoning && (
                      <div className="text-sm" style={{ whiteSpace: 'pre-wrap' }}>{op.reasoning}</div>
                    )}
                    {op.evidence.length > 0 && (
                      <ul className="text-xs text-muted" style={{ marginTop: 4 }}>
                        {op.evidence.map((e, k) => (
                          <li key={k}>{e.value || e.indicator}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
                {ev.risks && ev.risks.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <div className="text-xs text-muted">Risk warnings</div>
                    <ul className="text-xs" style={{ marginTop: 2 }}>
                      {ev.risks.map((r, k) => (
                        <li key={k}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ))}
          </DrawerSection>
          {data.timeline && data.timeline.length > 0 && (
            <DrawerSection label={`Timeline (${data.timeline.length})`}>
              <ul className="trace-timeline">
                {data.timeline.map((ev, i) => (
                  <li key={i} className="trace-event">
                    <span className="trace-event-ts mono text-xs">{fmtTimestamp(ev.at)}</span>
                    {ev.stage && <span className="trace-event-stage text-xs">{ev.stage}</span>}
                    {(ev.summary || ev.reason) && (
                      <div className="trace-event-summary">{ev.summary || ev.reason}</div>
                    )}
                    {ev.details && Object.keys(ev.details).length > 0 && (
                      <pre className="trace-event-details">{JSON.stringify(ev.details, null, 2)}</pre>
                    )}
                  </li>
                ))}
              </ul>
            </DrawerSection>
          )}
        </>
      )}
    </DetailDrawer>
  )
}

export default DecisionTraceDrawer
