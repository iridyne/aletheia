import { SettingsFeatureSection, type SettingsFeatureDescriptor } from '../SettingsFeatureSection'
import {
  RISK_BOOLS, RISK_CORR, RISK_DEMO, RISK_FAMILY_GATE, RISK_FUTURES_DEMO,
  RISK_FUTURES_LIVE, RISK_LIVE, RISK_MARGIN, RISK_SHARED,
} from '../risk/riskFields'

export const ADVANCED_SECTION = {
  id: 'configuration-matrix',
  title: 'Configuration Matrix',
  tab: 'advanced',
  description: 'Read-only coverage map for the sanitized local config. If a key exists in config.yaml but has no dedicated control above, it is still visible here.',
  searchTerms: ['coverage matrix', 'config audit', 'configuration coverage'],
} satisfies SettingsFeatureDescriptor

function formatConfigValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return JSON.stringify(value)
}

function flattenConfig(value: unknown, prefix = ''): Array<{ key: string; value: unknown }> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return prefix ? [{ key: prefix, value }] : []
  const rows: Array<{ key: string; value: unknown }> = []
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) rows.push(...flattenConfig(child, path))
    else rows.push({ key: path, value: child })
  }
  return rows
}

function ConfigCoverageCard({ settings }: { settings: unknown }) {
  const controlled = new Set([
    'agent.permission_mode', 'agent.autonomous_learning', 'agent.bypass_distress_gate',
    'remote_control.enabled',
    'learning.contribution_evaluation_enabled', 'learning.lifecycle_automation_enabled',
    'learning.paper_influence_enabled', 'learning.paper_influence_paths',
    'learning.auto_refreeze', 'learning.evaluation_interval_minutes',
    'learning.paper_active_allocation_pct',
    'runtime.scheduler_enabled', 'runtime.git_enabled', 'runtime.auto_reload',
    'runtime.runtime_continuity_enabled', 'runtime.runtime_continuity_lock_ttl_sec', 'runtime.runtime_continuity_shadow_port_offset',
    'app.auto_open_browser',
    'exchange.mode', 'exchange.enabled_markets.spot', 'exchange.enabled_markets.usdt_futures', 'exchange.paper_execution_mode',
    'live.state',
    'risk.symbol_filter_mode', 'risk.symbol_list',
    ...RISK_LIVE.map(f => `risk.${f.key}`),
    ...RISK_DEMO.map(f => `risk.${f.key}`),
    ...RISK_SHARED.map(f => `risk.${f.key}`),
    ...RISK_BOOLS.map(f => `risk.${f.key}`),
    ...RISK_CORR.map(f => `risk.${f.key}`),
    ...RISK_FAMILY_GATE.map(f => `risk.${f.key}`),
    ...RISK_FUTURES_LIVE.map(f => `risk.${f.key}`),
    ...RISK_FUTURES_DEMO.map(f => `risk.${f.key}`),
    ...RISK_MARGIN.map(f => `risk.${f.key}`),
    'trading.default_stop_loss_pct', 'trading.default_take_profit_pct', 'trading.max_position_hold_hours',
    'trading.slippage_bps', 'trading.execution_fee_pct', 'trading.min_confidence_to_trade', 'trading.auto_adopt_enabled',
    'trading.trail_enabled', 'trading.trail_atr_mult', 'trading.trail_pct', 'trading.breakeven_trigger_pct', 'trading.exchange_tpsl_enabled',
    'trading.default_notional_usdt', 'trading.vol_sizing_enabled', 'trading.vol_target_risk_usdt', 'trading.vol_target_pct', 'trading.vol_kelly_cap_fraction',
    'trading.persona_sizing_enabled', 'trading.active_persona', 'trading.personas',
    'trading.persona_learning_enabled', 'trading.persona_learning_min_samples',
    'trading.persona_progressive_unlock_enabled', 'trading.persona_unlock_step_samples',
    'trading.persona_pyramid_enabled', 'trading.persona_pyramid_add_trigger_pct',
    'trading.persona_auto_advance_enabled',
    'health.enabled', 'health.alert_via_telegram', 'health.alert_via_windows_toast', 'health.require_llm_at_startup',
    'health.self_heal_enabled', 'health.self_heal_code_drift', 'health.self_heal_market_data', 'health.self_heal_port_cleanup',
    'health.max_agent_failure_ratio', 'health.llm_unavailable_alert_after', 'health.market_data_max_staleness_min',
    'health.supervisor_max_restarts', 'health.supervisor_restart_window_sec', 'health.supervisor_backoff_cap_sec',
    'health.self_heal_min_interval_sec', 'health.self_heal_max_per_hour',
    'health.startup_gate_max_retries', 'health.startup_gate_retry_delay_sec',
    'retention.agent_runs_days', 'retention.tool_calls_days', 'retention.run_steps_days',
    'retention.market_snapshots_days', 'retention.portfolio_snapshots_days', 'retention.opportunity_ttl_hours', 'retention.jsonl_max_mb',
    'telegram.enabled', 'telegram.allowed_chat_ids',
    'telegram.notify_trade_fills', 'telegram.notify_daily_summary', 'telegram.daily_summary_hour_cst',
    'telegram.dnd_enabled', 'telegram.dnd_start_hour', 'telegram.dnd_end_hour',
    'update.enabled', 'update.branch',
    'universe.enabled', 'universe.refresh_interval_min', 'universe.max_candidates', 'universe.core_pool_size',
    'universe.min_volume_24h_usdt', 'universe.max_spread_bps', 'universe.min_depth_10bps_usd',
    'universe.explore_ratio', 'universe.blacklist', 'universe.fallback_symbols',
    'universe.market_type', 'universe.family_quota', 'universe.family_quota_by_regime',
    'tunnel.enabled', 'tunnel.tunnel_id', 'tunnel.hostname',
    'market_data.ccxt_exchange', 'market_data.ccxt_fallback', 'market_data.ohlcv_cache_ttl_min', 'market_data.enable_yfinance_macro',
    'market_data.observed_stocks', 'market_data.observed_stock_tokens', 'market_data.observed_prediction_markets',
    // These DO have UI controls — were missing from the set so the coverage matrix
    // falsely labelled them "config.yaml only" (CUI-3, honesty fix):
    'market_data.prediction_enabled',   // PredictionToggleCard
    'market_data.observed_equities',     // EquitiesCard
    'trading.market_aware_exits.enabled', 'trading.market_aware_exits.urgent_exit_score',
    'trading.market_aware_exits.tighten_stop_score', 'trading.market_aware_exits.confidence_collapse_score',
    'trading.market_aware_exits.tighten_atr_mult',  // TradingParamsCard
    'health.auto_resume_enabled', 'health.auto_resume_interval_sec',  // HealthCard (DO-1/CUI-4)
    'health.exchange_probe_interval_sec', 'health.exchange_probe_failure_threshold',
    'health.exchange_auto_resume_interval_sec', 'health.exchange_auto_resume_success_threshold',
    'health.startup_behavior_gate_enabled', 'health.startup_gate_recheck_interval_sec',  // Startup Behavior Gate
    'network.proxy_protocol', 'network.proxy_host', 'network.proxy_port',
    'network.enforce', 'network.proxy_url', 'network.no_proxy',
    'operator.loop_enabled',
    'guard.auto_promote_enabled',
  ])
  const readOnlyPrefixes = [
    'app.', 'runtime.paused', 'runtime.kill_switch_active',
    'update.last_', 'update.current_commit',
    'live.first_live_confirmation_done', 'tunnel.credentials_path', 'tunnel.cloudflared_path', 'tunnel.metrics_port',
  ]

  const rows = flattenConfig(settings)
    .filter(r => !r.key.includes('api_key') && !r.key.includes('oauth_token') && !r.key.includes('oauth_refresh'))
    .sort((a, b) => a.key.localeCompare(b.key))

  return (
    <div className="card mb-0">
      <p className="hint m-0">
        This matrix is intentionally read-only. It prevents hidden config drift; dedicated controls above remain the only editable surface for safety-sensitive settings.
      </p>
      <div style={{ maxHeight: '420px', overflow: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)' }}>
        <table>
          <thead>
            <tr>
              <th>Config key</th>
              <th>Value</th>
              <th>Setting surface</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.key}>
                <td className="mono">{row.key}</td>
                <td className="mono" style={{ maxWidth: '520px', wordBreak: 'break-word' }}>{formatConfigValue(row.value)}</td>
                <td>
                  {controlled.has(row.key)
                    ? <span className="badge badge-green">editable above</span>
                    : readOnlyPrefixes.some(prefix => row.key === prefix || row.key.startsWith(prefix))
                      ? <span className="badge badge-muted">read-only visible here</span>
                      : <span className="badge badge-muted">read-only / config.yaml</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function AdvancedTab({ settings }: { settings: unknown }) {
  return (
    <div>
      <SettingsFeatureSection descriptor={ADVANCED_SECTION}>
        <ConfigCoverageCard settings={settings} />
      </SettingsFeatureSection>
    </div>
  )
}
