import type { OperatorMode } from './schemas.js'
import { BackendClient } from './backend_client.js'
import type { OperatorShellConfig } from './config.js'
import { buildHeartbeat } from './heartbeat.js'
import { SessionRuntime } from './session_runtime.js'
import { TaskRuntime } from './task_runtime.js'
import { EventRouter } from './event_router.js'
import { RecommendationEngine } from './recommendation_engine.js'
import { RepairEngine } from './repair_engine.js'

export class OperatorLoop {
  private readonly backendClient: BackendClient
  private readonly sessionRuntime: SessionRuntime
  private readonly taskRuntime = new TaskRuntime()
  private readonly eventRouter = new EventRouter()
  private readonly repairEngine = new RepairEngine()
  private readonly recommendationEngine = new RecommendationEngine()
  private intervalHandle: NodeJS.Timeout | null = null
  private mode: OperatorMode = 'Observing'
  private lastError: string | null = null
  private lastSuccessAt: string | null = null

  constructor(private readonly config: OperatorShellConfig) {
    this.backendClient = new BackendClient(config.gatewayBaseUrl)
    this.sessionRuntime = new SessionRuntime(config.mainSessionId)
  }

  async runOnce(): Promise<boolean> {
    try {
      this.eventRouter.route()
      this.repairEngine.evaluate()
      this.recommendationEngine.list()
      const heartbeat = buildHeartbeat({
        mode: this.mode,
        permissionMode: this.config.permissionMode,
        session: this.sessionRuntime.getMainSession(),
        tasks: this.taskRuntime.snapshot(),
        modelProvider: this.config.modelProvider,
        lastError: this.lastError,
        lastSuccessAt: this.lastSuccessAt,
      })
      await this.backendClient.postHeartbeat(heartbeat)
      this.lastSuccessAt = heartbeat.last_seen_at
      this.lastError = null
      return true
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error)
      return false
    }
  }

  async start(): Promise<void> {
    if (this.config.once) {
      const ok = await this.runOnce()
      if (!ok) {
        process.exitCode = 1
      }
      return
    }

    await this.runOnce()
    this.intervalHandle = setInterval(() => {
      void this.runOnce()
    }, this.config.heartbeatIntervalSec * 1000)
  }

  async stop(): Promise<void> {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle)
      this.intervalHandle = null
    }
  }
}
