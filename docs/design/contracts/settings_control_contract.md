# Settings Control Persistence Contract

This contract covers the frontend control boundary for Settings and the small
number of non-Settings controls that reuse the same `Switch` primitive.

## Contract

`Switch` requires `persistence="immediate"` or `persistence="staged"` at every
call site. The value is part of the component API; it is never inferred from
the callback or the surrounding component.

An immediate control sends its mutation from the change handler. Its caller
owns the pending and error state and must leave the last confirmed value
visible until the mutation succeeds. A staged control changes only a local
draft. It renders as a native checkbox with a distinct staged-control style,
reports dirty state through the Settings draft lifecycle, and is applied by the
section's existing Save action. Leaving a tab with a dirty draft uses the
Settings shell's explicit discard confirmation; a failed Save leaves the draft
dirty and the error visible.

`SwitchGrid` requires the same persistence value and passes it to every switch
it expands. It is currently used only by staged Settings editors. Its fields
and Save boundaries are included in the inventory below.

The backend mutation routes, payloads, persisted values, and schemas are
unchanged by this contract. Risk, execution, and paper-trading controls remain
staged unless a separately reviewed backend contract changes their persistence
semantics.

## Checked-in inventory

The table maps all 65 static `Switch`/`Toggle` JSX sites in `frontend/src`.
`SwitchGrid` is one shared primitive site and expands the listed field arrays at
runtime; each grid uses the staged mode and the named Save boundary.

