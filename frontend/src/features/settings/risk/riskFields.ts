import type { BoolField, NumField } from '../../../components/ui'
import type { SettingsRiskConfig, SettingsRiskPolicy } from '../../../api/settings'

type LegacyNumField = Omit<NumField, 'key'> & { key: keyof SettingsRiskConfig & string }
type LegacyBoolField = Omit<BoolField, 'key'> & { key: keyof SettingsRiskConfig & string }
type PolicyPath =
  | `capital.${keyof NonNullable<SettingsRiskPolicy['capital']> & string}`
  | `loss.${keyof NonNullable<SettingsRiskPolicy['loss']> & string}`
  | `positions.${keyof NonNullable<SettingsRiskPolicy['positions']> & string}`
  | `leverage.${keyof NonNullable<SettingsRiskPolicy['leverage']> & string}`
  | `margin.${keyof NonNullable<SettingsRiskPolicy['margin']> & string}`
  | `correlation.${keyof NonNullable<SettingsRiskPolicy['correlation']> & string}`
  | `exits.${keyof NonNullable<SettingsRiskPolicy['exits']> & string}`
  | `eligibility.${keyof NonNullable<SettingsRiskPolicy['eligibility']> & string}`
  | 'auto_trip_mode_brake_on_daily_loss'
type PolicyNumField = Omit<NumField, 'key'> & { key: PolicyPath }
type PolicyBoolField = Omit<BoolField, 'key'> & { key: PolicyPath }

