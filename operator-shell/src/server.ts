import type { OperatorShellConfig } from './config.js'
import { OperatorLoop } from './operator_loop.js'

export class OperatorShellServer {
  private readonly loop: OperatorLoop

  constructor(config: OperatorShellConfig) {
    this.loop = new OperatorLoop(config)
  }

  async start(): Promise<void> {
    await this.loop.start()
  }

  async stop(): Promise<void> {
    await this.loop.stop()
  }
}
