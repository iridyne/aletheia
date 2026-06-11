import type { BackendGatewayClient, OperatorRecommendation } from './schemas.js'

export class EventRouter {
  private readonly activeRecommendationIds = new Set<string>()

  constructor(private readonly backendClient: Pick<BackendGatewayClient, 'createEvent'>) {}

  async route(recommendations: OperatorRecommendation[]): Promise<void> {
    const currentIds = new Set<string>()
    for (const recommendation of recommendations) {
      if (!recommendation.eventType) {
        continue
      }
      currentIds.add(recommendation.id)
      if (this.activeRecommendationIds.has(recommendation.id)) {
        continue
      }
      await this.backendClient.createEvent({
        event_type: recommendation.eventType,
        severity: recommendation.severity,
        source: 'operator_loop',
        related_entity_type: 'recommendation',
        related_entity_id: recommendation.id,
        payload: {
          kind: recommendation.kind,
          summary: recommendation.summary,
          ...(recommendation.metadata || {}),
        },
      })
    }
    this.activeRecommendationIds.clear()
    for (const id of currentIds) {
      this.activeRecommendationIds.add(id)
    }
  }
}
