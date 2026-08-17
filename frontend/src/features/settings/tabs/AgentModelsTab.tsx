import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { GuardAuthorityNotice } from '../../../components/GuardAuthorityNotice'
import {
  CollapsibleSection as Collapsible,
  SettingsRow as Row,
  Switch as Toggle,
} from '../../../components/ui'
import { ModelProvidersSection } from '../model-providers/ModelProvidersSection'
import { useSettingsDraftLifecycle } from '../draftLifecycle'
import { SettingsFeatureSection, type SettingsFeatureDescriptor } from '../SettingsFeatureSection'
import type { SettingsData, SettingsProvider } from '../../../api/settings'
import type { SettingsDirtyProps, SettingsProps, SettingsStatusProps } from '../types'

const PERMISSION_MODES = ['Observe', 'Paper', 'Full']
const PROVIDER_LABELS: Record<string, string> = {
  openai_compatible: 'API',
  databricks: 'Databricks',
  anthropic_native: 'Claude OAuth (Experimental)',
  openai_native: 'OpenAI OAuth (Experimental)',
  codex_cli: 'Codex CLI',
  claude_cli: 'Claude Code CLI',
}

export const PERMISSION_SECTION = {
  id: 'permission-mode', title: 'Permission Mode', tab: 'agent',
  description: 'Controls how much autonomy agents have over trading decisions. Changes are audited.',
  searchTerms: ['autonomy', 'observe', 'paper', 'full'],
} satisfies SettingsFeatureDescriptor

export const LEARNING_SECTION = {
  id: 'learning', title: 'Learning', tab: 'agent',
  description: 'Controls whether agents autonomously generate research, knowledge, and strategy ideas.',
  searchTerms: ['dreaming', 'research', 'pi daemon', 'paper learning', 'experiments', 'contribution', 'lifecycle', 'guard', 'lesson guard', 'auto promote', 'guard candidate', 'controller', 'shadow', 'filter tuning', 'opportunity funnel'],
} satisfies SettingsFeatureDescriptor

export const REGIME_SECTION = {
  id: 'regime-detection', title: 'Regime Detection', tab: 'agent',
  description: "Market regime classifier that adjusts trade direction bias. Identifies trending/ranging/bear conditions to guide the System Operator's direction preference.",
  searchTerms: ['regime', 'bias', 'short enabled', 'bear', 'trend', 'direction'],
} satisfies SettingsFeatureDescriptor

export const SERENITY_SECTION = {
  id: 'serenity-research', title: 'Serenity Research', tab: 'agent',
  description: 'Long-horizon industrial-chain analyst lens. Promoting a theme enqueues its opportunities into the System Operator evaluation queue (the System Operator remains sole trade-decision maker).',
  searchTerms: ['serenity', 'auto promote', 'pi evaluation', 'long horizon', 'industrial chain'],
} satisfies SettingsFeatureDescriptor

export const SCHEDULER_SECTION = {
  id: 'scheduler', title: 'Scheduler', tab: 'agent',
  description: 'Controls background scheduling of all agent jobs.',
  searchTerms: ['jobs', 'cron', 'autostart agents'],
} satisfies SettingsFeatureDescriptor

export const INFERENCE_ADMISSION_SECTION = {
  id: 'inference-admission', title: 'Inference Admission', tab: 'agent',
  description: "Per-provider rate limits, concurrency, and queue admission. Every LLM call routed through a provider is governed by these ceilings before it reaches the model. Independent of the provider's identity/model settings above.",
  searchTerms: ['broker', 'rate limit', 'rpm', 'tpm', 'concurrency', 'queue', 'inference limits', 'admission control', 'timeout'],
} satisfies SettingsFeatureDescriptor

export const LLM_COST_SECTION = {
  id: 'llm-cost-usage', title: 'LLM Cost & Usage', tab: 'agent',
  description: 'Enable default-off budget shadow observation. The read-only usage dashboard (token spend, provider trends, budgets, cooldowns) lives on the Usage page.',
  searchTerms: ['tokenomics', 'spend', 'budget', 'economics', 'cooldown', 'broker', 'inference'],
} satisfies SettingsFeatureDescriptor

