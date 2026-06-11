import type { OperatorRecommendation } from './schemas.js'

export interface RepairDecision {
  id: string
  shouldExecute: boolean
  classification: 'auto_execute' | 'proposal_only'
  title: string
  summary: string
  rationale: string
  recommendationId?: string
  commandName?: string
  parameters?: Record<string, unknown>
}

export class RepairEngine {
  evaluate(recommendations: OperatorRecommendation[], permissionMode = 'ReadOnly'): RepairDecision | null {
    const repairEligibleKinds = new Set([
      'health_attention_needed',
      'gateway_offline',
      'strategy_promotion_proposal_due',
      'live_limited_enablement_proposal_due',
      'agent_prompt_change_proposal_due',
      'agent_schedule_change_proposal_due',
      'risk_boundary_change_proposal_due',
      'command_center_behavior_change_proposal_due',
    ])
    const actionable = recommendations.find(
      item => item.taskType && repairEligibleKinds.has(item.kind),
    )
    const canAutoExecute = permissionMode !== 'ReadOnly'
    const canCreateProposals = permissionMode === 'AllowEditing' || permissionMode === 'Auto'
    if (
      actionable &&
      canAutoExecute &&
      (actionable.kind === 'health_attention_needed' || actionable.kind === 'gateway_offline')
    ) {
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'auto_execute',
        title: 'Diagnostic repair decision',
        summary: actionable.summary,
        rationale: `Create a diagnostic todo so the issue is visible in operator memory and the Command Center.`,
        recommendationId: actionable.id,
        commandName: 'create_diagnostic_todo',
        parameters: {
          title: 'Operator diagnostic',
          content: actionable.summary,
        },
      }
    }
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'strategy_promotion_proposal_due'
    ) {
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Strategy promotion proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed promotion proposal for human review rather than mutating lifecycle state directly.',
        recommendationId: actionable.id,
        commandName: 'propose_strategy_promotion',
        parameters: {
          strategy_id:
            typeof actionable.metadata?.strategy_id === 'string'
              ? actionable.metadata.strategy_id
              : '',
          target_state:
            typeof actionable.metadata?.target_state === 'string'
              ? actionable.metadata.target_state
              : 'paper_passed',
          rationale: actionable.summary,
        },
      }
    }
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'live_limited_enablement_proposal_due'
    ) {
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Live-limited enablement proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed live-limited enablement proposal for human review rather than changing live strategy state directly.',
        recommendationId: actionable.id,
        commandName: 'propose_live_limited_enablement',
        parameters: {
          strategy_id:
            typeof actionable.metadata?.strategy_id === 'string'
              ? actionable.metadata.strategy_id
              : '',
          rationale: actionable.summary,
        },
      }
    }
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'agent_prompt_change_proposal_due'
    ) {
      const target =
        typeof actionable.metadata?.target === 'string'
          ? actionable.metadata.target
          : typeof actionable.metadata?.agent_name === 'string'
            ? actionable.metadata.agent_name
            : ''
      const errorSamples = Array.isArray(actionable.metadata?.error_samples)
        ? actionable.metadata.error_samples.map(item => String(item))
        : []
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Prompt/playbook change proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed prompt/playbook proposal for human review rather than silently changing worker instructions.',
        recommendationId: actionable.id,
        commandName: 'propose_prompt_change',
        parameters: {
          target,
          rationale: actionable.summary,
          content:
            errorSamples.length > 0
              ? `Review prompt/playbook for ${target} after repeated failures: ${errorSamples.join('; ')}.`
              : `Review prompt/playbook for ${target} after repeated failures.`,
        },
      }
    }
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'agent_schedule_change_proposal_due'
    ) {
      const agentName =
        typeof actionable.metadata?.agent_name === 'string'
          ? actionable.metadata.agent_name
          : ''
      const requestedSchedule =
        typeof actionable.metadata?.requested_schedule === 'string'
          ? actionable.metadata.requested_schedule
          : ''
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Schedule change proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed schedule proposal for human review rather than silently changing scheduler cadence.',
        recommendationId: actionable.id,
        commandName: 'propose_schedule_change',
        parameters: {
          agent_name: agentName,
          requested_schedule: requestedSchedule,
          rationale: actionable.summary,
        },
      }
    }
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'risk_boundary_change_proposal_due'
    ) {
      const changes =
        actionable.metadata?.changes &&
        typeof actionable.metadata.changes === 'object' &&
        !Array.isArray(actionable.metadata.changes)
          ? actionable.metadata.changes
          : {}
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Risk boundary change proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed risk-boundary proposal for human review rather than silently changing hard risk limits.',
        recommendationId: actionable.id,
        commandName: 'propose_risk_boundary_change',
        parameters: {
          changes,
          rationale: actionable.summary,
        },
      }
    }
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'command_center_behavior_change_proposal_due'
    ) {
      const targetCommand =
        typeof actionable.metadata?.target_command === 'string'
          ? actionable.metadata.target_command
          : ''
      const requestedBehavior =
        typeof actionable.metadata?.requested_behavior === 'string'
          ? actionable.metadata.requested_behavior
          : actionable.summary
      const sampleInputs = Array.isArray(actionable.metadata?.sample_inputs)
        ? actionable.metadata.sample_inputs.map(item => String(item))
        : []
      const sampleResponses = Array.isArray(actionable.metadata?.sample_responses)
        ? actionable.metadata.sample_responses.map(item => String(item))
        : []
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Command Center behavior proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed Command Center behavior proposal for human review rather than silently changing operator UX behavior.',
        recommendationId: actionable.id,
        commandName: 'propose_command_center_behavior_change',
        parameters: {
          target_command: targetCommand,
          requested_behavior: requestedBehavior,
          rationale: actionable.summary,
          evidence: {
            rejection_count:
              typeof actionable.metadata?.rejection_count === 'number'
                ? actionable.metadata.rejection_count
                : 0,
            sample_inputs: sampleInputs,
            sample_responses: sampleResponses,
            command_ids: Array.isArray(actionable.metadata?.command_ids)
              ? actionable.metadata.command_ids.map(item => String(item))
              : [],
          },
        },
      }
    }
    return null
  }
}
