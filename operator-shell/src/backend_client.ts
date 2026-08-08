import type {
  AgentRunInvocationResult,
  AgentRunSummary,
  OperatorArtifactCreate,
  OperatorCommandExecution,
  OperatorCommandExecutionResult,
  OperatorContextQuery,
  OperatorContextSnapshot,
  BackendGatewayClient,
  DreamingSummary,
  GatewayHealthSnapshot,
  GatewayStatusSnapshot,
  OperatorEventCreate,
  OperatorExtensionRecord,
  OperatorHeartbeat,
  OperatorMessageCreate,
  PortfolioSnapshotRecord,
  PortfolioSummary,
  OperatorCompactionResult,
  PersistedOperatorCommand,
  OperatorSessionCreate,
  OperatorSessionDetail,
  OperatorSessionSummary,
  OperatorTaskCreate,
  OperatorTaskUpdate,
  PersistedOperatorArtifact,
  PersistedOperatorEvent,
  PersistedOperatorStatus,
  PersistedOperatorTask,
  RiskSummary,
  ReviewSummary,
  StrategySummary,
} from './schemas.js'
import { gatewayRequestFailureMessage } from './gateway_errors.js'

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
    },
    ...init,
  })
  if (!response.ok) {
    throw new Error(await gatewayRequestFailureMessage(url, init, response))
  }
  return response.json() as Promise<T>
}

function parseStringList(value: string | null | undefined): string[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.map(item => String(item)) : [String(parsed)]
  } catch {
    return [String(value)]
  }
}

const TERMINAL_AGENT_RUN_STATUSES = new Set([
  'completed',
  'completed_with_errors',
  'failed',
  'error',
  'timed_out',
  'cancelled',
  'rejected',
])

export class BackendClient implements BackendGatewayClient {
  constructor(private readonly baseUrl: string) {}

  async getGatewayStatus(): Promise<GatewayStatusSnapshot> {
    return fetchJson<GatewayStatusSnapshot>(`${this.baseUrl}/api/status`)
  }

  async getHealth(): Promise<GatewayHealthSnapshot> {
    return fetchJson<GatewayHealthSnapshot>(`${this.baseUrl}/api/health`)
  }

  async getOperatorStatus(): Promise<PersistedOperatorStatus> {
    return fetchJson<PersistedOperatorStatus>(`${this.baseUrl}/api/operator/status`)
  }

  async getOperatorEvents(limit = 50): Promise<PersistedOperatorEvent[]> {
    return fetchJson<PersistedOperatorEvent[]>(
      `${this.baseUrl}/api/operator/events?limit=${encodeURIComponent(String(limit))}`,
    )
  }

  async getOperatorSessions(limit = 50): Promise<OperatorSessionSummary[]> {
    return fetchJson<OperatorSessionSummary[]>(
      `${this.baseUrl}/api/operator/sessions?limit=${encodeURIComponent(String(limit))}`,
    )
  }

  async getOperatorSession(sessionId: string): Promise<OperatorSessionDetail> {
    return fetchJson<OperatorSessionDetail>(
      `${this.baseUrl}/api/operator/sessions/${encodeURIComponent(sessionId)}`,
    )
  }

  async getOperatorCommands(limit = 100): Promise<PersistedOperatorCommand[]> {
    return fetchJson<PersistedOperatorCommand[]>(
      `${this.baseUrl}/api/operator/commands?limit=${encodeURIComponent(String(limit))}`,
    )
  }

  async getOperatorTasks(status?: string, limit?: number): Promise<PersistedOperatorTask[]> {
    const query = new URLSearchParams()
    if (status) {
      query.set('status', status)
    }
    if (limit) {
      query.set('limit', String(limit))
    }
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    return fetchJson<PersistedOperatorTask[]>(`${this.baseUrl}/api/operator/tasks${suffix}`)
  }

