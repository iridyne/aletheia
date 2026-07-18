import { describe, expect, it } from 'vitest'
import { fmtDelta, fmtNum, fmtPct, fmtSigned } from './format'

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
