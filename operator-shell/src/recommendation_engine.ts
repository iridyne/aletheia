import type {
  AgentRunSummary,
  DreamingSummary,
  OperatorRecommendation,
  PersistedOperatorArtifact,
  PersistedOperatorCommand,
  PersistedOperatorTask,
  OperatorSnapshot,
  RiskSummary,
  ReviewSummary,
  StrategySummary,
} from './schemas.js'

export class RecommendationEngine {
  private isTaskStuck(task: PersistedOperatorTask, thresholdMinutes = 30): boolean {
    if (task.status !== 'running') {
      return false
    }
    const startedAt = task.started_at || task.created_at
    if (!startedAt) {
      return false
    }
    const parsed = Date.parse(startedAt)
    if (Number.isNaN(parsed)) {
      return false
    }
    return Date.now() - parsed >= thresholdMinutes * 60 * 1000
  }

  private taskAgeMinutes(task: Pick<PersistedOperatorTask, 'created_at'>): number | null {
    const parsed = Date.parse(task.created_at)
    if (Number.isNaN(parsed)) {
      return null
    }
    return (Date.now() - parsed) / (60 * 1000)
  }

  private queueReviewPolicy(queuedTasks: PersistedOperatorTask[]): {
    reviewStage: 'attention' | 'urgent'
    severity: 'medium' | 'high'
    suggestedReviewCadenceSec: number
    reasons: string[]
    oldestAgeMinutes: number | null
    highPriorityQueuedCount: number
    oldestHighPriorityAgeMinutes: number | null
  } | null {
    if (queuedTasks.length === 0) {
      return null
    }

    const queuedAges = queuedTasks
      .map(task => this.taskAgeMinutes(task))
      .filter((age): age is number => age !== null)
    const oldestAgeMinutes =
      queuedAges.length > 0 ? queuedAges.reduce((max, age) => Math.max(max, age), 0) : null

    const highPriorityQueued = queuedTasks.filter(task => task.priority === 'high')
    const highPriorityAges = highPriorityQueued
      .map(task => this.taskAgeMinutes(task))
      .filter((age): age is number => age !== null)
    const oldestHighPriorityAgeMinutes =
      highPriorityAges.length > 0
        ? highPriorityAges.reduce((max, age) => Math.max(max, age), 0)
        : null

    const attention =
      queuedTasks.length >= 3 || (oldestAgeMinutes !== null && oldestAgeMinutes >= 15)
    if (!attention) {
      return null
    }

    const urgent =
      queuedTasks.length >= 5 ||
      (oldestAgeMinutes !== null && oldestAgeMinutes >= 30) ||
      (oldestHighPriorityAgeMinutes !== null && oldestHighPriorityAgeMinutes >= 10)

    const reasons: string[] = []
    if (oldestAgeMinutes !== null && oldestAgeMinutes >= 15) {
      reasons.push(`oldest queued task has waited about ${Math.floor(oldestAgeMinutes)} minutes`)
    }
    if (queuedTasks.length >= 3) {
      reasons.push(`${queuedTasks.length} queued tasks are accumulating`)
    }
    if (oldestHighPriorityAgeMinutes !== null && oldestHighPriorityAgeMinutes >= 10) {
      const label =
        highPriorityQueued.length === 1
          ? '1 high-priority queued task is aging'
          : `${highPriorityQueued.length} high-priority queued tasks are aging`
      reasons.push(`${label} (oldest about ${Math.floor(oldestHighPriorityAgeMinutes)} minutes)`)
    }

    return {
      reviewStage: urgent ? 'urgent' : 'attention',
      severity: urgent ? 'high' : 'medium',
      suggestedReviewCadenceSec: urgent ? 60 : 300,
      reasons,
      oldestAgeMinutes: oldestAgeMinutes === null ? null : Math.floor(oldestAgeMinutes),
      highPriorityQueuedCount: highPriorityQueued.length,
      oldestHighPriorityAgeMinutes:
        oldestHighPriorityAgeMinutes === null ? null : Math.floor(oldestHighPriorityAgeMinutes),
    }
  }

