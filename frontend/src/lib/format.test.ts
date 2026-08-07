import { describe, expect, it } from 'vitest'
import {
  fmtCompact,
  fmtCompactSigned,
  fmtDelta,
  fmtNum,
  fmtOpportunityScore,
  fmtPct,
  fmtScore100,
  fmtSigned,
  opportunityScoreLabel,
} from './format'

// Thousands-separated integers depend on the runtime locale (Intl / ICU).
// Under Node's default en-US we expect "1,234.57"; the tests below use a
// regex that accepts either the comma (en) or dot/space grouping so CI on a
// non-en-US locale still passes.
const THOUSANDS_1234_57 = /^1[,.\s]?234\.57$/
const THOUSANDS_NEG_1234_57 = /^-1[,.\s]?234\.57$/

describe('fmtNum', () => {
  describe('|v| ≥ 1 — fixed 2 fraction digits with thousands separator', () => {
    it('formats a small positive integer', () => {
      expect(fmtNum(1)).toBe('1.00')
    })
    it('formats a small negative integer', () => {
      expect(fmtNum(-42)).toBe('-42.00')
    })
    it('rounds to 2 fraction digits (half-to-even via toLocaleString)', () => {
      expect(fmtNum(1.5)).toBe('1.50')
    })
    it('applies a thousands separator', () => {
      expect(fmtNum(1234.567)).toMatch(THOUSANDS_1234_57)
    })
    it('negative thousands still get separator + negative sign', () => {
      expect(fmtNum(-1234.567)).toMatch(THOUSANDS_NEG_1234_57)
    })
    it('rounds a sub-1 value that lifts to ≥1 into the ≥1 case', () => {
      // 0.99999 rounds to 1.00 at 4 sig figs — must not leak into subscript path.
      expect(fmtNum(0.99999)).toBe('1.00')
    })
  })

  describe('exact zero', () => {
    it('renders positive zero as "0.00"', () => {
      expect(fmtNum(0)).toBe('0.00')
    })
    it('renders negative zero as "0.00" (no leaking sign)', () => {
      expect(fmtNum(-0)).toBe('0.00')
    })
  })

  describe('0 < |v| < 1, leading zeros < 3 — 4 significant digits expanded', () => {
    it('0.5 → "0.5000"', () => {
      expect(fmtNum(0.5)).toBe('0.5000')
    })
    it('-0.5 → "-0.5000"', () => {
      expect(fmtNum(-0.5)).toBe('-0.5000')
    })
    it('0.1234 → "0.1234" (no trailing pad since already 4 sig)', () => {
      expect(fmtNum(0.1234)).toBe('0.1234')
    })
    it('0.12345 rounds to "0.1235" at 4 sig', () => {
      expect(fmtNum(0.12345)).toBe('0.1235')
    })
    it('0.0123 → "0.01230" (leading-zero 1, so 5 fraction digits)', () => {
      expect(fmtNum(0.0123)).toBe('0.01230')
    })
    it('0.001234 → "0.001234" (leading-zero 2, sub-threshold, 6 fraction digits)', () => {
      expect(fmtNum(0.001234)).toBe('0.001234')
    })
    it('0.000999999 rounds up: rendered as "0.001000" (still sub-threshold)', () => {
      expect(fmtNum(0.000999999)).toBe('0.001000')
    })
  })

  describe('leading zeros ≥ 3 — subscript notation', () => {
    it('0.0001234 → "0.0₃1234" (first threshold hit)', () => {
      expect(fmtNum(0.0001234)).toBe('0.0₃1234')
    })
    it('0.00003527 → "0.0₄3527"', () => {
      expect(fmtNum(0.00003527)).toBe('0.0₄3527')
    })
    it('PEPE-scale 0.0000000027 → "0.0₈2700" (4 sig, trailing zeros kept)', () => {
      expect(fmtNum(0.0000000027)).toBe('0.0₈2700')
    })
    it('negative PEPE-scale: -0.0000027 → "-0.0₅2700"', () => {
      expect(fmtNum(-0.0000027)).toBe('-0.0₅2700')
    })
    it('extreme 1e-20 uses multi-digit subscript', () => {
      // exp=-20 → leadingZeros=19 → "₁₉"; mantissa "1.000" → digits "1000"
      expect(fmtNum(1e-20)).toBe('0.0₁₉1000')
    })
  })

  describe('non-finite / missing / string inputs', () => {
    it('NaN → dash', () => {
      expect(fmtNum(Number.NaN)).toBe('—')
    })
    it('null → dash', () => {
      expect(fmtNum(null)).toBe('—')
    })
    it('undefined → dash', () => {
      expect(fmtNum(undefined)).toBe('—')
    })
    it('empty string → dash', () => {
      expect(fmtNum('')).toBe('—')
    })
    it('Infinity → dash', () => {
      expect(fmtNum(Number.POSITIVE_INFINITY)).toBe('—')
    })
    it('-Infinity → dash', () => {
      expect(fmtNum(Number.NEGATIVE_INFINITY)).toBe('—')
    })
    it('numeric string parses through', () => {
      expect(fmtNum('42.5')).toBe('42.50')
    })
    it('custom dash placeholder honored', () => {
      expect(fmtNum(null, { dash: 'N/A' })).toBe('N/A')
    })
    it('non-numeric string → dash', () => {
      expect(fmtNum('not a number')).toBe('—')
    })
  })
})

