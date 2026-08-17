import type { components } from './generated/settings'

/** The exact redacted payload returned by GET /api/settings. */
export type SettingsResponse = components['schemas']['SettingsResponse']

export type DeepPartial<T> = T extends (infer Item)[]
  ? Array<DeepPartial<Item>>
  : T extends object
    ? { [Key in keyof T]?: DeepPartial<T[Key]> }
    : T

/** Components accept a partial value in isolated tests and while loading. */
export type SettingsData = DeepPartial<SettingsResponse>

export type SettingsRiskConfig = components['schemas']['RiskConfig']
export type SettingsRiskPolicy = SettingsRiskConfig['demo']
export type SettingsProvider = components['schemas']['SettingsProviderResponse']
export type SettingsTrading = components['schemas']['TradingConfig']
