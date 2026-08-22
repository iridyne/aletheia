export interface ParsedIssue {
  key: string
  severity: 'red' | 'yellow'
  label: string
  value: string
  detail: string
  hint: string
}

function prefixedValue(raw: string, prefix: string): string {
  return raw.slice(prefix.length)
}

export function parseHealthIssue(raw: string): ParsedIssue {
  if (raw === 'kill_switch_active') {
    return {
      key: raw,
      severity: 'red',
      label: 'Kill switch',
      value: 'active',
      detail: 'All trading is blocked.',
      hint: 'Open Settings → Risk → Kill Switch to release.',
    }
  }
  if (raw.startsWith('health_check_error:')) {
    const value = prefixedValue(raw, 'health_check_error:')
    return { key: raw, severity: 'red', label: 'Health check error', value, detail: value, hint: 'Check gateway health details and logs.' }
  }
  if (raw.startsWith('code_updated_pending_restart:')) {
    const value = prefixedValue(raw, 'code_updated_pending_restart:')
    return { key: raw, severity: 'yellow', label: 'Code updated', value, detail: value, hint: 'Restart the gateway to load the checked-out code.' }
  }
  if (raw.startsWith('agent_failure_ratio:')) {
    const value = prefixedValue(raw, 'agent_failure_ratio:')
    return { key: raw, severity: 'red', label: 'Agent failure ratio', value, detail: value, hint: 'Check gateway logs for the failing agents.' }
  }
  if (raw.startsWith('market_data_stale:')) {
    const value = prefixedValue(raw, 'market_data_stale:')
    return { key: raw, severity: 'yellow', label: 'Market data stale', value, detail: value, hint: 'Feed refreshes every 5 minutes; restart the gateway if it stays stale for 30 minutes.' }
  }
  if (raw.startsWith('llm_unavailable:')) {
    const value = prefixedValue(raw, 'llm_unavailable:')
    return { key: raw, severity: 'red', label: 'LLM unavailable', value, detail: value, hint: 'Check Settings → Model Provider, then resume the operator.' }
  }
  if (raw === 'trading_paused') {
    return { key: raw, severity: 'yellow', label: 'Trading loop', value: 'paused', detail: '', hint: 'Use the Resume control in the top bar to continue.' }
  }
  return { key: raw, severity: 'yellow', label: 'Health issue', value: 'attention', detail: raw, hint: 'Review the system health details.' }
}

export function parseAllIssues(issues: string[]): ParsedIssue[] {
  return (issues || []).map(parseHealthIssue)
}

const _SEVERITY_ORDER: Record<string, number> = { red: 1, yellow: 0 }
const _LABEL_PRIORITY: Record<string, number> = {
  'kill_switch_active': 10,
  'health_check_error:': 9,
  'code_updated_pending_restart:': 4,
  'agent_failure_ratio:': 3,
  'llm_unavailable:': 3,
  'market_data_stale:': 2,
  'trading_paused': 2,
}

function _issuePriority(issue: ParsedIssue): number {
  const sevScore = (_SEVERITY_ORDER[issue.severity] ?? 0) * 100
  for (const [prefix, score] of Object.entries(_LABEL_PRIORITY)) {
    if (issue.key.startsWith(prefix) || issue.key === prefix) return sevScore + score
  }
  return sevScore
}

export function sortedIssues(issues: ParsedIssue[]): ParsedIssue[] {
  return [...issues].sort((a, b) => _issuePriority(b) - _issuePriority(a))
}