describe('fmtSigned', () => {
  it('positive gets a + sign', () => {
    expect(fmtSigned(1.5)).toBe('+1.50')
  })
  it('zero counts as non-negative (renders +0.00)', () => {
    expect(fmtSigned(0)).toBe('+0.00')
  })
  it('negative renders with the native negative sign, no double negative', () => {
    expect(fmtSigned(-1.5)).toBe('-1.50')
  })
  it('positive subscript value keeps + prefix', () => {
    expect(fmtSigned(0.0000027)).toBe('+0.0₅2700')
  })
  it('negative subscript value keeps - prefix without double sign', () => {
    expect(fmtSigned(-0.0000027)).toBe('-0.0₅2700')
  })
  it('NaN → dash', () => {
    expect(fmtSigned(Number.NaN)).toBe('—')
  })
})

describe('fmtDelta', () => {
  it('positive gets ▲ + + sign', () => {
    expect(fmtDelta(1.5)).toBe('▲+1.50')
  })
  it('negative gets ▼ then native negative', () => {
    expect(fmtDelta(-1.5)).toBe('▼-1.50')
  })
  it('zero has no arrow but still gets +0.00', () => {
    expect(fmtDelta(0)).toBe('+0.00')
  })
  it('subscript-scale negative delta', () => {
    expect(fmtDelta(-0.0000027)).toBe('▼-0.0₅2700')
  })
  it('null → dash', () => {
    expect(fmtDelta(null)).toBe('—')
  })
})

