import type { OperatorTaskState } from './schemas.js'

export class TaskRuntime {
  private state: OperatorTaskState = {
    currentTaskId: null,
    queueSize: 0,
  }

  snapshot(): OperatorTaskState {
    return this.state
  }
}
