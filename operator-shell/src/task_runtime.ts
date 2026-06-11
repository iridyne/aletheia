import type {
  BackendGatewayClient,
  OperatorRecommendation,
  OperatorTaskState,
  PersistedOperatorTask,
} from './schemas.js'

export class TaskRuntime {
  private queuedTasks: Array<{
    recommendationId: string | null
    taskId: string
    taskType: string
    assignedTo: string
    inputData: Record<string, unknown>
    priority: string
    createdAt: string | null
    restoredFromRecovery: boolean
  }> = []

  private activeTask:
    | {
        recommendationId: string | null
        taskId: string
        taskType: string
        assignedTo: string
        inputData: Record<string, unknown>
        priority: string
        createdAt: string | null
        restoredFromRecovery: boolean
      }
    | null = null

  private state: OperatorTaskState = {
    currentTaskId: null,
    queueSize: 0,
  }

  constructor(
    private readonly backendClient: Pick<BackendGatewayClient, 'createTask' | 'updateTask'>,
  ) {}

  snapshot(): OperatorTaskState {
    return this.state
  }

  private toRuntimeTask(task: PersistedOperatorTask, restoredFromRecovery: boolean) {
    return {
      recommendationId:
        typeof task.input_data?.recommendation_id === 'string'
          ? task.input_data.recommendation_id
          : null,
      taskId: task.id,
      taskType: task.task_type,
      assignedTo: task.assigned_to,
      inputData: task.input_data || {},
      priority: task.priority,
      createdAt: task.created_at,
      restoredFromRecovery,
    }
  }

  private queueSortValue(priority: string): number {
    return priority === 'high' ? 0 : priority === 'normal' ? 1 : 2
  }

  private sortQueuedTasks(): void {
    this.queuedTasks.sort((left, right) => {
      const priorityDelta = this.queueSortValue(left.priority) - this.queueSortValue(right.priority)
      if (priorityDelta !== 0) {
        return priorityDelta
      }
      return Date.parse(left.createdAt || '') - Date.parse(right.createdAt || '')
    })
  }

  private syncState(): void {
    this.state = {
      currentTaskId: this.activeTask?.taskId || null,
      queueSize: (this.activeTask ? 1 : 0) + this.queuedTasks.length,
    }
  }

  private assignedToFor(taskType: string): string {
    return taskType === 'run_dreaming'
      ? 'dreaming_agent'
      : taskType === 'generate_review'
        ? 'post_trade_reviewer'
        : taskType === 'run_portfolio_snapshot'
          ? 'portfolio_agent'
        : taskType === 'run_strategy_design'
          ? 'strategy_designer'
          : taskType === 'run_risk_critique'
            ? 'risk_critic'
        : 'operator'
  }

  private async createQueuedTask(recommendation: OperatorRecommendation): Promise<void> {
    const taskType = recommendation.taskType || 'diagnose_system_health'
    const assignedTo = this.assignedToFor(taskType)
    const inputData = {
      recommendation_id: recommendation.id,
      kind: recommendation.kind,
      summary: recommendation.summary,
      ...(recommendation.metadata || {}),
    }
    const created = await this.backendClient.createTask({
      task_type: taskType,
      status: 'queued',
      priority: recommendation.severity === 'high' ? 'high' : 'normal',
      created_by: 'operator_loop',
      assigned_to: assignedTo,
      input_data: inputData,
    })
    this.queuedTasks.push({
      recommendationId: recommendation.id,
      taskId: created.id,
      taskType,
      assignedTo,
      inputData,
      priority: recommendation.severity === 'high' ? 'high' : 'normal',
      createdAt: null,
      restoredFromRecovery: false,
    })
    this.sortQueuedTasks()
  }

  private async promoteQueuedTask(): Promise<void> {
    this.sortQueuedTasks()
    const next = this.queuedTasks.shift()
    if (!next) {
      return
    }
    await this.backendClient.updateTask(next.taskId, {
      status: 'running',
    })
    this.activeTask = {
      ...next,
      restoredFromRecovery: false,
    }
    this.syncState()
  }

  getActiveTask():
    | {
        recommendationId: string | null
        taskId: string
        taskType: string
        assignedTo: string
        inputData: Record<string, unknown>
        priority: string
        createdAt: string | null
        restoredFromRecovery: boolean
      }
    | null {
    return this.activeTask ? { ...this.activeTask } : null
  }

  getTaskForRecommendation(
    recommendationId: string,
  ):
    | {
        recommendationId: string | null
        taskId: string
        taskType: string
        assignedTo: string
        inputData: Record<string, unknown>
        priority: string
        createdAt: string | null
        restoredFromRecovery: boolean
        active: boolean
      }
    | null {
    if (this.activeTask?.recommendationId === recommendationId) {
      return {
        ...this.activeTask,
        active: true,
      }
    }

    const queuedTask = this.queuedTasks.find(task => task.recommendationId === recommendationId)
    if (!queuedTask) {
      return null
    }

    return {
      ...queuedTask,
      active: false,
    }
  }

