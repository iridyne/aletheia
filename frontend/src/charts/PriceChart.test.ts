import { expect, it } from 'vitest'
import { marketDataMetaState } from './PriceChart'

it('advances retained metadata age and marks a failed poll stale', () => {
  expect(marketDataMetaState({ ageMs: 5_000, stale: false, receivedAt: 1_000 }, 22_000)).toEqual({
    ageMs: 26_000,
    stale: true,
  })
})