export function AgentModelsTab({ status, settings, qc, onDirty }: SettingsStatusProps & SettingsDirtyProps) {
  const agent = settings?.agent as any || {}
  const runtime = settings?.runtime as any || {}
  // #164 fix: previously these mutations had onSuccess only — a rejected save
  // (validation, permission, backend error) failed silently. Toggle appears to
  // work, config never persists. Route every failure through a shared local
  // banner so the operator sees the real reason.
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const onSaveError = (label: string) => (e: any) =>
    setSaveErr(`${label}: ${e?.message || 'save failed'}`)

  const permMut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['status'] }); qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Permission mode'),
  })
  const autoLearnMut = useMutation({
    mutationFn: (enabled: boolean) => api.setAutonomousLearning(enabled),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Autonomous learning'),
  })
  const bypassDistressMut = useMutation({
    mutationFn: (enabled: boolean) => api.setBypassDistressGate(enabled),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Bypass distress gate'),
  })
  const runtimeMut = useMutation({
    mutationFn: (body: any) => api.updateRuntimeConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Runtime config'),
  })
  const operatorMut = useMutation({
    mutationFn: (body: any) => api.updateOperatorConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Operator config'),
  })
  const piDaemonMut = useMutation({
    mutationFn: (body: any) => api.updatePiDaemonConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('System Operator daemon'),
  })
  const serenityMut = useMutation({
    mutationFn: (body: any) => api.updateSerenityConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Serenity config'),
  })
  const controllerMut = useMutation({
    mutationFn: (body: any) => api.updateControllerConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Filter controller config'),
  })
  const learningMut = useMutation({
    mutationFn: (body: any) => api.updateLearningConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Paper Learning config'),
  })

  const perm = String(status.permission_mode || agent.permission_mode || '')
  const autoLearn = !!agent.autonomous_learning
  const bypassDistressGate = !!agent.bypass_distress_gate
  const schedulerEnabled = runtime.scheduler_enabled !== false
  const operatorLoopEnabled = settings?.operator?.loop_enabled !== false
  const piDaemon = (settings?.pi_daemon ?? {}) as any
  const piDaemonEnabled = piDaemon.enabled !== false
  const serenity = (settings?.serenity ?? {}) as any
  const serenityEnabled = !!serenity.enabled
  const serenityAutoPromote = !!serenity.auto_promote
  const serenityMinScore = Number(serenity.min_score_for_pi ?? 72)
  const serenityStopLossPct = Number(serenity.stop_loss_pct ?? 8.0)
  const serenityMaxHoldDays = Number(serenity.max_hold_days ?? 90)
  const controllerCfg = (settings?.controller ?? {}) as any
  const controllerShadowEnabled = controllerCfg.shadow_enabled !== false
  const controllerObserveHours = Number(controllerCfg.observe_interval_hours ?? 6)
  const controllerMinSamples = Number(controllerCfg.min_samples_gate ?? 20)
  const learningCfg = (settings?.learning ?? {}) as any
  const contributionEvaluationEnabled = learningCfg.contribution_evaluation_enabled !== false
  const lifecycleAutomationEnabled = !!learningCfg.lifecycle_automation_enabled
  const paperInfluenceEnabled = !!learningCfg.paper_influence_enabled
  const paperInfluencePaths = Array.isArray(learningCfg.paper_influence_paths) ? learningCfg.paper_influence_paths : []
  const learningPathOptions = Array.isArray(settings?.learning_path_options) ? settings.learning_path_options as Array<{
    path_id: string
    label: string
    supported: boolean
    status: 'supported' | 'consumer_unwired' | 'measurement_unwired' | 'authority_unwired'
  }> : []
  const knownLearningPathIds = new Set(learningPathOptions.map(option => option.path_id))
  const unknownPaperInfluencePaths = paperInfluencePaths.filter(
    (path: string) => !knownLearningPathIds.has(path),
  )
  const autoRefreeze = learningCfg.auto_refreeze !== false
  const evaluationInterval = Number(learningCfg.evaluation_interval_minutes ?? 60)
  const paperActiveAllocation = Number(learningCfg.paper_active_allocation_pct ?? 0.80)
  const guardCfg = (settings?.guard ?? {}) as any
  const legacyGuardAutoPromote = !!guardCfg.auto_promote_enabled

  const guardMut = useMutation({
    mutationFn: (body: any) => api.updateGuardConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Guard promotion'),
  })

  const regimeCfg = (settings?.regime ?? {}) as any
  const regimeMut = useMutation({
    mutationFn: (body: any) => api.updateRegimeConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Regime config'),
  })

  return (
    <div>
      {saveErr && (
        <div className="save-err-banner" role="alert">
          Save failed — {saveErr}
        </div>
      )}
      <SettingsFeatureSection descriptor={PERMISSION_SECTION}>
        <Row label="Permission mode" hint="Observe → Paper → Full. Increasing autonomy at each step.">
          <select value={perm} onChange={e => {
            const next = e.target.value
            if (!confirm(`Change permission mode to "${next}"? This affects agent autonomy and is audited.`)) return
            permMut.mutate(next)
          }} className="w-auto">
            {PERMISSION_MODES.map(m => <option key={m}>{m}</option>)}
          </select>
        </Row>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={LEARNING_SECTION}>
        <Row label="Autonomous learning" hint="When ON, agents run dreaming/research cycles unprompted. When OFF, only human-triggered runs execute.">
          <Toggle checked={autoLearn} onChange={v => autoLearnMut.mutate(v)} disabled={autoLearnMut.isPending} />
        </Row>
        <Row label="Bypass distress gate" hint="When ON, historical win-rate/PnL stats are hidden from the System Operator prompt so poor past performance cannot trigger self-protection. Use during demo/test phases only — disable before evaluating real results.">
          <Toggle checked={bypassDistressGate} onChange={v => bypassDistressMut.mutate(v)} disabled={bypassDistressMut.isPending} />
        </Row>
        <Collapsible title="Paper Learning Experiments">
          <p className="hint mb-2">Measured Paper Learning capability defaults on. A Shadow experiment still has no influence until explicit owner admission, and Learning never affects live trading.</p>
          <Row label="Contribution measurement" hint="Evaluate already-recorded shadow exposures and outcomes. This does not grant any influence.">
            <Toggle checked={contributionEvaluationEnabled} onChange={v => learningMut.mutate({ contribution_evaluation_enabled: v })} disabled={learningMut.isPending} />
          </Row>
          <Row label="Lifecycle automation" hint="Record stage recommendations and, only after owner admission and all safety gates, move one Paper experiment stage at a time. Default ON.">
            <Toggle checked={lifecycleAutomationEnabled} onChange={v => {
              if (v && !confirm('Enable Paper-experiment lifecycle automation? Experiment influence still requires owner admission and every safety gate.')) return
              learningMut.mutate({ lifecycle_automation_enabled: v })
            }} disabled={learningMut.isPending} />
          </Row>
          <Row label="Paper influence" hint="Make the measured Paper path available to an explicitly admitted, fully gated experiment. Default ON; this never enables live trading.">
            <Toggle checked={paperInfluenceEnabled} onChange={v => {
              if (v && !confirm('Enable paper Learning influence? Only explicitly allowlisted paths can use paper treatment.')) return
              learningMut.mutate({ paper_influence_enabled: v })
            }} disabled={learningMut.isPending} />
          </Row>
          <Row label="Allowed paper paths" hint="Only paths with a wired consumer, prospective measurement, and authority adapter can be selected. Unsupported design entries remain visible but disabled.">
            <div style={{ display: 'grid', gap: 6, minWidth: 320 }}>
              {learningPathOptions.map(option => (
                <label key={option.path_id} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="checkbox"
                    checked={paperInfluencePaths.includes(option.path_id)}
                    disabled={learningMut.isPending || !option.supported}
                    onChange={e => {
                      const configuredSupported = paperInfluencePaths.filter(
                        (path: string) => learningPathOptions.some(
                          candidate => candidate.path_id === path && candidate.supported,
                        ),
                      )
                      const next = e.currentTarget.checked
                        ? [...new Set([...configuredSupported, option.path_id])]
                        : configuredSupported.filter((path: string) => path !== option.path_id)
                      learningMut.mutate({ paper_influence_paths: next })
                    }}
                  />
                  <span>{option.label}</span>
                  <code>{option.path_id}</code>
                  <span style={{ color: option.status === 'supported' ? 'var(--green)' : 'var(--muted)' }}>
                    {option.status === 'supported' ? 'Supported' : option.status === 'consumer_unwired' ? 'Consumer unwired' : option.status === 'measurement_unwired' ? 'Measurement unwired' : 'Authority unwired'}
                  </span>
                </label>
              ))}
              {unknownPaperInfluencePaths.map((path: string) => (
                <div key={path} style={{ color: 'var(--yellow)' }}>
                  Unsupported saved path: <code>{path}</code>. Change any supported selection to remove it.
                </div>
              ))}
            </div>
          </Row>
          <Row label="Auto-refreeze" hint="Immediately closes a paper experiment after a negative or invalid measurement verdict. Default ON.">
            <Toggle checked={autoRefreeze} onChange={v => learningMut.mutate({ auto_refreeze: v })} disabled={learningMut.isPending} />
          </Row>
          <Row label="Evaluation interval (minutes)" hint="How often read-only measurement and lifecycle checks run. Default 60; allowed range 15–1440.">
            <input type="number" min={15} max={1440} step={5} defaultValue={evaluationInterval} onBlur={e => {
              const v = Math.round(Number(e.currentTarget.value))
              if (Number.isFinite(v) && v >= 15 && v <= 1440 && v !== evaluationInterval) learningMut.mutate({ evaluation_interval_minutes: v })
            }} disabled={learningMut.isPending} className="w-24" />
          </Row>
          <Row label="Active-stage treatment allocation" hint="Maximum paper treatment share at the final stage. A control holdout is mandatory; hard maximum 90%.">
            <input type="number" min={0.01} max={0.90} step={0.05} defaultValue={paperActiveAllocation} onBlur={e => {
              const v = Number(e.currentTarget.value)
              if (Number.isFinite(v) && v > 0 && v <= 0.90 && v !== paperActiveAllocation) learningMut.mutate({ paper_active_allocation_pct: v })
            }} disabled={learningMut.isPending} className="w-24" />
          </Row>
        </Collapsible>
        <Collapsible title="Trading Guards">
          <GuardAuthorityNotice legacyAutoPromoteEnabled={legacyGuardAutoPromote} />
          <Row
            label="Guards enabled"
            hint="Master evaluation switch. When OFF, guards do not block or subtract score in the paper ranker. Candidate creation and expiry maintenance are separate."
          >
            <Toggle
              checked={guardCfg.enabled !== false}
              onChange={v => guardMut.mutate({ enabled: v })}
              disabled={guardMut.isPending}
            />
          </Row>
          <Row
            label="Enforce in paper ranker"
            hint="When ON, active guards subtract score in the paper trader's ranking pass. When OFF, guards are advisory only (kept for observation but do not affect trade selection)."
          >
            <Toggle
              checked={guardCfg.enforce_in_paper_ranker !== false}
              onChange={v => guardMut.mutate({ enforce_in_paper_ranker: v })}
              disabled={guardMut.isPending}
            />
          </Row>
          <Row
            label="Max score penalty"
            hint="Maximum score points a single guard can subtract in the ranker. Caps runaway penalties from broad-matching guards."
          >
            <input
              type="number"
              min={0}
              max={100}
              step={5}
              value={Number(guardCfg.max_score_penalty ?? 40)}
              onChange={e => {
                const n = Math.max(0, Math.min(100, Number(e.target.value) || 0))
                guardMut.mutate({ max_score_penalty: n })
              }}
              disabled={guardMut.isPending}
              style={{ width: 80 }}
            />
          </Row>
        </Collapsible>
        <Collapsible title="Filter Feedback Controller">
          <p className="hint mb-2">Phase 1 (SHADOW): records whether each opportunity filter has enough causal evidence for a future review. It NEVER changes trading config in this phase.</p>
          <Row
            label="Shadow observe job enabled"
            hint="When ON, the controller records read-only evidence-availability rows. It writes no trading config and does not issue threshold changes until passed/dropped cohorts are recorded."
          >
            <Toggle
              checked={controllerShadowEnabled}
              onChange={v => controllerMut.mutate({ shadow_enabled: v })}
              disabled={controllerMut.isPending}
            />
          </Row>
          <Row
            label="Observe interval (hours)"
            hint="How often the shadow observe job runs. Verdicts use multi-hour forward-return horizons, so frequent runs add no signal. Default 6."
          >
            <input
              type="number"
              min={1}
              max={168}
              step={1}
              defaultValue={controllerObserveHours}
              onBlur={e => {
                const v = parseInt(e.currentTarget.value, 10)
                if (!Number.isFinite(v) || v < 1 || v > 168 || v === controllerObserveHours) return
                controllerMut.mutate({ observe_interval_hours: v })
              }}
              className="w-24"
              disabled={controllerMut.isPending}
            />
          </Row>
          <Row
            label="Min samples gate"
            hint="Reserved for future filter-specific evidence. Aggregate family samples never produce a filter threshold recommendation. Default 20."
          >
            <input
              type="number"
              min={5}
              max={500}
              step={1}
              defaultValue={controllerMinSamples}
              onBlur={e => {
                const v = parseInt(e.currentTarget.value, 10)
                if (!Number.isFinite(v) || v < 5 || v > 500 || v === controllerMinSamples) return
                controllerMut.mutate({ min_samples_gate: v })
              }}
              className="w-24"
              disabled={controllerMut.isPending}
            />
          </Row>
        </Collapsible>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={REGIME_SECTION}>
        <Row label="Regime detection enabled" hint="When ON, regime detector classifies market conditions and injects bias into System Operator context.">
          <Toggle checked={regimeCfg.enabled !== false} onChange={v => regimeMut.mutate({ enabled: v })} disabled={regimeMut.isPending} />
        </Row>
        <Row label="Short trades enabled" hint="Allow short direction trades when regime is bearish. Disable to restrict to long-only.">
          <Toggle checked={regimeCfg.short_enabled !== false} onChange={v => regimeMut.mutate({ short_enabled: v })} disabled={regimeMut.isPending} />
        </Row>
        <Row label="Bias threshold" hint="Minimum score change required to flip regime bias (0.0–1.0). Higher = more stable, slower to react.">
          <input
            type="number" step="0.05" min="0" max="1"
            value={Number(regimeCfg.bias_threshold ?? 0.30)}
            onChange={e => regimeMut.mutate({ bias_threshold: Number(e.target.value) })}
            className="input-sm"
          />
        </Row>
        <Row label="Min hold periods" hint="Minimum bars the regime must hold before a bias flip is accepted. Prevents whipsawing (1–20).">
          <input
            type="number" step="1" min="1" max="20"
            value={Number(regimeCfg.min_hold_periods ?? 3)}
            onChange={e => regimeMut.mutate({ min_hold_periods: Number(e.target.value) })}
            className="input-sm"
          />
        </Row>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={SERENITY_SECTION}>
        <Row
          label="Serenity Lens enabled"
          hint="Master switch. When OFF, the scheduler skips all Serenity research runs (evidence collection, theme clustering, opportunity generation). Existing data is preserved."
        >
          <Toggle
            checked={serenityEnabled}
            onChange={v => serenityMut.mutate({ enabled: v })}
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Auto-promote candidate themes"
          hint="When ON, research_worker auto-promotes themes that produce a 'candidate_position' bucket opportunity (score ≥ min below, 0 fail gates). When OFF, promotion is manual-only."
        >
          <Toggle
            checked={serenityAutoPromote}
            onChange={v => {
              if (v && !confirm('Enable auto-promote? Themes that meet the threshold will be queued to the System Operator without human review.')) return
              serenityMut.mutate({ auto_promote: v })
            }}
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Min score for System Operator"
          hint="Minimum aggregate research score (0-100) required before an opportunity is forwarded to the System Operator. Default 72."
        >
          <input
            type="number"
            min={0}
            max={100}
            step={1}
            defaultValue={serenityMinScore}
            onBlur={e => {
              const v = parseInt(e.currentTarget.value, 10)
              if (!Number.isFinite(v) || v < 0 || v > 100 || v === serenityMinScore) return
              serenityMut.mutate({ min_score_for_pi: v })
            }}
            className="w-24"
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Stop-loss % (serenity school)"
          hint="Wider stop applied to serenity-school positions (long-horizon thesis). Default 8.0%. Short-term positions use the global Trading default."
        >
          <input
            type="number"
            min={0.1}
            max={50}
            step={0.1}
            defaultValue={serenityStopLossPct}
            onBlur={e => {
              const v = parseFloat(e.currentTarget.value)
              if (!Number.isFinite(v) || v < 0.1 || v > 50 || v === serenityStopLossPct) return
              serenityMut.mutate({ stop_loss_pct: v })
            }}
            className="w-24"
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Max hold days (serenity school)"
          hint="Maximum holding period for serenity-school positions before time-based exit. Default 90 days."
        >
          <input
            type="number"
            min={1}
            max={365}
            step={1}
            defaultValue={serenityMaxHoldDays}
            onBlur={e => {
              const v = parseInt(e.currentTarget.value, 10)
              if (!Number.isFinite(v) || v < 1 || v > 365 || v === serenityMaxHoldDays) return
              serenityMut.mutate({ max_hold_days: v })
            }}
            className="w-24"
            disabled={serenityMut.isPending}
          />
        </Row>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={SCHEDULER_SECTION}>
        <Row
          label="Scheduler enabled"
          hint="When disabled, no agent jobs fire automatically. The gateway stays up; runs can still be triggered manually."
        >
          <Toggle
            checked={schedulerEnabled}
            onChange={v => {
              if (!v && !confirm('Disable the scheduler? All agent jobs will stop firing automatically.')) return
              runtimeMut.mutate({ scheduler_enabled: v })
            }}
            disabled={runtimeMut.isPending}
          />
        </Row>
        <Row
          label="Operator loop"
          hint="When enabled, the System Operator runs system-level assessments every 4h and takes Level 1 actions automatically."
        >
          <Toggle
            checked={operatorLoopEnabled}
            onChange={v => {
              if (!v && !confirm('Disable the operator loop? Automated system governance will stop.')) return
              operatorMut.mutate({ loop_enabled: v })
            }}
            disabled={operatorMut.isPending}
          />
        </Row>
        <Row
          label="System Operator daemon (event-driven)"
          hint="When ON, System Operator wakes instantly on signal arrival instead of polling every 15 min. Disable to revert to legacy polling."
        >
          <Toggle
            checked={piDaemonEnabled}
            onChange={v => {
              if (!v && !confirm('Disable the System Operator daemon? The agent will revert to 15-minute polling intervals.')) return
              piDaemonMut.mutate({ enabled: v })
            }}
            disabled={piDaemonMut.isPending}
          />
        </Row>
        {piDaemonEnabled && (
          <>
            <Row label="Debounce (s)" hint="How long to wait after a signal before evaluating (collects burst signals). Range 0–60.">
              <input
                type="number" min={0} max={60} step={1}
                defaultValue={piDaemon.debounce_sec ?? 8}
                className="w-20"
                onBlur={e => {
                  const v = parseFloat(e.target.value)
                  if (!isNaN(v)) piDaemonMut.mutate({ debounce_sec: v })
                }}
              />
            </Row>
            <Row label="Cooldown (s)" hint="Minimum gap between two evaluations. Range 0–600.">
              <input
                type="number" min={0} max={600} step={5}
                defaultValue={piDaemon.cooldown_sec ?? 60}
                className="w-20"
                onBlur={e => {
                  const v = parseFloat(e.target.value)
                  if (!isNaN(v)) piDaemonMut.mutate({ cooldown_sec: v })
                }}
              />
            </Row>
            <Row label="Heartbeat (s)" hint="Fallback polling interval when no signals arrive. Range 60–3600.">
              <input
                type="number" min={60} max={3600} step={60}
                defaultValue={piDaemon.heartbeat_sec ?? 300}
                className="w-20"
                onBlur={e => {
                  const v = parseFloat(e.target.value)
                  if (!isNaN(v)) piDaemonMut.mutate({ heartbeat_sec: v })
                }}
              />
            </Row>
          </>
        )}
      </SettingsFeatureSection>

      <ModelProvidersSection qc={qc} onDirty={onDirty} />

      <SettingsFeatureSection descriptor={INFERENCE_ADMISSION_SECTION}>
        <InferenceAdmissionCard qc={qc} onDirty={onDirty} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={LLM_COST_SECTION}>
        <EconomicsSnapshotCard economics={settings?.economics} qc={qc} />
      </SettingsFeatureSection>
    </div>
  )
}


