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

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
    },
    ...init,
  })
  if (!response.ok) {
    throw new Error(`gateway_request_failed:${response.status}`)
  }
  return response.json() as Promise<T>
}

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

  async getOperatorTasks(status?: string): Promise<PersistedOperatorTask[]> {
    const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
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
    return fetchJson<AgentRunInvocationResult>(`${this.baseUrl}/api/agent-runs/start`, {
      method: 'POST',
      body: JSON.stringify({
        agent_name: agentName,
      }),
    })
  }
}
