import type { OperatorHeartbeat } from './schemas.js'

export class BackendClient {
  constructor(private readonly baseUrl: string) {}

  async postHeartbeat(heartbeat: OperatorHeartbeat): Promise<boolean> {
    const response = await fetch(`${this.baseUrl}/api/operator/internal/heartbeat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(heartbeat),
    })
    if (!response.ok) {
      throw new Error(`heartbeat_failed:${response.status}`)
    }
    return true
  }
}