export const RISK_LIVE = [
  { key: 'max_daily_loss_pct',            label: 'Max daily loss',          unit: '%',    min: 0.1,  max: 20,     step: 0.1 },
  { key: 'max_single_trade_risk_pct',     label: 'Per-trade cost/risk budget', unit: '%', min: 0.01, max: 10, step: 0.01 },
  { key: 'max_total_live_exposure_pct',   label: 'Max total exposure',      unit: '%',    min: 1,    max: 50,     step: 0.5 },
  { key: 'starting_capital_usdt',         label: 'Starting capital',        unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_live_limited_cap_usdt',     label: 'Live cap',                unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_live_position_notional_usdt', label: 'Position hard ceiling', unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_futures_leverage_live',     label: 'Max leverage',            unit: 'x',    min: 1,    max: 20,     step: 1, isInt: true },
  { key: 'max_open_positions_live',       label: 'Max open positions',      unit: '',     min: 1,    max: 50,     step: 1, isInt: true },
] as const satisfies readonly LegacyNumField[]

export const RISK_DEMO = [
  { key: 'max_demo_position_notional_usdt', label: 'Max position notional', unit: 'USDT', min: 1,    step: 1 },
  { key: 'max_demo_single_trade_risk_pct',  label: 'Per-trade cost/risk budget', unit: '%', min: 0.01, step: 0.01 },
  { key: 'max_futures_leverage_demo',       label: 'Max leverage',          unit: 'x',    min: 1,    max: 125,    step: 1, isInt: true },
  { key: 'max_open_positions_demo',         label: 'Max open positions',    unit: '',     min: 1,    step: 1, isInt: true },
] as const satisfies readonly LegacyNumField[]

export const RISK_SHARED = [
  { key: 'max_per_symbol_notional_usdt', label: 'Max per-symbol notional (cumulative)', unit: 'USDT', min: 0, max: 100000, step: 10 },
] as const satisfies readonly LegacyNumField[]

export const RISK_BOOLS = [
  { key: 'kill_switch_enabled_live', label: 'Kill switch auto-trigger (live)' },
  { key: 'kill_switch_enabled_demo', label: 'Kill switch auto-trigger (demo)' },
  { key: 'allow_short_live', label: 'Allow short selling (live)' },
  { key: 'allow_short_demo', label: 'Allow short selling (demo)' },
  { key: 'correlation_guard_enabled', label: 'Correlation guard (sector + beta)' },
] as const satisfies readonly LegacyBoolField[]

export const RISK_CORR = [
  { key: 'max_sector_concentration_pct', label: 'Max sector concentration', unit: '%', min: 10, max: 100, step: 5 },
  { key: 'max_portfolio_beta', label: 'Max portfolio BTC beta', unit: '', min: 0.5, max: 10, step: 0.5 },
] as const satisfies readonly LegacyNumField[]

export const RISK_FAMILY_GATE = [
  { key: 'live_family_min_samples', label: 'Live family min samples', unit: '', min: 1, max: 500, step: 1, isInt: true },
  { key: 'live_family_min_win_rate_pct', label: 'Live family min win rate', unit: '%', min: 0, max: 100, step: 1 },
] as const satisfies readonly LegacyNumField[]

export const RISK_FUTURES_LIVE = [
  { key: 'max_leverage_major_live', label: 'Major (BTC/ETH)', unit: 'x', min: 1, max: 10, step: 1, isInt: true },
  { key: 'max_leverage_mid_live', label: 'Mid-cap', unit: 'x', min: 1, max: 5, step: 1, isInt: true },
  { key: 'max_leverage_alt_live', label: 'Alt/Small', unit: 'x', min: 1, max: 3, step: 1, isInt: true },
  { key: 'max_account_leverage_live', label: 'Account leverage cap', unit: 'x', min: 1, max: 5, step: 0.1 },
] as const satisfies readonly LegacyNumField[]

export const RISK_FUTURES_DEMO = [
  { key: 'max_leverage_major_demo', label: 'Major (BTC/ETH)', unit: 'x', min: 1, max: 20, step: 1, isInt: true },
  { key: 'max_leverage_mid_demo', label: 'Mid-cap', unit: 'x', min: 1, max: 10, step: 1, isInt: true },
  { key: 'max_leverage_alt_demo', label: 'Alt/Small', unit: 'x', min: 1, max: 5, step: 1, isInt: true },
  { key: 'max_account_leverage_demo', label: 'Account leverage cap', unit: 'x', min: 1, step: 0.1 },
] as const satisfies readonly LegacyNumField[]

export const RISK_MARGIN = [
  { key: 'futures_margin_warning_ratio', label: 'Margin warning at', unit: '', min: 0.3, max: 0.95, step: 0.05 },
  { key: 'futures_margin_force_close_ratio', label: 'Margin block at', unit: '', min: 0.5, max: 0.99, step: 0.05 },
  { key: 'min_liquidation_buffer_pct', label: 'Liquidation buffer min', unit: '%', min: 1, max: 20, step: 0.5 },
] as const satisfies readonly LegacyNumField[]

export const RISK_POLICY_NUM_FIELDS = [
  { key: 'capital.max_position_notional_usdt', label: 'Position notional cap', unit: 'USDT', min: 1, step: 1 },
  { key: 'capital.max_per_symbol_notional_usdt', label: 'Per-symbol cumulative cap', unit: 'USDT', min: 0, step: 10 },
  { key: 'capital.max_total_exposure_pct', label: 'Total exposure cap', unit: '%', min: 0, step: 0.5 },
  { key: 'loss.max_single_trade_risk_pct', label: 'Per-trade cost/risk budget', unit: '%', min: 0.01, step: 0.01 },
  { key: 'loss.max_daily_loss_pct', label: 'Daily loss', unit: '%', min: 0.1, step: 0.1 },
  { key: 'positions.max_open_positions', label: 'Open positions', unit: '', min: 1, step: 1, isInt: true },
  { key: 'leverage.futures_cap', label: 'Futures leverage', unit: 'x', min: 1, step: 1, isInt: true },
  { key: 'leverage.major_cap', label: 'Major leverage', unit: 'x', min: 1, step: 1, isInt: true },
  { key: 'leverage.mid_cap', label: 'Mid-cap leverage', unit: 'x', min: 1, step: 1, isInt: true },
  { key: 'leverage.alt_cap', label: 'Alt leverage', unit: 'x', min: 1, step: 1, isInt: true },
  { key: 'leverage.account_cap', label: 'Account leverage', unit: 'x', min: 1, step: 0.1 },
  { key: 'margin.min_entry_liquidation_buffer_pct', label: 'Entry liquidation buffer', unit: '%', min: 0, step: 0.5 },
  { key: 'margin.runtime_liquidation_buffer_pct', label: 'Runtime liquidation buffer', unit: '%', min: 0, step: 0.5 },
  { key: 'correlation.max_sector_concentration_pct', label: 'Sector concentration', unit: '%', min: 0, step: 1 },
  { key: 'correlation.max_portfolio_beta', label: 'Portfolio BTC beta', unit: '', min: 0, step: 0.1 },
  { key: 'exits.catastrophic_stop_pct', label: 'Catastrophic stop', unit: '%', min: 0, step: 0.5 },
  { key: 'exits.catastrophic_stop_pct_serenity', label: 'Serenity catastrophic stop', unit: '%', min: 0, step: 0.5 },
  { key: 'exits.horizon_hard_backstop_mult', label: 'Horizon hard backstop', unit: 'x', min: 1, step: 0.5 },
] as const satisfies readonly PolicyNumField[]

export const RISK_POLICY_BOOLS = [
  { key: 'positions.allow_short', label: 'Allow short selling' },
  { key: 'correlation.enabled', label: 'Correlation guard' },
  { key: 'exits.catastrophic_stop_enabled', label: 'Catastrophic stop' },
  { key: 'auto_trip_mode_brake_on_daily_loss', label: 'Auto-trip mode brake on daily loss' },
] as const satisfies readonly PolicyBoolField[]

export const RISK_POLICY_LIVE_FIELDS = [
  { key: 'eligibility.family_min_samples', label: 'Family evidence samples', unit: '', min: 1, step: 1, isInt: true },
  { key: 'eligibility.family_min_win_rate_pct', label: 'Family evidence win rate', unit: '%', min: 0, max: 100, step: 1 },
] as const satisfies readonly PolicyNumField[]

/** Every path supported by the policy editor; unknown paths are rejected. */
export type RiskPolicyPath =
  | typeof RISK_POLICY_NUM_FIELDS[number]['key']
  | typeof RISK_POLICY_BOOLS[number]['key']
  | typeof RISK_POLICY_LIVE_FIELDS[number]['key']
  | 'capital.base_source'
  | 'capital.base_usdt'

export const RISK_POLICY_PATHS: ReadonlySet<RiskPolicyPath> = new Set([
  'capital.base_source',
  'capital.base_usdt',
  ...RISK_POLICY_NUM_FIELDS.map(field => field.key),
  ...RISK_POLICY_BOOLS.map(field => field.key),
  ...RISK_POLICY_LIVE_FIELDS.map(field => field.key),
])

export const RISK_LEGACY_POLICY_KEYS = [
  ...RISK_LIVE, ...RISK_DEMO, ...RISK_SHARED, ...RISK_BOOLS, ...RISK_CORR,
  ...RISK_FAMILY_GATE, ...RISK_FUTURES_LIVE, ...RISK_FUTURES_DEMO, ...RISK_MARGIN,
].map(field => field.key)
