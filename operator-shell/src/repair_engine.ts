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
      'agent_prompt_change_proposal_due',
      'agent_schedule_change_proposal_due',
      'risk_boundary_change_proposal_due',
      'command_center_behavior_change_proposal_due',
      'extension_enablement_proposal_due',
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
          risk_mode: actionable.metadata?.risk_mode,
          policy_id: actionable.metadata?.policy_id,
          policy_revision: actionable.metadata?.policy_revision,
          policy_hash: actionable.metadata?.policy_hash,
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
    if (
      actionable &&
      canCreateProposals &&
      actionable.kind === 'extension_enablement_proposal_due'
    ) {
      const extensionId =
        typeof actionable.metadata?.extension_id === 'string'
          ? actionable.metadata.extension_id
          : ''
      return {
        id: `repair:${actionable.id}`,
        shouldExecute: true,
        classification: 'proposal_only',
        title: 'Extension enablement proposal decision',
        summary: actionable.summary,
        rationale:
          'Create a typed extension enablement proposal for human review rather than enabling the extension directly.',
        recommendationId: actionable.id,
        commandName: 'propose_extension_enablement',
        parameters: {
          extension_id: extensionId,
          rationale: actionable.summary,
        },
      }
    }
    return null
  }
}
