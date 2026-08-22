/**
 * Single-source master system state — used by topbar StatusBar AND Risk console band,
 * so the same signals never produce two different colors on two pages (a UI honesty bug
 * caught in audit). Project rule: same signal → same color, everywhere.
 *
 * The priority ladder is severity-first: any failing higher-priority condition wins,
 * even if a lower condition would also fire. Components beside the master word
 * (kill switch dot, scheduler dot, etc.) still render their own sub-state, so this
 * function SUMMARIZES without hiding.
 */

export type MasterTone = 'green' | 'yellow' | 'red' | 'blue' | 'muted'

export type SemanticState = 'active' | 'degraded' | 'loading' | 'unknown' | 'unavailable' | 'disabled' | 'blocked'
export type SemanticTone = 'green' | 'yellow' | 'red' | 'muted'

/** Keep state colors consistent across status surfaces. Loading/unknown/disabled are not incidents. */
export function semanticTone(state: SemanticState): SemanticTone {
  if (state === 'active') return 'green'
  if (state === 'degraded') return 'yellow'
  if (state === 'unavailable' || state === 'blocked') return 'red'
  return 'muted'
}

export function connectivityTone(state?: string | null): SemanticTone {
  if (state === 'reachable') return semanticTone('active')
  if (state === 'unavailable') return semanticTone('unavailable')
  if (state === 'recovering') return semanticTone('degraded')
  return semanticTone('unknown')
}

export interface MasterStateInput {
  gatewayRunning: boolean
  killed: boolean
  paused: boolean
  /** From /risk/status .status — 'healthy' | 'warning' | 'blocked' | 'not_available' | … */
  riskState?: string
  /** From /operator/status .status — 'online' | 'stale' | 'offline' | undefined */
  operatorStatus?: string
  /** From /operator/status .mode — used to surface trade-blocking modes at master level */
  operatorMode?: string
  /**
   * True when the operator is online but the trading daemon is not evaluating due to a
   * gateway health issue (e.g. llm_unavailable). Derived from trading_daemon.evaluating
   * and current_intent containing 'Gateway health issue'.
   */
  operatorTradingBlocked?: boolean
  /** Human-readable detail from operator current_intent, shown in the blocked detail. */
  operatorBlockedDetail?: string
}

// ─── Operator display status ───────────────────────────────────────────────
// Internal Operator modes are implementation details; user-facing UI shows only
// these three states. Raw mode names are relegated to debug/log sections.

export type OperatorDisplayState = 'Running' | 'Blocked' | 'Offline'

export interface OperatorDisplayStatus {
  state: OperatorDisplayState
  tone: 'green' | 'yellow' | 'red'
  /** Raw internal mode — for debug display only, not primary UI */
  debugMode: string
  /**
   * Human-readable explanation of why the Operator is blocked.
   * Only set when state === 'Blocked'. Must describe the concrete problem
   * so the user understands what happened without knowing internal mode names.
   */
  blockedDetail?: string
}

// Modes that prevent the Operator from generating new trade proposals.
const _TRADE_BLOCKING_MODES = new Set<string>(['Repairing', 'Degraded', 'Paused'])

const _BLOCKED_MODE_DETAILS: Record<string, string> = {
  Repairing:
    'A maintenance task is queued but cannot run — the Operator lacks the required permission to execute it. ' +
    'New trade proposals will not be generated until the task is resolved or cleared. ' +
    'The task will eventually time out and self-clear, or you can restart the Operator to force a reset.',
  Degraded:
    'The Operator has detected high-severity issues and suspended normal trading. ' +
    'Check the Recommendations panel for the specific violations that triggered this state.',
  Paused:
    'The Operator has been manually paused. Resume it from the Command Center or via the pause toggle to restore normal trading.',
}

/**
 * Map an Operator heartbeat to a simple three-state user-facing status.
 * Internal mode names (Repairing, Reviewing, Dreaming, …) go into debugMode
 * and should only appear in developer/debug sections of the UI.
 */
