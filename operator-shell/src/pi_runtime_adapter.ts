import type { BackendGatewayClient } from './schemas.js'
import { gatewayRequestFailureMessage } from './gateway_errors.js'
import { applyOperatorServiceHeader } from './service_auth.js'

export interface PiSessionRequest {
  goal: string
  sessionId?: string | null
  contextSections?: string[]
  allowedCommands?: string[]
  maxTurns?: number
}

export interface PiSessionResult {
  status: 'completed' | 'llm_unavailable' | 'max_turns_reached' | 'error'
  turnsUsed: number
  commandsExecuted: Array<{
    command: string
    permitted: boolean
    response?: string
    reason?: string
  }>
  finalAnswer: string | null
  error?: string
  learningManifest?: Record<string, unknown> | null
}

export class PiRuntimeAdapter {
  private readonly backendClient: BackendGatewayClient
  private readonly baseUrl: string

  constructor(backendClient: BackendGatewayClient, baseUrl: string) {
    this.backendClient = backendClient
    this.baseUrl = baseUrl
  }

  providerLabel(): string {
    return 'pi-shell'
  }

  async runSession(request: PiSessionRequest): Promise<PiSessionResult> {
    const payload = {
      goal: request.goal,
      session_id: request.sessionId || null,
      context_sections: request.contextSections || null,
      allowed_commands: request.allowedCommands || null,
      max_turns: request.maxTurns ?? 8,
    }
    const url = `${this.baseUrl}/api/operator/internal/llm-session`
    const init: RequestInit = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }
    const requestHeaders = applyOperatorServiceHeader(new Headers(init.headers))

    try {
      const response = await fetch(url, {
        ...init,
        headers: requestHeaders,
      })

      if (!response.ok) {
        return {
          status: 'error',
          turnsUsed: 0,
          commandsExecuted: [],
          finalAnswer: null,
          // Use the credential-free init for diagnostics so the service secret
          // can never enter an error message or persisted task result.
          error: await gatewayRequestFailureMessage(url, init, response),
        }
      }

      const data = await response.json() as Record<string, unknown>
      return {
        status: (data.status as PiSessionResult['status']) || 'error',
        turnsUsed: (data.turns_used as number) || 0,
        commandsExecuted: (data.commands_executed as PiSessionResult['commandsExecuted']) || [],
        finalAnswer: (data.final_answer as string) || null,
        error: (data.error as string) || undefined,
        learningManifest: data.learning_manifest && typeof data.learning_manifest === 'object'
          ? data.learning_manifest as Record<string, unknown>
          : null,
      }
    } catch (err) {
      return {
        status: 'error',
        turnsUsed: 0,
        commandsExecuted: [],
        finalAnswer: null,
        error: err instanceof Error ? err.message : 'unknown_error',
      }
    }
  }
}