  restore(tasks: PersistedOperatorTask[]): void {
    const active = tasks.find(task => task.status === 'running')
    const queued = tasks.filter(task => task.status === 'queued')
    if (!active) {
      this.activeTask = null
      this.queuedTasks = queued
        .map(task => this.toRuntimeTask(task, true))
      this.sortQueuedTasks()
      this.syncState()
      return
    }

    this.activeTask = this.toRuntimeTask(active, true)
    this.queuedTasks = queued
      .map(task => this.toRuntimeTask(task, true))
    this.sortQueuedTasks()
    this.syncState()
  }

  async completeActiveTask(output_data: Record<string, unknown>): Promise<void> {
    if (!this.activeTask) {
      return
    }
    const taskId = this.activeTask.taskId
    await this.backendClient.updateTask(taskId, {
      status: 'completed',
      output_data,
    })
    this.activeTask = null
    this.syncState()
  }

  async completeTaskForRecommendation(
    recommendationId: string,
    output_data: Record<string, unknown>,
  ): Promise<boolean> {
    if (this.activeTask?.recommendationId === recommendationId) {
      await this.completeActiveTask(output_data)
      return true
    }

    const queuedIndex = this.queuedTasks.findIndex(task => task.recommendationId === recommendationId)
    if (queuedIndex === -1) {
      return false
    }

    const [task] = this.queuedTasks.splice(queuedIndex, 1)
    await this.backendClient.updateTask(task.taskId, {
      status: 'completed',
      output_data,
    })
    this.syncState()
    return true
  }

  async failActiveTask(error: string, output_data?: Record<string, unknown>): Promise<void> {
    if (!this.activeTask) {
      return
    }
    const taskId = this.activeTask.taskId
    await this.backendClient.updateTask(taskId, {
      status: 'failed',
      error,
      output_data,
    })
    this.activeTask = null
    this.syncState()
  }

  async failTaskForRecommendation(
    recommendationId: string,
    error: string,
    output_data?: Record<string, unknown>,
  ): Promise<boolean> {
    if (this.activeTask?.recommendationId === recommendationId) {
      await this.failActiveTask(error, output_data)
      return true
    }

    const queuedIndex = this.queuedTasks.findIndex(task => task.recommendationId === recommendationId)
    if (queuedIndex === -1) {
      return false
    }

    const [task] = this.queuedTasks.splice(queuedIndex, 1)
    await this.backendClient.updateTask(task.taskId, {
      status: 'failed',
      error,
      output_data,
    })
    this.syncState()
    return true
  }

  async sync(recommendations: OperatorRecommendation[]): Promise<void> {
    const actionable = recommendations.filter(item => item.taskType)
    const representedRecommendationIds = new Set(
      [this.activeTask?.recommendationId || null, ...this.queuedTasks.map(task => task.recommendationId)].filter(
        (value): value is string => typeof value === 'string' && value.length > 0,
      ),
    )

    if (this.activeTask?.restoredFromRecovery) {
      this.activeTask = {
        ...this.activeTask,
        restoredFromRecovery: false,
      }
      for (const recommendation of actionable) {
        if (!representedRecommendationIds.has(recommendation.id)) {
          await this.createQueuedTask(recommendation)
          representedRecommendationIds.add(recommendation.id)
        }
      }
      this.syncState()
      return
    }

    const activeRecommendationStillActionable =
      this.activeTask?.recommendationId != null &&
      actionable.some(item => item.id === this.activeTask?.recommendationId)

    if (!activeRecommendationStillActionable && this.activeTask && actionable.length === 0) {
      await this.completeActiveTask({
        summary: 'Condition cleared; returning to passive observation.',
      })
      if (this.queuedTasks.length > 0) {
        await this.promoteQueuedTask()
      }
      return
    }

    if (!activeRecommendationStillActionable && this.activeTask && actionable.length > 0) {
      await this.completeActiveTask({
        summary: 'Superseded by a newer operator diagnostic.',
      })
    }

    if (!this.activeTask && this.queuedTasks.length > 0) {
      await this.promoteQueuedTask()
    }

    if (!this.activeTask && actionable.length === 0) {
      this.syncState()
      return
    }

    if (!this.activeTask && actionable.length > 0) {
      const first = actionable[0]
      const taskType = first.taskType || 'diagnose_system_health'
      const assignedTo = this.assignedToFor(taskType)
      const inputData = {
        recommendation_id: first.id,
        kind: first.kind,
        summary: first.summary,
        ...(first.metadata || {}),
      }
      const created = await this.backendClient.createTask({
        task_type: taskType,
        status: 'running',
        priority: first.severity === 'high' ? 'high' : 'normal',
        created_by: 'operator_loop',
        assigned_to: assignedTo,
        input_data: inputData,
      })
      this.activeTask = {
        recommendationId: first.id,
        taskId: created.id,
        taskType,
        assignedTo,
        inputData,
        priority: first.severity === 'high' ? 'high' : 'normal',
        createdAt: null,
        restoredFromRecovery: false,
      }
      representedRecommendationIds.add(first.id)
    }

    for (const recommendation of actionable) {
      if (recommendation.id === this.activeTask?.recommendationId) {
        continue
      }
      if (representedRecommendationIds.has(recommendation.id)) {
        continue
      }
      await this.createQueuedTask(recommendation)
      representedRecommendationIds.add(recommendation.id)
    }

    this.syncState()
  }
}