export function getOperatorDisplayStatus(heartbeat: {
  status?: string | null
  mode?: string | null
} | null | undefined): OperatorDisplayStatus {
  const debugMode = heartbeat?.mode ?? '—'
  if (!heartbeat || heartbeat.status === 'offline') {
    return { state: 'Offline', tone: 'red', debugMode }
  }
  if (heartbeat.status === 'stale') {
    return { state: 'Offline', tone: 'yellow', debugMode }
  }
  const mode = heartbeat.mode ?? ''
  if (_TRADE_BLOCKING_MODES.has(mode)) {
    return {
      state: 'Blocked',
      tone: 'yellow',
      debugMode,
      blockedDetail: _BLOCKED_MODE_DETAILS[mode] ?? 'The Operator is blocked and cannot generate new trade proposals.',
    }
  }
  return { state: 'Running', tone: 'green', debugMode }
}

export interface MasterState {
  word: string
  tone: MasterTone
  detail: string
}

/**
 * Derive the single master headline. Priority (top→bottom = most severe → least):
 *   1. Gateway down                → OFFLINE  / red
 *   2. Kill switch engaged         → HALTED   / red
 *   3. Risk engine blocking orders → BLOCKED  / red
 *   4. Operator process offline    → OFFLINE  / red  (heartbeat dead)
 *   5. Operator heartbeat stale    → STALE    / yellow
 *   6. Operator in blocking mode   → BLOCKED  / yellow (Repairing / Degraded)
 *   7. Scheduler paused            → PAUSED   / yellow
 *   8. Risk warning                → CAUTION  / yellow
 *   9. Risk healthy                → OPERATIONAL / green
 *  10. Unknown/not evaluated       → STANDBY  / blue
 */
export function deriveMasterState(p: MasterStateInput): MasterState {
  if (!p.gatewayRunning) {
    return { word: 'OFFLINE', tone: 'red', detail: 'Gateway is stopped — no agents are running.' }
  }
  if (p.killed) {
    return { word: 'HALTED', tone: 'red', detail: 'Kill switch engaged. All trading is stopped until released.' }
  }
  if (p.riskState === 'blocked') {
    return { word: 'BLOCKED', tone: 'red', detail: 'Risk engine is rejecting new orders. Clear the violations below.' }
  }
  if (p.operatorStatus === 'offline') {
    return { word: 'OFFLINE', tone: 'red', detail: 'Operator process is not reachable. Check gateway logs.' }
  }
  if (p.operatorStatus === 'stale') {
    return { word: 'STALE', tone: 'yellow', detail: 'Operator heartbeat is delayed — supervisor will refresh; usually clears in 1–2 min.' }
  }
  if (p.operatorMode && _TRADE_BLOCKING_MODES.has(p.operatorMode)) {
    return { word: 'BLOCKED', tone: 'yellow', detail: 'Operator cannot generate new trade proposals. Check the Operator panel for details.' }
  }
  if (p.operatorTradingBlocked) {
    const detail = p.operatorBlockedDetail
      ? `Operator is online but not evaluating signals: ${p.operatorBlockedDetail}`
      : 'Operator is online but the trading daemon is not evaluating signals. Check the Operator panel.'
    return { word: 'BLOCKED', tone: 'yellow', detail }
  }
  if (p.paused) {
    return { word: 'PAUSED', tone: 'yellow', detail: 'Scheduler is paused. Agents are idle until resumed.' }
  }
  if (p.riskState === 'warning') {
    return { word: 'CAUTION', tone: 'yellow', detail: 'Running with active risk warnings — review the violations below.' }
  }
  if (p.riskState === 'healthy') {
    return { word: 'OPERATIONAL', tone: 'green', detail: 'Agents are running within all risk limits.' }
  }
  return { word: 'STANDBY', tone: 'blue', detail: 'Risk state has not been evaluated yet.' }
}
