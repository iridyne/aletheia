export interface ParsedIssue {
  key: string
  severity: 'red' | 'yellow'
  label: string
  detail: string
}

export function parseHealthIssue(raw: string): ParsedIssue {
  if (raw === 'kill_switch_active') {
    return { key: raw, severity: 'red', label: 'Kill switch 已激活 — 交易暂停', detail: '' }
  }
  if (raw.startsWith('health_check_error:')) {
    return { key: raw, severity: 'red', label: '健康检查异常', detail: raw.slice('health_check_error:'.length) }
  }
  if (raw.startsWith('code_updated_pending_restart:')) {
    return { key: raw, severity: 'yellow', label: '代码已更新，待重启', detail: raw.slice('code_updated_pending_restart:'.length) }
  }
  if (raw.startsWith('agent_failure_ratio:')) {
    return { key: raw, severity: 'yellow', label: 'Agent 失败率过高', detail: raw.slice('agent_failure_ratio:'.length) }
  }
  if (raw.startsWith('market_data_stale:')) {
    return { key: raw, severity: 'yellow', label: '行情数据过期', detail: raw.slice('market_data_stale:'.length) }
  }
  if (raw.startsWith('llm_unavailable:')) {
    return { key: raw, severity: 'yellow', label: 'LLM 不可用', detail: `连续失败 ${raw.slice('llm_unavailable:'.length)} 次` }
  }
  if (raw === 'trading_paused') {
    return { key: raw, severity: 'yellow', label: '交易循环已暂停', detail: '' }
  }
  return { key: raw, severity: 'yellow', label: raw, detail: '' }
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