// ─────────────────────────────────────────────────────────────────────────────
function EconomicsSnapshotCard({ economics, qc }: { economics?: SettingsData['economics']; qc: SettingsProps['qc'] }) {
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const economicsEnabled = economics?.enabled === true
  const shadowEnabled = economics?.budgets_shadow_enabled === true
  const guardrailDetectionEnabled = economics?.prompt_guardrail_detection_enabled === true
  const economicsMut = useMutation({
    mutationFn: (body: { enabled?: boolean; budgets_shadow_enabled?: boolean; prompt_guardrail_detection_enabled?: boolean }) => api.updateEconomicsConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: (error: any) => setSaveErr(`Economics controls: ${error?.message || 'save failed'}`),
  })
  const { data: status } = useQuery({
    queryKey: ['economics-status'],
    queryFn: api.getEconomicsStatus,
    refetchInterval: 30_000,
  })

  return (
    <div>
      {saveErr && <div className="settings-save-msg err">{saveErr}</div>}
      <Row
        label="Enable economics harness"
        hint="Keeps LLM budget controls dormant until explicitly enabled. This does not block or reroute any model call."
      >
        <Toggle
          checked={economicsEnabled}
          onChange={enabled => economicsMut.mutate({ enabled })}
          disabled={economicsMut.isPending}
        />
      </Row>
      <Row
        label="Budget shadow observation"
        hint="Records calls that would exceed declared budgets. Calls still proceed; collect this evidence for two weeks before considering enforcement."
      >
        <Toggle
          checked={shadowEnabled}
          onChange={budgets_shadow_enabled => economicsMut.mutate({ budgets_shadow_enabled })}
          disabled={!economicsEnabled || economicsMut.isPending}
        />
      </Row>
      <Row
        label="Prompt secret-shape detection (observe only)"
        hint="Records only stable rule IDs and counts; it never stores prompt text or blocks a model call."
      >
        <Toggle
          checked={guardrailDetectionEnabled}
          onChange={prompt_guardrail_detection_enabled => economicsMut.mutate({ prompt_guardrail_detection_enabled })}
          disabled={!economicsEnabled || economicsMut.isPending}
        />
      </Row>
      {status && (status.tracing_enabled === false || status.audit_enabled === false) && (
        <p className="hint" style={{ marginBottom: '0.75rem' }}>
          {status.tracing_enabled === false && 'Cost tracing disabled (ALETHEIA_COST_TRACING=0). '}
          {status.audit_enabled === false && 'Viability audit disabled (ALETHEIA_VIABILITY_AUDIT=0).'}
        </p>
      )}
      <p className="hint">
        Read-only spend, token, budget, and cooldown views live on the{' '}
        <Link to="/usage">Usage page</Link>.
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Inference Admission — sibling Section to AI Model Providers.
// The broker's rate limit / concurrency / queue policy is a control-plane
// concern separate from provider identity. Rendered as its own Section so
// the semantic decoupling shows up in the operator's UI, not just the code.
// ─────────────────────────────────────────────────────────────────────────────

function InferenceAdmissionCard({ qc, onDirty }: { qc: SettingsProps['qc']; onDirty?: SettingsDirtyProps['onDirty'] }) {
  const { data } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: broker } = useQuery({
    queryKey: ['model-broker'],
    queryFn: api.getInferenceBroker,
    refetchInterval: 10_000,
  })

  if (!data) return <p className="hint">Loading provider list…</p>

  const providers = Object.entries((data as any).providers ?? {}) as Array<[string, any]>
  const enabled = providers.filter(([, cfg]) => cfg?.enabled === true)

  if (enabled.length === 0) {
    return (
      <p className="hint">
        No providers enabled. Enable a provider under "AI Model Providers" above
        to configure its inference admission.
      </p>
    )
  }

  const running = broker?.running ?? false

  return (
    <div className="flex flex-col gap-3">
      <div className="hint mono" style={{ fontSize: '0.72rem' }}>
        Broker: {running ? 'running' : 'not yet started'}
        {broker && ` · ${Object.keys(broker.providers ?? {}).length} lane(s) tracked`}
        · 0 RPM / 0 TPM means unlimited
      </div>
      {enabled.map(([name, cfg]) => {
        const metrics = broker?.providers?.[name]?.metrics
        const label = PROVIDER_LABELS[name] || name
        const compactModel = cfg?.model && cfg.model !== 'default' ? ` · model: ${cfg.model}` : ''
        return (
          <div
            key={name}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 'var(--r)',
              padding: '0.7rem 0.9rem',
              background: 'var(--surface)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                marginBottom: '0.5rem',
              }}
            >
              <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{label}</span>
              <span className="hint mono" style={{ fontSize: '0.72rem', color: 'var(--muted)' }}>
                {name}
                {compactModel}
              </span>
              {metrics && (
                <span
                  className="hint mono"
                  style={{ marginLeft: 'auto', fontSize: '0.72rem' }}
                >
                  active {metrics.active ?? 0} · queued {metrics.queued ?? 0} · wait avg{' '}
                  {Math.round((metrics.queue_wait_avg_ms as number) ?? 0)}ms
                </span>
              )}
            </div>
            <InferenceLimitsForm name={name} config={cfg} metrics={metrics} qc={qc} onDirty={onDirty} />
          </div>
        )
      })}
    </div>
  )
}