  async getOperatorContext(payload: OperatorContextQuery): Promise<OperatorContextSnapshot> {
    return fetchJson<OperatorContextSnapshot>(`${this.baseUrl}/api/operator/internal/context/query`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async createSession(payload: OperatorSessionCreate): Promise<OperatorSessionSummary> {
    return fetchJson<OperatorSessionSummary>(`${this.baseUrl}/api/operator/internal/sessions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async createArtifact(payload: OperatorArtifactCreate): Promise<PersistedOperatorArtifact> {
    return fetchJson<PersistedOperatorArtifact>(`${this.baseUrl}/api/operator/internal/artifacts`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getDreamingSummary(): Promise<DreamingSummary> {
    return fetchJson<DreamingSummary>(`${this.baseUrl}/api/dreaming/summary`)
  }

  async getReviewSummary(): Promise<ReviewSummary> {
    return fetchJson<ReviewSummary>(`${this.baseUrl}/api/reviews/summary`)
  }

  async getStrategySummary(): Promise<StrategySummary> {
    return fetchJson<StrategySummary>(`${this.baseUrl}/api/strategies/summary`)
  }

  async getRiskSummary(): Promise<RiskSummary> {
    return fetchJson<RiskSummary>(`${this.baseUrl}/api/risk/summary`)
  }

  async getPortfolioSummary(): Promise<PortfolioSummary> {
    return fetchJson<PortfolioSummary>(`${this.baseUrl}/api/portfolio`)
  }

  async getPortfolioHistory(limit = 20): Promise<PortfolioSnapshotRecord[]> {
    return fetchJson<PortfolioSnapshotRecord[]>(
      `${this.baseUrl}/api/market/portfolio-history?limit=${encodeURIComponent(String(limit))}`,
    )
  }

  async getOperatorExtensions(status?: string): Promise<OperatorExtensionRecord[]> {
    const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
    return fetchJson<OperatorExtensionRecord[]>(`${this.baseUrl}/api/operator/extensions${suffix}`)
  }

  async getAgentRuns(): Promise<AgentRunSummary[]> {
    return fetchJson<AgentRunSummary[]>(`${this.baseUrl}/api/agent-runs`)
  }

  async postHeartbeat(heartbeat: OperatorHeartbeat): Promise<boolean> {
    await fetchJson(`${this.baseUrl}/api/operator/internal/heartbeat`, {
      method: 'POST',
      body: JSON.stringify(heartbeat),
    })
    return true
  }

  async createMessage(payload: OperatorMessageCreate): Promise<{ id: string }> {
    const message = await fetchJson<{ id: string }>(`${this.baseUrl}/api/operator/internal/messages`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    return { id: message.id }
  }

  async createTask(payload: OperatorTaskCreate): Promise<{ id: string }> {
    const task = await fetchJson<{ id: string }>(`${this.baseUrl}/api/operator/internal/tasks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    return { id: task.id }
  }

  async updateTask(taskId: string, payload: OperatorTaskUpdate): Promise<{ id: string }> {
    const task = await fetchJson<{ id: string }>(
      `${this.baseUrl}/api/operator/internal/tasks/${encodeURIComponent(taskId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
    )
    return { id: task.id }
  }

  async createEvent(payload: OperatorEventCreate): Promise<{ id: string }> {
    const event = await fetchJson<{ id: string }>(`${this.baseUrl}/api/operator/internal/events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    return { id: event.id }
  }

  async executeOperatorCommand(
    commandName: string,
    payload: OperatorCommandExecution,
  ): Promise<OperatorCommandExecutionResult> {
    return fetchJson<OperatorCommandExecutionResult>(
      `${this.baseUrl}/api/operator/internal/commands/execute`,
      {
        method: 'POST',
        body: JSON.stringify({
          command_name: commandName,
          ...payload,
        }),
      },
    )
  }

  async compactOperatorMemory(date: string): Promise<OperatorCompactionResult> {
    return fetchJson<OperatorCompactionResult>(
      `${this.baseUrl}/api/operator/internal/compact?date=${encodeURIComponent(date)}`,
      {
        method: 'POST',
      },
    )
  }

  async startAgentRun(agentName: string): Promise<AgentRunInvocationResult> {
    const queued = await fetchJson<{ run_id: string; status: string }>(`${this.baseUrl}/api/agent-runs/start`, {
      method: 'POST',
      body: JSON.stringify({
        agent_name: agentName,
      }),
    })
    const deadline = Date.now() + 5 * 60 * 1000
    while (Date.now() < deadline) {
      const run = await fetchJson<AgentRunSummary>(
        `${this.baseUrl}/api/agent-runs/${encodeURIComponent(queued.run_id)}`,
      )
      if (TERMINAL_AGENT_RUN_STATUSES.has(run.status)) {
        return {
          run_id: run.id,
          status: run.status,
          outputs: parseStringList(run.outputs_created),
          errors: parseStringList(run.errors),
        }
      }
      await new Promise(resolve => setTimeout(resolve, 250))
    }
    return {
      run_id: queued.run_id,
      status: 'timed_out',
      outputs: [],
      errors: ['Delegated agent run did not reach a terminal state within 5 minutes.'],
    }
  }
}
