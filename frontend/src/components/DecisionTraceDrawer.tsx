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
