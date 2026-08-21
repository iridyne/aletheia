import { describe, expect, it } from 'vitest'
import { formatViolation } from './riskViolations'

describe('riskViolations', () => {
  describe('block severity', () => {
    it('brake codes', () => {
      expect(formatViolation('live_brake_active').severity).toBe('block')
      expect(formatViolation('demo_brake_active').severity).toBe('block')
    })

    it('invalid input codes', () => {
      expect(formatViolation('invalid_quantity').severity).toBe('block')
      expect(formatViolation('invalid_execution_mode').severity).toBe('block')
      expect(formatViolation('invalid_symbol_format').severity).toBe('block')
      expect(formatViolation('invalid_market_type').severity).toBe('block')
      expect(formatViolation('invalid_direction').severity).toBe('block')
      expect(formatViolation('invalid_order_type').severity).toBe('block')
      expect(formatViolation('invalid_price').severity).toBe('block')
      expect(formatViolation('invalid_leverage').severity).toBe('block')
    })

    it('paper account unavailable codes', () => {
      expect(formatViolation('paper_account_balance_unavailable').severity).toBe('block')
      expect(formatViolation('entry_risk_reservation_unavailable').severity).toBe('block')
      expect(formatViolation('unresolved_entry_exposure_unavailable').severity).toBe('block')
    })

    it('entry plan geometry failures', () => {
      expect(formatViolation('entry_plan_geometry:expected_stop<entry<tp_for_long').severity).toBe('block')
      expect(formatViolation('entry_plan_geometry:expected_tp<entry<stop_for_short').severity).toBe('block')
      expect(formatViolation('incomplete_entry_plan:stop_price+take_profit').severity).toBe('block')
      expect(formatViolation('entry_plan_risk_reward_mismatch').severity).toBe('block')
      expect(formatViolation('risk_reward_outside_envelope').severity).toBe('block')
    })

    it('proposal/opportunity identity mismatch', () => {
      expect(formatViolation('proposal_opportunity_symbol_mismatch').severity).toBe('block')
      expect(formatViolation('proposal_opportunity_market_mismatch').severity).toBe('block')
      expect(formatViolation('proposal_opportunity_direction_mismatch').severity).toBe('block')
      expect(formatViolation('proposal_opportunity_not_found').severity).toBe('block')
      expect(formatViolation('proposal_opportunity_ambiguous').severity).toBe('block')
      expect(formatViolation('proposal_opportunity_required').severity).toBe('block')
      expect(formatViolation('proposal_opportunity_check_error').severity).toBe('block')
    })

    it('existing known block codes', () => {
      expect(formatViolation('kill_switch_active').severity).toBe('block')
      expect(formatViolation('max_daily_loss_exceeded:-5.97<=-1.0').severity).toBe('block')
      expect(formatViolation('account_mode_mismatch:order=live,exchange=demo').severity).toBe('block')
    })
  })

  describe('warn severity', () => {
    it('stale data advisory', () => {
      expect(formatViolation('stale_market_data:age=200').severity).toBe('warn')
      expect(formatViolation('stale_market_data_demo:age=2000').severity).toBe('warn')
    })

    it('explicit advisory codes', () => {
      expect(formatViolation('live_disabled_while_exchange_live').severity).toBe('warn')
      expect(formatViolation('permission_mode_not_live_eligible:Observe').severity).toBe('warn')
    })
  })

  describe('labels', () => {
    it('brake codes', () => {
      expect(formatViolation('live_brake_active').label).toBe('Live trading halted')
      expect(formatViolation('demo_brake_active').label).toBe('Demo trading halted')
    })

    it('entry plan geometry', () => {
      const result = formatViolation('entry_plan_geometry:expected_stop<entry<tp_for_long')
      expect(result.label).toBe('Entry plan geometry invalid')
      expect(result.detail).toBe('expected_stop<entry<tp_for_long')
    })

    it('incomplete entry plan', () => {
      const result = formatViolation('incomplete_entry_plan:stop_price+take_profit')
      expect(result.label).toBe('Entry plan incomplete')
      expect(result.detail).toBe('missing: stop_price, take_profit')
    })
  })
})
