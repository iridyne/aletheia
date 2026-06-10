export interface OperatorShellConfig {
  gatewayBaseUrl: string
  heartbeatIntervalSec: number
  permissionMode: string
  mainSessionId: string
  modelProvider: string
  once: boolean
}

function readInt(name: string, fallback: number): number {
  const raw = process.env[name]
  if (!raw) {
    return fallback
  }
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export function loadConfig(args: string[] = process.argv.slice(2)): OperatorShellConfig {
  return {
    gatewayBaseUrl: process.env.ALETHEIA_GATEWAY_BASE_URL || 'http://127.0.0.1:8787',
    heartbeatIntervalSec: readInt('ALETHEIA_OPERATOR_HEARTBEAT_SEC', 10),
    permissionMode: process.env.ALETHEIA_OPERATOR_PERMISSION_MODE || 'ReadOnly',
    mainSessionId: process.env.ALETHEIA_OPERATOR_MAIN_SESSION_ID || 'operator-main',
    modelProvider: process.env.ALETHEIA_OPERATOR_MODEL_PROVIDER || 'pi-shell',
    once: process.env.ALETHEIA_OPERATOR_ONCE === '1' || args.includes('--once'),
  }
}
