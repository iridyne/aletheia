import { describe, expect, it } from 'vitest'
import type { SettingsRiskConfig } from '../../../api/settings'
import {
  RISK_BOOLS,
  RISK_CORR,
  RISK_DEMO,
  RISK_FAMILY_GATE,
  RISK_FUTURES_DEMO,
  RISK_FUTURES_LIVE,
  RISK_LIVE,
  RISK_MARGIN,
  RISK_POLICY_BOOLS,
  RISK_POLICY_LIVE_FIELDS,
  RISK_POLICY_NUM_FIELDS,
  RISK_POLICY_PATHS,
  RISK_SHARED,
  type RiskPolicyPath,
} from './riskFields'
import { readPolicyPath, writePolicyPath, type RiskPolicyDraft } from '../tabs/RiskTab'

const policy: RiskPolicyDraft = {
  policy_id: 'test-policy',
  revision: 1,
  auto_trip_mode_brake_on_daily_loss: true,
  capital: {
    base_source: 'account_equity',
    base_usdt: 100,
    max_position_notional_usdt: 50,
    max_per_symbol_notional_usdt: 400,
    max_total_exposure_pct: 5,
  },
  loss: { max_single_trade_risk_pct: 0.25, max_daily_loss_pct: 1 },
  positions: { max_open_positions: 3, allow_short: true },
  leverage: { futures_cap: 2, major_cap: 3, mid_cap: 2, alt_cap: 1, account_cap: 1.5 },
  margin: { min_entry_liquidation_buffer_pct: 5, runtime_liquidation_buffer_pct: 3, warning_ratio: 0.7, block_ratio: 0.85 },
  correlation: { max_sector_concentration_pct: 60, max_portfolio_beta: 3, enabled: true },
  exits: { catastrophic_stop_pct: 10, catastrophic_stop_pct_serenity: 15, horizon_hard_backstop_mult: 3, catastrophic_stop_enabled: true },
  eligibility: { family_min_samples: 30, family_min_win_rate_pct: 50 },
}

type LegacyField = { key: keyof SettingsRiskConfig & string }
const legacyFields: readonly (readonly LegacyField[])[] = [
  RISK_LIVE, RISK_DEMO, RISK_SHARED, RISK_BOOLS, RISK_CORR,
  RISK_FAMILY_GATE, RISK_FUTURES_LIVE, RISK_FUTURES_DEMO, RISK_MARGIN,
]

const policyFields: readonly (readonly { key: RiskPolicyPath }[])[] = [
  RISK_POLICY_NUM_FIELDS, RISK_POLICY_BOOLS, RISK_POLICY_LIVE_FIELDS,
]

describe('risk field contracts', () => {
  it('keeps legacy controls and policy controls aligned with generated settings types', () => {
    expect(legacyFields.flat().every(field => typeof field.key === 'string')).toBe(true)

    for (const field of policyFields.flat()) {
      expect(RISK_POLICY_PATHS.has(field.key)).toBe(true)
      expect(readPolicyPath(policy, field.key)).not.toBeUndefined()
    }
    expect(readPolicyPath(policy, 'capital.base_source')).toBe('account_equity')
    expect(readPolicyPath(policy, 'capital.base_usdt')).toBe(100)
  })

  it('rejects unsupported policy paths instead of writing arbitrary keys', () => {
    expect(() => readPolicyPath(policy, 'capital.typo' as RiskPolicyPath)).toThrow('Unsupported risk policy path')
    expect(() => writePolicyPath(policy, 'loss.typo' as RiskPolicyPath, 1)).toThrow('Unsupported risk policy path')
  })
})
