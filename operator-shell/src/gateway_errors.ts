function requestPath(url: string): string {
  try {
    const parsed = new URL(url)
    return `${parsed.pathname}${parsed.search}`
  } catch {
    return url
  }
}

function compactText(value: string, limit = 180): string {
  const compacted = value.replace(/\s+/g, ' ').trim()
  return compacted.length > limit ? `${compacted.slice(0, limit)}…` : compacted
}

async function responseDetail(response: Response): Promise<string | null> {
  let text = ''
  try {
    text = await response.text()
  } catch {
    return null
  }
  if (!text.trim()) {
    return null
  }
  try {
    const parsed = JSON.parse(text) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>
      const detail = record.detail ?? record.error ?? record.message
      if (typeof detail === 'string') {
        return compactText(detail)
      }
    }
  } catch {
    // Fall through to compact raw text.
  }
  return compactText(text)
}

export async function gatewayRequestFailureMessage(
  url: string,
  init: RequestInit | undefined,
  response: Response,
): Promise<string> {
  const method = String(init?.method || 'GET').toUpperCase()
  const detail = await responseDetail(response)
  const prefix = `gateway_request_failed:${response.status}:${method} ${requestPath(url)}`
  return detail ? `${prefix}:${detail}` : prefix
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function isGatewayInfrastructureError(message: string | null | undefined): boolean {
  if (!message) {
    return false
  }
  const normalized = message.toLowerCase()
  return (
    normalized.startsWith('gateway_request_failed:') ||
    normalized === 'fetch failed' ||
    normalized.includes('fetch failed') ||
    normalized.includes('econnrefused') ||
    normalized.includes('connection refused')
  )
}

export function isTaskNotFoundError(message: string | null | undefined): boolean {
  if (!message) return false
  return message.includes('gateway_request_failed:404') && message.includes('/tasks/')
}

export function operatorIntentForError(message: string | null | undefined): string {
  if (isGatewayInfrastructureError(message)) {
    return 'gateway connectivity recovery pending'
  }
  const cleaned = message?.trim()
  return cleaned || 'operator loop failure; review logs'
}
