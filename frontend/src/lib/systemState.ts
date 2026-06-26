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

export interface MasterStateInput {
  gatewayRunning: boolean
  killed: boolean
  paused: boolean
  /** From /risk/status .status — 'healthy' | 'warning' | 'blocked' | 'not_available' | … */
  riskState?: string
  /** From /operator/status .status — 'online' | 'stale' | 'offline' | undefined */
  operatorStatus?: string
}

export interface MasterState {
  word: string
  tone: MasterTone
  detail: string
}

/**
 * Derive the single master headline. Priority (top→bottom = most severe → least):
 *   1. Gateway down                → OFFLINE / red  (nothing can run)
 *   2. Kill switch engaged         → HALTED  / red
 *   3. Risk engine blocking orders → BLOCKED / red
 *   4. Operator process offline    → STALE   / red  (heartbeat dead)
 *   5. Operator heartbeat stale    → STALE   / yellow
 *   6. Scheduler paused            → PAUSED  / yellow
 *   7. Risk warning                → CAUTION / yellow
 *   8. Risk healthy                → OPERATIONAL / green
 *   9. Unknown/not evaluated       → STANDBY / blue
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
