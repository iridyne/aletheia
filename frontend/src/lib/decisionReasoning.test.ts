import { describe, expect, it } from 'vitest'
import { parseDecisionRationale, stringList } from './decisionReasoning'

describe('parseDecisionRationale', () => {
  it('splits the persisted "{thesis}\\n---\\n{json}" format', () => {
    const raw = 'Momentum breakout with volume confirmation.\n---\n{"conviction":72,"supporting_signals":["a","b"]}'
    const parsed = parseDecisionRationale(raw)
    expect(parsed.thesis).toBe('Momentum breakout with volume confirmation.')
    expect(parsed.meta).toEqual({ conviction: 72, supporting_signals: ['a', 'b'] })
  })

  it('treats null / empty as fully empty', () => {
    expect(parseDecisionRationale(null)).toEqual({ thesis: '', meta: null })
    expect(parseDecisionRationale(undefined)).toEqual({ thesis: '', meta: null })
    expect(parseDecisionRationale('')).toEqual({ thesis: '', meta: null })
  })

  it('legacy records without a separator return only the readable thesis', () => {
    const parsed = parseDecisionRationale('Older row: pure text, no JSON tail.')
    expect(parsed.thesis).toBe('Older row: pure text, no JSON tail.')
    expect(parsed.meta).toBeNull()
  })

  it('malformed JSON tail keeps thesis, discards meta rather than throwing', () => {
    const parsed = parseDecisionRationale('Reasoning here.\n---\n{not: valid json')
    expect(parsed.thesis).toBe('Reasoning here.')
    expect(parsed.meta).toBeNull()
  })

  it('rejects non-object JSON (array / scalar) — meta must be a record', () => {
    expect(parseDecisionRationale('t\n---\n[1,2,3]').meta).toBeNull()
    expect(parseDecisionRationale('t\n---\n"just a string"').meta).toBeNull()
    expect(parseDecisionRationale('t\n---\n42').meta).toBeNull()
  })
})

describe('stringList', () => {
  it('takes strings, caps at max, drops empties', () => {
    expect(stringList(['a', '', ' b ', 'c'], 5)).toEqual(['a', 'b', 'c'])
    expect(stringList(['a', 'b', 'c', 'd', 'e', 'f'], 3)).toEqual(['a', 'b', 'c'])
  })

  it('returns [] for non-array input', () => {
    expect(stringList(null)).toEqual([])
    expect(stringList('a,b')).toEqual([])
    expect(stringList({ 0: 'a' })).toEqual([])
  })

  it('coerces numeric/boolean scalars but skips nested objects', () => {
    expect(stringList([1, true, { x: 1 }, 'ok'])).toEqual(['1', 'true', 'ok'])
  })
})