  private currentScheduleFor(agentName: string): string | null {
    const schedules: Record<string, string> = {
      market_data: 'every 5 minutes',
      market_observer: 'every 15 minutes',
      paper_trader: 'every 15 minutes',
      position_manager: 'every 5 minutes',
      exchange_sync: 'every 5 minutes',
      portfolio_snapshot: 'every 10 minutes',
      post_trade_reviewer: 'every 4 hours',
      risk_critic: 'every 6 hours',
      dreaming_agent: 'daily at 02:00',
      strategy_designer: 'daily at 01:00',
    }
    return schedules[agentName] || null
  }

  private suggestedScheduleFor(agentName: string): string | null {
    const current = this.currentScheduleFor(agentName)
    switch (current) {
      case 'every 15 minutes':
        return 'every 10 minutes'
      case 'every 10 minutes':
        return 'every 5 minutes'
      case 'every 5 minutes':
        return 'every 3 minutes'
      case 'every 4 hours':
        return 'every 2 hours'
      case 'every 6 hours':
        return 'every 4 hours'
      case 'daily at 02:00':
      case 'daily at 01:00':
        return 'every 12 hours'
      default:
        return null
    }
  }

  private parseErrors(raw: string | null | undefined): string[] {
    if (!raw) {
      return []
    }
    try {
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed.map(item => String(item)) : [String(parsed)]
    } catch {
      return [String(raw)]
    }
  }

  private runAgeMinutes(
    run: Pick<AgentRunSummary, 'started_at' | 'ended_at'>,
  ): number | null {
    const timestamp = run.ended_at || run.started_at
    if (!timestamp) {
      return null
    }
    const parsed = Date.parse(timestamp)
    if (Number.isNaN(parsed)) {
      return null
    }
    return (Date.now() - parsed) / (60 * 1000)
  }

  private runSortTimestamp(run: Pick<AgentRunSummary, 'started_at' | 'ended_at'>): number {
    const parsed = Date.parse(run.ended_at || run.started_at)
    return Number.isNaN(parsed) ? 0 : parsed
  }

  private commandCenterRequestedBehavior(
    commandName: string,
    sampleResponse: string | null | undefined,
  ): string {
    const response = String(sampleResponse || '').toLowerCase()
    const label = commandName.replaceAll('_', ' ') || 'this flow'
    if (
      response.includes('requires operator permission mode') ||
      response.includes('requires worker permission mode') ||
      response.includes('worker/global safety') ||
      response.includes('permission denied')
    ) {
      return `Add an inline permission gate and clearer next action for \`${commandName}\` in Command Center, so repeated denied attempts to ${label} surface the required mode before retry.`
    }
    if (response.includes('unknown command')) {
      return `Add a more discoverable affordance or alias around \`${commandName}\` in Command Center, so repeated attempts to ${label} do not fail as unknown commands.`
    }
    return `Improve Command Center behavior around \`${commandName}\` so repeated failed attempts to ${label} surface clearer guidance and a better next action.`
  }

  private artifactFreshnessMinutes(artifact: PersistedOperatorArtifact): number | null {
    const parsed = Date.parse(artifact.created_at)
    if (Number.isNaN(parsed)) {
      return null
    }
    return (Date.now() - parsed) / (60 * 1000)
  }

  private hasFreshReportArtifact(
    artifacts: PersistedOperatorArtifact[],
    artifactType: string,
    reportType: string,
    withinMinutes: number,
  ): boolean {
    return artifacts.some(artifact => {
      if (artifact.artifact_type !== artifactType) {
        return false
      }
      const ageMinutes = this.artifactFreshnessMinutes(artifact)
      if (ageMinutes === null || ageMinutes > withinMinutes) {
        return false
      }
      const content = artifact.content
      return (
        content &&
        typeof content === 'object' &&
        !Array.isArray(content) &&
        String(content.report_type || '') === reportType
      )
    })
  }