describe('fmtCompact — tile-budget formatter (≤ ~7 chars incl. dot)', () => {
  it('zero → "0.00"', () => {
    expect(fmtCompact(0)).toBe('0.00')
  })
  it('sub-1 with 0 leading zeros: 3 sig figs expanded', () => {
    expect(fmtCompact(0.234)).toBe('0.234')
    expect(fmtCompact(0.9)).toBe('0.900')
  })
  it('sub-1 with 1 leading zero', () => {
    expect(fmtCompact(0.0234)).toBe('0.0234')
    expect(fmtCompact(0.0305)).toBe('0.0305')
  })
  it('sub-1 with 2 leading zeros still expanded (< subscript threshold)', () => {
    expect(fmtCompact(0.00234)).toBe('0.00234')
  })
  it('sub-1 with 3+ leading zeros → subscript-zero notation', () => {
    expect(fmtCompact(0.0000234)).toBe('0.0₄234')
    expect(fmtCompact(0.000000234)).toBe('0.0₆234')
  })
  it('1 ≤ |v| < 10: 4 decimals', () => {
    expect(fmtCompact(1.23456)).toBe('1.2346')
    // Rounding up to the next bucket at bucket boundaries is fine — the
    // resulting "10.0000" is 7 chars, still inside the tile budget.
    expect(fmtCompact(9.99999)).toBe('10.0000')
  })
  it('10 ≤ |v| < 100: 3 decimals', () => {
    expect(fmtCompact(12.3456)).toBe('12.346')
  })
  it('100 ≤ |v| < 1000: 2 decimals', () => {
    expect(fmtCompact(123.456)).toBe('123.46')
  })
  it('≥ 1000: k suffix, 3 sig figs', () => {
    expect(fmtCompact(1234.56)).toBe('1.23k')
    expect(fmtCompact(12345.6)).toBe('12.3k')
    expect(fmtCompact(123456)).toBe('123k')
  })
  it('≥ 1M: M suffix', () => {
    expect(fmtCompact(1_234_567)).toBe('1.23M')
    expect(fmtCompact(12_345_678)).toBe('12.3M')
  })
  it('≥ 1B: B suffix', () => {
    expect(fmtCompact(1_234_567_890)).toBe('1.23B')
  })
  it('negatives keep the minus sign in every branch', () => {
    expect(fmtCompact(-0.234)).toBe('-0.234')
    expect(fmtCompact(-12.3456)).toBe('-12.346')
    expect(fmtCompact(-1234.56)).toBe('-1.23k')
    expect(fmtCompact(-0.0000234)).toBe('-0.0₄234')
  })
  it('null / NaN → dash', () => {
    expect(fmtCompact(null)).toBe('—')
    expect(fmtCompact(Number.NaN)).toBe('—')
  })
})

describe('fmtCompactSigned — fmtCompact with explicit + on non-negative', () => {
  it('positive gets +', () => {
    expect(fmtCompactSigned(1.23)).toBe('+1.2300')
    expect(fmtCompactSigned(1234)).toBe('+1.23k')
  })
  it('negative keeps native minus', () => {
    expect(fmtCompactSigned(-0.0305)).toBe('-0.0305')
    expect(fmtCompactSigned(-12345)).toBe('-12.3k')
  })
  it('zero: no arrow, positive + sign', () => {
    expect(fmtCompactSigned(0)).toBe('+0.00')
  })
  it('null → dash', () => {
    expect(fmtCompactSigned(null)).toBe('—')
  })
})

describe('fmtPct (unchanged — no subscript treatment)', () => {
  it('scales a 0-1 ratio to a percent', () => {
    expect(fmtPct(0.83)).toBe('83%')
  })
  it('leaves an already-scaled percent alone', () => {
    expect(fmtPct(83)).toBe('83%')
  })
  it('honors decimals option', () => {
    expect(fmtPct(0.8321, { decimals: 2 })).toBe('83.21%')
  })
  it('null → dash', () => {
    expect(fmtPct(null)).toBe('—')
  })
})

describe('bounded score semantics', () => {
  it('renders a score without a probability percent sign', () => {
    expect(fmtScore100(49)).toBe('49/100')
    expect(fmtScore100(48.925)).toBe('48.93/100')
  })

  it('distinguishes Universe ranking from observer confidence', () => {
    expect(opportunityScoreLabel('universe_scanner')).toBe('Scanner score')
    expect(opportunityScoreLabel('market_observer')).toBe('Confidence score')
    expect(fmtOpportunityScore('universe_scanner', 49, 'medium')).toBe('Scanner score 49/100')
    expect(fmtOpportunityScore('market_observer', 65, 'medium')).toBe('Confidence score 65/100')
  })

  it('keeps categorical legacy values explicit when no numeric score exists', () => {
    expect(fmtOpportunityScore('universe_scanner', null, 'medium')).toBe('Scanner score medium')
    expect(fmtOpportunityScore('market_observer', null, null)).toBe('Score not available')
  })
})
