export const OPERATOR_SERVICE_TOKEN_ENV = 'ALETHEIA_OPERATOR_SERVICE_TOKEN'
export const OPERATOR_SERVICE_HEADER = 'X-Aletheia-Operator-Service'

export function applyOperatorServiceHeader(headers: Headers): Headers {
  const token = process.env[OPERATOR_SERVICE_TOKEN_ENV]?.trim()
  if (token) {
    headers.set(OPERATOR_SERVICE_HEADER, token)
  }
  return headers
}
