const AUTHORIZATION_VALUE = /\bauthorization\b\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+/gi
const SENSITIVE_ASSIGNMENT = /\b(api[_ -]?key|api[_ -]?secret|cookie|passphrase|password|secret|token)\b\s*[:=]\s*[^\s,;]+/gi
const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi

export const MUTATION_ERROR_EVENT = 'aletheia:mutation-error'

/** Keep operator-facing errors useful without echoing credentials or tokens. */
export function safeErrorText(value: unknown, fallback = 'request failed'): string {
  const raw = value instanceof Error
    ? value.message
    : typeof value === 'string'
      ? value
      : null
  const text = raw?.trim() || fallback
  return text
    .replace(AUTHORIZATION_VALUE, 'Authorization: [redacted]')
    .replace(SENSITIVE_ASSIGNMENT, (_match, key: string) => `${key}: [redacted]`)
    .replace(BEARER_TOKEN, 'Bearer [redacted]')
    .slice(0, 240)
}

/** Publish failures from mutations that do not have a local error surface. */
export function reportMutationError(error: unknown): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(MUTATION_ERROR_EVENT, {
    detail: { message: `Action failed: ${safeErrorText(error)}` },
  }))
}
