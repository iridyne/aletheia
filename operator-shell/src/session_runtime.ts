import type { OperatorSessionKind, OperatorSessionState } from './schemas.js'

export class SessionRuntime {
  private readonly mainSession: OperatorSessionState

  constructor(sessionId: string, kind: OperatorSessionKind = 'command_session') {
    this.mainSession = {
      id: sessionId,
      kind,
      currentIntent: 'monitoring system state',
    }
  }

  getMainSession(): OperatorSessionState {
    return this.mainSession
  }

  setCurrentIntent(intent: string | null): void {
    this.mainSession.currentIntent = intent
  }
}