function InferenceLimitsForm({ name, config, metrics, qc, onDirty }: {
  name: string; config: Partial<SettingsProvider> & Record<string, unknown>; metrics?: Record<string, number>; qc: SettingsProps['qc']; onDirty?: SettingsDirtyProps['onDirty']
}) {
  const [maxConcurrency, setMaxConcurrency] = useState(config?.inference_max_concurrency ?? 2)
  const [rpm, setRpm] = useState(config?.inference_requests_per_minute ?? 60)
  const [tpm, setTpm] = useState(config?.inference_tpm ?? 120000)
  const [queueCapacity, setQueueCapacity] = useState(config?.inference_queue_capacity ?? 64)
  const [reservedSlots, setReservedSlots] = useState(config?.inference_reserved_high_priority_slots ?? 8)
  const [timeoutSec, setTimeoutSec] = useState(config?.inference_timeout_sec ?? 300)
  const serverValues = {
    maxConcurrency: config?.inference_max_concurrency ?? 2,
    rpm: config?.inference_requests_per_minute ?? 60,
    tpm: config?.inference_tpm ?? 120000,
    queueCapacity: config?.inference_queue_capacity ?? 64,
    reservedSlots: config?.inference_reserved_high_priority_slots ?? 8,
    timeoutSec: config?.inference_timeout_sec ?? 300,
  }
  const dirty = JSON.stringify({ maxConcurrency, rpm, tpm, queueCapacity, reservedSlots, timeoutSec }) !== JSON.stringify(serverValues)
  useSettingsDraftLifecycle({
    id: `inference-limits-${name}`,
    dirty,
    onDirty,
    serverKey: JSON.stringify(serverValues),
    adoptServer: () => {
      setMaxConcurrency(serverValues.maxConcurrency)
      setRpm(serverValues.rpm)
      setTpm(serverValues.tpm)
      setQueueCapacity(serverValues.queueCapacity)
      setReservedSlots(serverValues.reservedSlots)
      setTimeoutSec(serverValues.timeoutSec)
    },
  })
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        inference_max_concurrency: Number(maxConcurrency),
        inference_requests_per_minute: Number(rpm),
        inference_tpm: Number(tpm),
        inference_queue_capacity: Number(queueCapacity),
        inference_reserved_high_priority_slots: Number(reservedSlots),
        inference_timeout_sec: Number(timeoutSec),
      }
      return config?.kind === 'api'
        ? api.updateModelProfile(name, payload)
        : api.updateModelIntegration(name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model'] })
      qc.invalidateQueries({ queryKey: ['model-broker'] })
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  return (
    <div style={{ marginTop: '0.75rem' }}>
      {metrics && (
        <div className="hint mono" style={{ marginBottom: '0.5rem', fontSize: '0.72rem' }}>
          active {metrics.active ?? 0} · queued {metrics.queued ?? 0} · wait avg {Math.round(metrics.queue_wait_avg_ms ?? 0)}ms
        </div>
      )}
      <div className="form-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <label>Max concurrency<input type="number" min={1} max={16} value={maxConcurrency} onChange={e => setMaxConcurrency(Number(e.target.value))} /></label>
        <label>Requests/min<input type="number" min={0} max={10000} value={rpm} onChange={e => setRpm(Number(e.target.value))} /></label>
        <label>Tokens/min<input type="number" min={0} max={100000000} value={tpm} onChange={e => setTpm(Number(e.target.value))} /></label>
        <label>Queue capacity<input type="number" min={1} max={1000} value={queueCapacity} onChange={e => setQueueCapacity(Number(e.target.value))} /></label>
        <label>Reserved priority slots<input type="number" min={0} max={queueCapacity} value={reservedSlots} onChange={e => setReservedSlots(Number(e.target.value))} /></label>
        <label>Total timeout (sec)<input type="number" min={1} max={1800} value={timeoutSec} onChange={e => setTimeoutSec(Number(e.target.value))} /></label>
      </div>
      <div className="flex items-center gap-2 mt-2">
        <button className="secondary" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save inference limits'}
        </button>
        <span className="hint">0 RPM/TPM means unlimited. Priority slots are reserved for interactive/trading work.</span>
      </div>
      {save.isSuccess && <div className="settings-save-msg ok">Inference limits saved.</div>}
      {save.isError && <div className="settings-save-msg err">{(save.error as any)?.message || 'Save failed'}</div>}
    </div>
  )
}
