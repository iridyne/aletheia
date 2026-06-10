export interface RoutedEvent {
  type: string
  payload: Record<string, unknown>
}

export class EventRouter {
  route(): RoutedEvent[] {
    return []
  }
}