  list(
    snapshot: OperatorSnapshot,
    dreamingSummary?: DreamingSummary,
    reviewSummary?: ReviewSummary,
    strategySummary?: StrategySummary,
    recentAgentRuns?: AgentRunSummary[],
    recentArtifacts?: PersistedOperatorArtifact[],
    riskSummary?: RiskSummary,
    recentCommands?: PersistedOperatorCommand[],
    operatorTasks?: PersistedOperatorTask[],
  ): OperatorRecommendation[] {
    const recommendations: OperatorRecommendation[] = []

    if (snapshot.status.paused) {
      recommendations.push({
        id: 'operator_paused',
        kind: 'operator_paused',
        severity: 'medium',
        summary: 'Gateway is paused. Monitoring stays passive until a human resumes it.',
      })
    }

    if (!snapshot.status.gateway_running) {
      recommendations.push({
        id: 'gateway_offline',
        kind: 'gateway_offline',
        severity: 'high',
        summary: 'Gateway reports itself as not running. Inspect process supervision before trusting automation.',
        taskType: 'diagnose_gateway',
        eventType: 'operator_degraded',
        metadata: {
          status: 'gateway_offline',
        },
      })
    }

    const primaryIssue = snapshot.health.issues[0]
    if (snapshot.health.status !== 'healthy' || primaryIssue) {
      const issueKey = primaryIssue ? primaryIssue.split(':', 1)[0] : snapshot.health.status
      recommendations.push({
        id: `health:${issueKey}`,
        kind: 'health_attention_needed',
        severity: 'high',
        summary: primaryIssue
          ? `Gateway health issue: ${primaryIssue}`
          : `Gateway health is ${snapshot.health.status}.`,
        taskType: 'diagnose_system_health',
        eventType: 'operator_degraded',
        metadata: {
          issue: primaryIssue || snapshot.health.status,
          health_status: snapshot.health.status,
        },
      })
    }

    const healthIssues = snapshot.health.issues.filter(issue => issue !== 'trading_paused')
    if (
      healthIssues.length > 0 &&
      !this.hasFreshReportArtifact(
        recentArtifacts || [],
        'diagnostic_report',
        'system_health',
        60,
      )
    ) {
      recommendations.push({
        id: 'system-health-report-refresh',
        kind: 'system_health_report_refresh_due',
        severity: 'medium',
        summary:
          'Gateway health is degraded and no recent system-health diagnostic report exists. Refresh a diagnostic report for operator follow-up.',
        taskType: 'create_diagnostic_report',
        metadata: {
          task_type: 'create_diagnostic_report',
          report_type: 'system_health',
          title: 'System Health Diagnostic Report',
          health_issues: healthIssues,
          refresh_window_minutes: 60,
        },
      })
    }

    const failedTasks = (operatorTasks || []).filter(task => task.status === 'failed')
    if (failedTasks.length > 0) {
      recommendations.push({
        id: 'failed-tasks',
        kind: 'failed_tasks_present',
        severity: 'high',
        summary:
          failedTasks.length === 1
            ? `1 failed operator task requires review before trusting background supervision.`
            : `${failedTasks.length} failed operator tasks require review before trusting background supervision.`,
        metadata: {
          task_ids: failedTasks.slice(0, 5).map(task => task.id),
        },
      })
    }

    const stuckTasks = (operatorTasks || []).filter(task => this.isTaskStuck(task))
    if (stuckTasks.length > 0) {
      recommendations.push({
        id: 'stuck-tasks',
        kind: 'stuck_tasks_present',
        severity: 'high',
        summary:
          stuckTasks.length === 1
            ? `1 running operator task appears stuck and should be reviewed before assuming the shell is making progress.`
            : `${stuckTasks.length} running operator tasks appear stuck and should be reviewed before assuming the shell is making progress.`,
        metadata: {
          task_ids: stuckTasks.slice(0, 5).map(task => task.id),
        },
      })
    }

    const queuedTasks = (operatorTasks || []).filter(task => task.status === 'queued')
    if (queuedTasks.length > 0) {
      recommendations.push({
        id: 'queued-tasks',
        kind: 'queued_tasks_present',
        severity: 'medium',
        summary:
          queuedTasks.length === 1
            ? `1 queued operator task is waiting in backlog review.`
            : `${queuedTasks.length} queued operator tasks are waiting in backlog review.`,
        metadata: {
          task_ids: queuedTasks.slice(0, 5).map(task => task.id),
        },
      })

      const queueReview = this.queueReviewPolicy(queuedTasks)
      if (queueReview) {
        recommendations.push({
          id: 'queue-review-needed',
          kind: 'queue_review_needed',
          severity: queueReview.severity,
          summary: `Queued operator backlog needs queue review (${queueReview.reviewStage}): ${queueReview.reasons.join(' and ')}.`,
          metadata: {
            task_ids: queuedTasks.slice(0, 5).map(task => task.id),
            queued_count: queuedTasks.length,
            oldest_age_minutes: queueReview.oldestAgeMinutes,
            review_stage: queueReview.reviewStage,
            suggested_review_cadence_sec: queueReview.suggestedReviewCadenceSec,
            high_priority_queued_count: queueReview.highPriorityQueuedCount,
            oldest_high_priority_age_minutes: queueReview.oldestHighPriorityAgeMinutes,
          },
        })
      }
    }

    const marketObserverRuns = (recentAgentRuns || []).filter(
      run => run.agent_name === 'market_observer',
    )
    const latestMarketObserverRun = marketObserverRuns
      .slice()
      .sort(
        (left, right) =>
          Date.parse(right.ended_at || right.started_at) - Date.parse(left.ended_at || left.started_at),
      )[0]
    const marketObserverAgeMinutes = latestMarketObserverRun
      ? this.runAgeMinutes(latestMarketObserverRun)
      : null
    const marketScanRefreshDue =
      !snapshot.status.paused &&
      (latestMarketObserverRun == null ||
        (marketObserverAgeMinutes !== null && marketObserverAgeMinutes >= 45))
    if (marketScanRefreshDue) {
      recommendations.push({
        id: 'market-scan-refresh-due',
        kind: 'market_scan_refresh_due',
        severity: 'medium',
        summary:
          latestMarketObserverRun == null
            ? 'Market observer has no recent activity. Refresh market scan to repopulate opportunities.'
            : `Latest market observer activity is about ${Math.floor(marketObserverAgeMinutes || 0)} minutes old. Refresh market scan to repopulate opportunities.`,
        taskType: 'run_market_scan',
        metadata: {
          task_type: 'run_market_scan',
          last_run_id: latestMarketObserverRun?.id || null,
          last_run_status: latestMarketObserverRun?.status || null,
          stale_minutes:
            marketObserverAgeMinutes === null ? null : Math.floor(marketObserverAgeMinutes),
        },
      })
    }

    const failedRunStatuses = new Set(['failed', 'error', 'completed_with_errors'])
    const runsByAgent = new Map<string, AgentRunSummary[]>()
    for (const run of recentAgentRuns || []) {
      const agentName = String(run.agent_name || '').trim()
      if (!agentName) {
        continue
      }
      const bucket = runsByAgent.get(agentName) || []
      bucket.push(run)
      runsByAgent.set(agentName, bucket)
    }
    const failedRerunCandidate = [...runsByAgent.entries()]
      .map(([agentName, runs]) => ({
        agentName,
        runs: runs
          .slice()
          .sort((left, right) => this.runSortTimestamp(right) - this.runSortTimestamp(left)),
      }))
      .map(item => {
        const latestRun = item.runs[0]
        const latestFailure =
          latestRun && failedRunStatuses.has(String(latestRun.status || '')) ? latestRun : null
        const latestFailureAgeMinutes = latestFailure ? this.runAgeMinutes(latestFailure) : null
        return {
          ...item,
          latestFailure,
          latestFailureAgeMinutes,
          failedRunCount: item.runs.filter(run => failedRunStatuses.has(String(run.status || ''))).length,
        }
      })
      .find(item => {
        if (!item.latestFailure || item.latestFailureAgeMinutes === null) {
          return false
        }
        if (item.latestFailureAgeMinutes < 15) {
          return false
        }
        if (item.failedRunCount >= 2) {
          return false
        }
        return String(item.latestFailure.trigger_type || '') !== 'manual'
      })
    if (!snapshot.status.paused && failedRerunCandidate?.latestFailure) {
      recommendations.push({
        id: `failed-agent-rerun:${failedRerunCandidate.latestFailure.id}`,
        kind: 'failed_agent_rerun_due',
        severity: 'medium',
        summary: `${failedRerunCandidate.agentName} has a recent failed run with no newer recovery. Rerun the failed task to restore scheduled coverage.`,
        taskType: 'rerun_failed_agent_task',
        metadata: {
          task_type: 'rerun_failed_agent_task',
          agent_name: failedRerunCandidate.agentName,
          failed_run_id: failedRerunCandidate.latestFailure.id,
          failed_run_status: failedRerunCandidate.latestFailure.status,
          stale_minutes: Math.floor(failedRerunCandidate.latestFailureAgeMinutes || 0),
        },
      })
    }

    const hasRecentDreamingActivity =
      (dreamingSummary?.recent_dreaming_logs.length || 0) > 0 ||
      (dreamingSummary?.recent_dreaming_runs.length || 0) > 0
    if (!snapshot.status.paused && dreamingSummary && !hasRecentDreamingActivity) {
      recommendations.push({
        id: 'dreaming-due',
        kind: 'dreaming_due',
        severity: 'medium',
        summary: 'No recent dreaming consolidation is visible. Queue a dreaming run to refresh operator memory.',
        taskType: 'run_dreaming',
        metadata: {
          reason: 'no_recent_dreaming_activity',
        },
      })
    }

    const missingReviewStrategyIds = reviewSummary?.missing_review_strategy_ids || []
    const reviewRecentlyAttempted = (reviewSummary?.recent_post_trade_reviewer_runs.length || 0) > 0
    if (!snapshot.status.paused && missingReviewStrategyIds.length > 0 && !reviewRecentlyAttempted) {
      recommendations.push({
        id: 'review-due',
        kind: 'review_due',
        severity: 'medium',
        summary:
          missingReviewStrategyIds.length === 1
            ? `Closed strategy ${missingReviewStrategyIds[0]} still lacks a recent post-trade review.`
            : `${missingReviewStrategyIds.length} closed strategies still lack recent post-trade reviews.`,
        taskType: 'generate_review',
        metadata: {
          missing_review_strategy_ids: missingReviewStrategyIds,
          closed_position_count: reviewSummary?.recent_closed_positions.length || 0,
        },
      })
    }

    const needsStrategyEvaluationIds = strategySummary?.needs_operator_evaluation_strategy_ids || []
    if (!snapshot.status.paused && needsStrategyEvaluationIds.length > 0) {
      const firstFlagged =
        strategySummary?.under_review_strategies.find(
          item => String(item.id || '') === needsStrategyEvaluationIds[0],
        ) ||
        strategySummary?.paused_paper_strategies.find(
          item => String(item.id || '') === needsStrategyEvaluationIds[0],
        ) ||
        null
      const strategyName = firstFlagged && typeof firstFlagged.name === 'string' ? firstFlagged.name : null
      recommendations.push({
        id: 'strategy-review-due',
        kind: 'strategy_review_due',
        severity: 'medium',
        summary:
          needsStrategyEvaluationIds.length === 1
            ? `Strategy ${strategyName || needsStrategyEvaluationIds[0]} still needs an operator evaluation session.`
            : `${needsStrategyEvaluationIds.length} strategies still need operator evaluation sessions.`,
        taskType: 'evaluate_strategy_state',
        metadata: {
          strategy_ids: needsStrategyEvaluationIds,
          strategy_id: needsStrategyEvaluationIds[0],
          strategy_name: strategyName,
        },
      })
    }

    const needsPromotionProposalIds =
      strategySummary?.needs_operator_promotion_proposal_strategy_ids || []
    if (!snapshot.status.paused && needsPromotionProposalIds.length > 0) {
      const firstCandidate =
        strategySummary?.active_paper_strategies.find(
          item => String(item.id || '') === needsPromotionProposalIds[0],
        ) || null
      const strategyName =
        firstCandidate && typeof firstCandidate.name === 'string'
          ? firstCandidate.name
          : null
      recommendations.push({
        id: 'strategy-promotion-proposal-due',
        kind: 'strategy_promotion_proposal_due',
        severity: 'medium',
        summary:
          needsPromotionProposalIds.length === 1
            ? `Strategy ${strategyName || needsPromotionProposalIds[0]} passed recent promotion checks and should be proposed for paper_passed human review.`
            : `${needsPromotionProposalIds.length} strategies passed recent promotion checks and should be proposed for human review.`,
        taskType: 'create_repair_proposal',
        metadata: {
          strategy_ids: needsPromotionProposalIds,
          strategy_id: needsPromotionProposalIds[0],
          strategy_name: strategyName,
          target_state: 'paper_passed',
          proposal_command: 'propose_strategy_promotion',
        },
      })
    }

    const failedRunsByAgent = new Map<string, AgentRunSummary[]>()
    for (const run of recentAgentRuns || []) {
      if (run.status !== 'failed') {
        continue
      }
      const bucket = failedRunsByAgent.get(run.agent_name) || []
      bucket.push(run)
      failedRunsByAgent.set(run.agent_name, bucket)
    }
    const repeatedFailedAgent = [...failedRunsByAgent.entries()]
      .map(([agentName, runs]) => ({ agentName, runs }))
      .find(item => {
        if (item.runs.length < 2) {
          return false
        }
        const alreadyProposed = (recentArtifacts || []).some(artifact => {
          if (artifact.artifact_type !== 'prompt_change_proposal') {
            return false
          }
          const content = artifact.content
          return (
            content &&
            typeof content === 'object' &&
            !Array.isArray(content) &&
            String(content.target || '') === item.agentName
          )
        })
        return !alreadyProposed
      })
    if (!snapshot.status.paused && repeatedFailedAgent) {
      const errorSamples = repeatedFailedAgent.runs
        .flatMap(run => this.parseErrors(run.errors))
        .filter(Boolean)
        .slice(0, 3)
      recommendations.push({
        id: `agent-prompt-proposal:${repeatedFailedAgent.agentName}`,
        kind: 'agent_prompt_change_proposal_due',
        severity: 'medium',
        summary: `${repeatedFailedAgent.agentName} failed ${repeatedFailedAgent.runs.length} recent runs and should get a prompt/playbook change proposal for human review.`,
        taskType: 'create_repair_proposal',
        metadata: {
          agent_name: repeatedFailedAgent.agentName,
          target: repeatedFailedAgent.agentName,
          failed_run_ids: repeatedFailedAgent.runs.map(run => run.id),
          error_samples: errorSamples,
          proposal_command: 'propose_prompt_change',
        },
      })
    }

    const manualRunsByAgent = new Map<string, AgentRunSummary[]>()
    for (const run of recentAgentRuns || []) {
      if (run.trigger_type !== 'manual') {
        continue
      }
      const bucket = manualRunsByAgent.get(run.agent_name) || []
      bucket.push(run)
      manualRunsByAgent.set(run.agent_name, bucket)
    }
    const repeatedManualAgent = [...manualRunsByAgent.entries()]
      .map(([agentName, runs]) => ({ agentName, runs }))
      .find(item => {
        if (item.runs.length < 2) {
          return false
        }
        const currentSchedule = this.currentScheduleFor(item.agentName)
        const requestedSchedule = this.suggestedScheduleFor(item.agentName)
        if (!currentSchedule || !requestedSchedule) {
          return false
        }
        const alreadyProposed = (recentArtifacts || []).some(artifact => {
          if (artifact.artifact_type !== 'schedule_change_proposal') {
            return false
          }
          const content = artifact.content
          return (
            content &&
            typeof content === 'object' &&
            !Array.isArray(content) &&
            String(content.agent_name || '') === item.agentName
          )
        })
        return !alreadyProposed
      })
    if (!snapshot.status.paused && repeatedManualAgent) {
      const currentSchedule = this.currentScheduleFor(repeatedManualAgent.agentName)
      const requestedSchedule = this.suggestedScheduleFor(repeatedManualAgent.agentName)
      recommendations.push({
        id: `agent-schedule-proposal:${repeatedManualAgent.agentName}`,
        kind: 'agent_schedule_change_proposal_due',
        severity: 'medium',
        summary: `${repeatedManualAgent.agentName} needed ${repeatedManualAgent.runs.length} recent manual runs on top of its scheduled cadence and should get a schedule-change proposal for human review.`,
        taskType: 'create_repair_proposal',
        metadata: {
          agent_name: repeatedManualAgent.agentName,
          current_schedule: currentSchedule,
          requested_schedule: requestedSchedule,
          manual_run_ids: repeatedManualAgent.runs.map(run => run.id),
          proposal_command: 'propose_schedule_change',
        },
      })
    }

    const targetDate = new Date().toISOString().slice(0, 10)
    const recentDailyReviews = dreamingSummary?.recent_daily_reviews || []
    const hasDailyReviewForTargetDate = recentDailyReviews.some(
      item => String(item.created_at || '').slice(0, 10) === targetDate,
    )
    const recentActivityRunCount = (recentAgentRuns || []).filter(run => {
      const ageMinutes = this.runAgeMinutes(run)
      return ageMinutes !== null && ageMinutes <= 12 * 60
    }).length
    if (!snapshot.status.paused && !hasDailyReviewForTargetDate && recentActivityRunCount > 0) {
      recommendations.push({
        id: `daily-review-refresh:${targetDate}`,
        kind: 'daily_review_refresh_due',
        severity: 'medium',
        summary: `No daily review has been written for ${targetDate} UTC. Refresh the non-critical operator summary report.`,
        taskType: 'create_daily_review',
        metadata: {
          task_type: 'create_daily_review',
          target_date: targetDate,
          recent_activity_run_count: recentActivityRunCount,
          title: `Operator Daily Review ${targetDate}`,
        },
      })
    }

    if (
      !snapshot.status.paused &&
      reviewSummary?.recent_closed_positions?.length &&
      !this.hasFreshReportArtifact(
        recentArtifacts || [],
        'review_report',
        'trade_review',
        360,
      )
    ) {
      recommendations.push({
        id: 'trade-review-report-refresh',
        kind: 'trade_review_report_refresh_due',
        severity: 'medium',
        summary:
          'Recent closed positions exist and no fresh trade review report is visible. Refresh a non-trading trade review summary.',
        taskType: 'create_review_report',
        metadata: {
          task_type: 'create_review_report',
          report_type: 'trade_review',
          title: 'Trade Review Report',
          closed_position_count: reviewSummary.recent_closed_positions.length,
          missing_review_strategy_ids: reviewSummary.missing_review_strategy_ids,
          refresh_window_minutes: 360,
        },
      })
    }

    const recentFailedRunCount = (recentAgentRuns || []).filter(run => {
      const ageMinutes = this.runAgeMinutes(run)
      return (
        ageMinutes !== null &&
        ageMinutes <= 12 * 60 &&
        ['failed', 'error', 'completed_with_errors'].includes(String(run.status || ''))
      )
    }).length
    if (
      !snapshot.status.paused &&
      (recentFailedRunCount > 0 || recentActivityRunCount >= 5) &&
      !this.hasFreshReportArtifact(
        recentArtifacts || [],
        'review_report',
        'agent_performance',
        360,
      )
    ) {
      recommendations.push({
        id: 'agent-performance-report-refresh',
        kind: 'agent_performance_report_refresh_due',
        severity: 'medium',
        summary: 'Recent agent activity warrants a fresh agent-performance report for operator review.',
        taskType: 'create_review_report',
        metadata: {
          task_type: 'create_review_report',
          report_type: 'agent_performance',
          title: 'Agent Performance Report',
          recent_activity_run_count: recentActivityRunCount,
          failed_run_count: recentFailedRunCount,
          refresh_window_minutes: 360,
        },
      })
    }

    const dueRiskBoundaryChange = riskSummary?.needs_operator_risk_boundary_proposals?.[0]
    if (
      !snapshot.status.paused &&
      (riskSummary?.recent_rejected_decisions?.length || 0) > 0 &&
      !this.hasFreshReportArtifact(
        recentArtifacts || [],
        'review_report',
        'risk_review',
        360,
      )
    ) {
      recommendations.push({
        id: 'risk-review-report-refresh',
        kind: 'risk_review_report_refresh_due',
        severity: 'medium',
        summary:
          'Recent risk rejections exist and no fresh risk review report is visible. Refresh a risk-focused operator summary.',
        taskType: 'create_review_report',
        metadata: {
          task_type: 'create_review_report',
          report_type: 'risk_review',
          title: 'Risk Review Report',
          rejected_decision_count: riskSummary?.recent_rejected_decisions?.length || 0,
          proposal_candidate_count: riskSummary?.needs_operator_risk_boundary_proposals?.length || 0,
          refresh_window_minutes: 360,
        },
      })
    }
    if (!snapshot.status.paused && dueRiskBoundaryChange) {
      recommendations.push({
        id: `risk-boundary-proposal:${dueRiskBoundaryChange.boundary_key}`,
        kind: 'risk_boundary_change_proposal_due',
        severity: 'medium',
        summary: dueRiskBoundaryChange.summary,
        taskType: 'create_repair_proposal',
        metadata: {
          boundary_key: dueRiskBoundaryChange.boundary_key,
          violation_kind: dueRiskBoundaryChange.violation_kind,
          rejection_count: dueRiskBoundaryChange.rejection_count,
          changes: dueRiskBoundaryChange.changes,
          sample_violations: dueRiskBoundaryChange.sample_violations,
          decision_ids: dueRiskBoundaryChange.decision_ids,
          affected_symbols: dueRiskBoundaryChange.affected_symbols || [],
          execution_modes: dueRiskBoundaryChange.execution_modes || [],
          proposal_command: 'propose_risk_boundary_change',
        },
      })
    }

    const rejectedCommandsByName = new Map<string, PersistedOperatorCommand[]>()
    for (const command of recentCommands || []) {
      if (command.permitted) {
        continue
      }
      if (command.session_kind && command.session_kind !== 'command_session') {
        continue
      }
      const bucket = rejectedCommandsByName.get(command.command) || []
      bucket.push(command)
      rejectedCommandsByName.set(command.command, bucket)
    }
    const repeatedRejectedCommand = [...rejectedCommandsByName.entries()]
      .map(([commandName, commands]) => ({ commandName, commands }))
      .find(item => {
        if (item.commands.length < 2) {
          return false
        }
        const alreadyProposed = (recentArtifacts || []).some(artifact => {
          if (artifact.artifact_type !== 'command_center_behavior_proposal') {
            return false
          }
          const content = artifact.content
          return (
            content &&
            typeof content === 'object' &&
            !Array.isArray(content) &&
            String(content.target_command || '') === item.commandName
          )
        })
        return !alreadyProposed
      })
    if (!snapshot.status.paused && repeatedRejectedCommand) {
      const sampleResponses = repeatedRejectedCommand.commands
        .map(command => String(command.result_summary || '').trim())
        .filter(Boolean)
        .slice(0, 3)
      const sampleInputs = repeatedRejectedCommand.commands
        .map(command => String(command.session_user_input || '').trim())
        .filter(Boolean)
        .slice(0, 3)
      recommendations.push({
        id: `command-center-behavior:${repeatedRejectedCommand.commandName}`,
        kind: 'command_center_behavior_change_proposal_due',
        severity: 'medium',
        summary: `Command Center rejected \`${repeatedRejectedCommand.commandName}\` ${repeatedRejectedCommand.commands.length} recent times and should get a human-reviewed behavior proposal instead of making the operator rediscover the same failure.`,
        taskType: 'create_repair_proposal',
        metadata: {
          target_command: repeatedRejectedCommand.commandName,
          rejection_count: repeatedRejectedCommand.commands.length,
          requested_behavior: this.commandCenterRequestedBehavior(
            repeatedRejectedCommand.commandName,
            sampleResponses[0],
          ),
          sample_responses: sampleResponses,
          sample_inputs: sampleInputs,
          command_ids: repeatedRejectedCommand.commands.map(command => command.id),
          proposal_command: 'propose_command_center_behavior_change',
        },
      })
    }

    return recommendations
  }
}
