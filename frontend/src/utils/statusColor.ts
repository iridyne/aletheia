const GREEN = ['completed', 'approved', 'running', 'active', 'open', 'passed', 'filled', 'connected']
const RED = ['rejected', 'failed', 'blocked', 'stopped', 'error', 'critical']
const YELLOW = ['pending', 'draft', 'warning', 'paused', 'risk_checking']
const PURPLE = ['dreaming', 'learning', 'researching']

export function badgeClass(status: string): string {
  const s = status.toLowerCase()
  if (GREEN.includes(s)) return 'badge-green'
  if (RED.includes(s)) return 'badge-red'
  if (YELLOW.includes(s)) return 'badge-yellow'
  if (PURPLE.includes(s)) return 'badge-purple'
  return 'badge-blue'
}

export function pnlClass(value: number | null | undefined): string {
  return (value ?? 0) >= 0 ? 'pnl-positive' : 'pnl-negative'
}