| Source site | Control(s) | Persistence | Mutation or Save/discard boundary |
| --- | --- | --- | --- |
| `components/ui/SettingsSection.tsx` `SwitchGrid` | Each `BoolField` supplied by a grid | Explicit caller mode; all current callers are staged | Caller card Save; Settings tab discard confirmation |
| `features/settings/execution/PaperTradingSettings.tsx` `TradingParamsCard` | Auto-adopt external demo positions | staged | `updateTradingConfig`; `Save` |
| same | Trailing stop + breakeven | staged | `updateTradingConfig`; `Save` |
| same | Volatility-target sizing | staged | `updateTradingConfig`; `Save` |
| same | Exchange-native TPSL bracket | staged | `updateTradingConfig`; `Save` |
| same | Persona-based sizing | staged | `updateTradingConfig`; `Save` |
| same | Persona learning | staged | `updateTradingConfig`; `Save` |
| same | Progressive unlock | staged | `updateTradingConfig`; `Save` |
| same | Auto-advance rollout | staged | `updateTradingConfig`; `Save` |
| same | Pyramid add-on policy helper | staged | `updateTradingConfig`; `Save` |
| same | Market-aware review | staged | `updateTradingConfig`; `Save` |
| `features/settings/model-providers/ModelProvidersSection.tsx` `ApiProfileEditor` | Enable after verification | staged | `createModelProfile`/`updateModelProfile`; `Verify & Save` or `Cancel` |
| same `ApiProfiles` | Enable API profile | immediate | `updateModelProfile`; pending/error state in provider panel |
| same `IntegrationCard` | Enable CLI integration | immediate | `updateModelIntegration`; pending/error state in provider card |
| `features/settings/tabs/AgentModelsTab.tsx` Learning | Autonomous learning | immediate | `setAutonomousLearning` |
| same | Bypass distress gate | immediate | `setBypassDistressGate` |
| same | Contribution measurement | immediate | `updateLearningConfig` |
| same | Lifecycle automation | immediate | `updateLearningConfig` |
| same | Paper influence | immediate | `updateLearningConfig` |
| same | Auto-refreeze | immediate | `updateLearningConfig` |
| same | Guards enabled | immediate | `updateGuardConfig` |
| same | Enforce in paper ranker | immediate | `updateGuardConfig` |
| same | Shadow observe job enabled | immediate | `updateControllerConfig` |
| same | Regime detection enabled | immediate | `updateRegimeConfig` |
| same | Short trades enabled | immediate | `updateRegimeConfig` |
| same | Serenity Lens enabled | immediate | `updateSerenityConfig` |
| same | Auto-promote candidate themes | immediate | `updateSerenityConfig` |
| same | Scheduler enabled | immediate | `updateRuntimeConfig` |
| same | Operator loop | immediate | `updateOperatorConfig` |
| same | System Operator daemon | immediate | `updatePiDaemonConfig` |
| same `EconomicsSnapshotCard` | Enable economics harness | immediate | `updateEconomicsConfig` |
| same | Budget shadow observation | immediate | `updateEconomicsConfig` |
| same | Prompt secret-shape detection | immediate | `updateEconomicsConfig` |
| `features/settings/tabs/ExecutionTab.tsx` `ExchangeMarketsRow` | Spot market | staged | `setExchangeMarkets`; `Save exchange config` |
| same | USDT Futures market | staged | `setExchangeMarkets`; `Save exchange config` |
| same | Exchange/data divergence confirmation | staged | `setExchangeMarkets`; `Save exchange config` |
| `features/settings/tabs/HealthNotificationsTab.tsx` `WebhookCard` | Webhook delivery | staged | `updateWebhookConfig`; `Save` |
| same | Alert events | staged | `updateWebhookConfig`; `Save` |
| same | Trade fill events | staged | `updateWebhookConfig`; `Save` |
| same | Daily summary event | staged | `updateWebhookConfig`; `Save` |
| `features/settings/tabs/RiskTab.tsx` `UniverseConfigCard` | Universe scanner enabled | staged | `updateUniverseConfig`; `Save` |
| same | Snapshot TTL enabled | staged | `updateUniverseConfig`; `Save` |
| `features/settings/tabs/RuntimeSystemTab.tsx` `AppRows` | Remote Control | immediate | `updateRemoteControl` |
| same | Remote Control token requirement | immediate | `updateRemoteControl` |
| same | Auto-open browser | immediate | `updateAppConfig` |
| same | Git versioning of data | immediate | `updateRuntimeConfig` |
| same | Auto-reload Python changes | immediate | `updateRuntimeConfig` |
| same | Runtime continuity | immediate | `updateRuntimeConfig` |
| same `UpdateCard` | Auto-update enabled | staged | `updateUpdateConfig`; `Save config` |
| same `NetworkCard` | Enforce proxy | staged | `updateNetworkConfig`; `Save` |
| `features/settings/tabs/SignalsTab.tsx` `MarketDataCard` | Exchange/data divergence confirmation | staged | `updateMarketDataConfig`; `Save market data config` |
| same | Yahoo Finance macro | staged | `updateMarketDataConfig`; `Save market data config` |
| same | Long/Short ratio | staged | `updateMarketDataConfig`; `Save market data config` |
| same `MacroSignalsCard` | Fear & Greed Index | staged | `updateMarketDataConfig`; `Save macro signals config` |
| same | CoinGecko top markets | staged | `updateMarketDataConfig`; `Save macro signals config` |
| same | Hyperliquid cross-venue | staged | `updateMarketDataConfig`; `Save macro signals config` |
| same | DefiLlama on-chain | staged | `updateMarketDataConfig`; `Save macro signals config` |
| same `PredictionToggleCard` | Prediction observer | immediate | `togglePrediction`; success/error message |
| same `NewsFeedsCard` | News ingestion | staged | `updateNewsConfig`; `Save news config` |
| same | Individual RSS feed enabled | staged | `updateNewsConfig`; `Save news config` |
| `features/settings/telegram/TelegramSection.tsx` | Telegram delivery | staged | `updateTelegramConfig`; `Save` |
| same | Trade fill notifications | staged | `updateTelegramConfig`; `Save` |
| same | Daily summary event | staged | `updateTelegramConfig`; `Save` |
| same | Do-not-disturb | staged | `updateTelegramConfig`; `Save` |
| `pages/SerenityLens.tsx` `OverviewTab` | Save preferences to profile | staged | `createSerenityRun` when `Run Research` is pressed |

### `SwitchGrid` field arrays

These are the dynamic controls expanded by the shared site and are all staged:

- Historical data: Enabled, Nightly backfill, Active Universe catch-up → `Save Historical Data`.
- Health monitoring: Health monitoring, Telegram alerts, Windows toast alerts, Require LLM at startup, Startup behavior gate, Self-heal enabled, Restart on code drift, Restart on stale market data, Clean orphan port holders, Auto-resume after LLM gate → `Save`.
- Event triggers: Enabled, Watch universe → `Save Event Triggers`.
- Risk policy: Allow short selling, Correlation guard, Catastrophic stop, Auto-trip mode brake on daily loss → `Save demo/live policy`.
- Legacy risk limits: Kill switch auto-trigger (live), Kill switch auto-trigger (demo), Allow short selling (live), Allow short selling (demo), Correlation guard → `Save risk limits`.

The source-level frontend test keeps the 65-site count and rejects any site
that omits the explicit `persistence` prop. The shared primitive test also
asserts that staged controls do not render the immediate switch track.
