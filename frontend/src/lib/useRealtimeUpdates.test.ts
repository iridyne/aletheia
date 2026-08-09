import { describe, expect, it } from 'vitest'
import { parseRealtimeFrame, queryKeysForChanged } from './useRealtimeUpdates'

describe('realtime frame boundary', () => {
  it('rejects malformed frames and accepts the current backend envelope', () => {
    expect(parseRealtimeFrame('{')).toBeNull()
    expect(parseRealtimeFrame('null')).toBeNull()
    expect(parseRealtimeFrame('{"type":"other","changed":[]}')).toBeNull()
    expect(parseRealtimeFrame('{"type":"update","changed":[1]}')).toBeNull()
    expect(parseRealtimeFrame(JSON.stringify({ type: 'alert', changed: ['infra_events'] })))
      .toEqual({ type: 'alert', changed: ['infra_events'] })
  })

  it('deduplicates repeated categories and overlapping query prefixes', () => {
    expect(queryKeysForChanged(['positions', 'positions', 'fills']))
      .toEqual([['positions'], ['portfolio'], ['equity-curve']])
    expect(queryKeysForChanged(['unknown'])).toEqual([])
  })
})
