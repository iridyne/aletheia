import type {
  OperatorSessionBranchState,
  OperatorSessionDetail,
  OperatorSessionKind,
  OperatorSessionState,
  OperatorSessionSummary,
} from './schemas.js'

function messageText(content: Record<string, unknown> | string): string | null {
  if (typeof content === 'string') {
    return content.trim() || null
  }
  const text = typeof content.text === 'string' ? content.text.trim() : ''
  if (text) {
    return text
  }
  return null
}

function branchSummary(detail: OperatorSessionDetail): string | null {
  const sessionResponse = detail.session.response?.trim()
  if (sessionResponse) {
    return sessionResponse
  }
  const sessionIntent = detail.session.intent?.trim()
  if (sessionIntent) {
    return sessionIntent
  }

  const latestAssistantMessage = [...detail.messages]
    .reverse()
    .find(message => message.role === 'assistant' && message.message_type !== 'context')
  if (latestAssistantMessage) {
    const text = messageText(latestAssistantMessage.content)
    if (text) {
      return text
    }
  }

  const latestArtifact = [...detail.artifacts].reverse().find(artifact => artifact.artifact_type !== 'repair_decision')
  if (latestArtifact) {
    if (
      latestArtifact.content &&
      typeof latestArtifact.content === 'object' &&
      !Array.isArray(latestArtifact.content) &&
      typeof latestArtifact.content.summary === 'string' &&
      latestArtifact.content.summary.trim().length > 0
    ) {
      return latestArtifact.content.summary.trim()
    }
    return latestArtifact.title.trim() || null
  }

  return detail.session.title?.trim() || detail.session.user_input?.trim() || null
}

function sessionTimestamp(session: Pick<OperatorSessionSummary, 'created_at'>): number {
  const parsed = Date.parse(session.created_at)
  return Number.isNaN(parsed) ? 0 : parsed
}

function descendantSessionIds(
  mainSessionId: string,
  sessions: Array<Pick<OperatorSessionSummary, 'id' | 'parent_session_id'>>,
): Set<string> {
  const children = new Map<string, string[]>()
  for (const session of sessions) {
    const parent = session.parent_session_id || ''
    if (!parent) {
      continue
    }
    const items = children.get(parent) || []
    items.push(session.id)
    children.set(parent, items)
  }

  const descendants = new Set<string>()
  const stack = [...(children.get(mainSessionId) || [])]
  while (stack.length > 0) {
    const sessionId = stack.pop()
    if (!sessionId || descendants.has(sessionId)) {
      continue
    }
    descendants.add(sessionId)
    stack.push(...(children.get(sessionId) || []))
  }
  return descendants
}

export class SessionRuntime {
  private readonly mainSession: OperatorSessionState
  private readonly branches = new Map<string, OperatorSessionBranchState>()
  private activeBranchId: string | null = null

  constructor(sessionId: string, kind: OperatorSessionKind = 'command_session') {
    this.mainSession = {
      id: sessionId,
      kind,
      currentIntent: 'monitoring system state',
    }
  }

  getMainSession(): OperatorSessionState {
    return this.mainSession
  }

  setCurrentIntent(intent: string | null): void {
    this.mainSession.currentIntent = intent
  }

  restore(
    detail: OperatorSessionDetail | null,
    sessions: OperatorSessionSummary[] = [],
    branchDetails: OperatorSessionDetail[] = [],
    preferredActiveBranch:
      | Partial<OperatorSessionBranchState>
      | null = null,
  ): void {
    if (!detail) {
      return
    }
    this.mainSession.currentIntent =
      detail.session.intent ||
      detail.session.response ||
      this.mainSession.currentIntent
    this.branches.clear()
    this.activeBranchId = null

    const sessionRecords = new Map<string, OperatorSessionSummary>()
    for (const session of sessions) {
      sessionRecords.set(session.id, session)
    }
    for (const branch of branchDetails) {
      sessionRecords.set(branch.session.id, branch.session)
    }
    const descendantIds = descendantSessionIds(this.mainSession.id, [...sessionRecords.values()])
    const branchStates = new Map<string, OperatorSessionBranchState>()
    for (const branch of branchDetails) {
      if (!descendantIds.has(branch.session.id)) {
        continue
      }
      branchStates.set(branch.session.id, {
        id: branch.session.id,
        kind: (branch.session.kind as OperatorSessionKind | null) || 'command_session',
        title: branch.session.title || null,
        parent_session_id: branch.session.parent_session_id || null,
        summary: branchSummary(branch),
        created_at: branch.session.created_at,
      })
    }

    for (const session of sessions) {
      if (!descendantIds.has(session.id) || branchStates.has(session.id)) {
        continue
      }
      branchStates.set(session.id, {
        id: session.id,
        kind: (session.kind as OperatorSessionKind | null) || 'command_session',
        title: session.title || null,
        parent_session_id: session.parent_session_id || null,
        summary: session.response || session.intent || session.title || session.user_input,
        created_at: session.created_at,
      })
    }

    const orderedBranches = [...branchStates.values()].sort(
      (left, right) =>
        sessionTimestamp({ created_at: right.created_at || '' }) -
        sessionTimestamp({ created_at: left.created_at || '' }),
    )
    for (const branch of orderedBranches) {
      this.branches.set(branch.id, branch)
    }

    const preferredActiveBranchId =
      typeof preferredActiveBranch?.id === 'string' && preferredActiveBranch.id.trim().length > 0
        ? preferredActiveBranch.id.trim()
        : null
    if (preferredActiveBranchId && preferredActiveBranch) {
      const preferredBranch = preferredActiveBranch
      const existingBranch = this.branches.get(preferredActiveBranchId)
      if (existingBranch) {
        this.branches.set(preferredActiveBranchId, {
          ...existingBranch,
          kind:
            existingBranch.kind ||
            (preferredBranch.kind as OperatorSessionKind | null) ||
            'command_session',
          title: existingBranch.title || preferredBranch.title || null,
          summary: existingBranch.summary || preferredBranch.summary || null,
          parent_session_id: existingBranch.parent_session_id || this.mainSession.id,
          created_at: existingBranch.created_at || preferredBranch.created_at || null,
        })
      } else {
        this.branches.set(preferredActiveBranchId, {
          id: preferredActiveBranchId,
          kind: (preferredBranch.kind as OperatorSessionKind | null) || 'command_session',
          title: preferredBranch.title || null,
          parent_session_id: this.mainSession.id,
          summary: preferredBranch.summary || null,
          created_at: preferredBranch.created_at || null,
        })
      }
      this.activeBranchId = preferredActiveBranchId
      return
    }

    this.activeBranchId = orderedBranches[0]?.id || null
  }

  rememberBranch(branch: OperatorSessionBranchState): void {
    this.branches.set(branch.id, branch)
    this.activeBranchId = branch.id
  }

  selectBranch(branch: Partial<OperatorSessionBranchState> | null): void {
    const branchId = typeof branch?.id === 'string' ? branch.id.trim() : ''
    if (!branchId) {
      this.clearActiveBranch()
      return
    }
    const selectedBranch = branch as Partial<OperatorSessionBranchState>

    const existing = this.branches.get(branchId)
    this.branches.set(branchId, {
      id: branchId,
      kind:
        existing?.kind ||
        (selectedBranch.kind as OperatorSessionKind | null) ||
        'command_session',
      title: existing?.title || selectedBranch.title || null,
      parent_session_id:
        existing?.parent_session_id || selectedBranch.parent_session_id || this.mainSession.id,
      summary: existing?.summary || selectedBranch.summary || null,
      created_at: existing?.created_at || selectedBranch.created_at || null,
    })
    this.activeBranchId = branchId
  }

  clearActiveBranch(): void {
    this.activeBranchId = null
  }

  getActiveBranch(): OperatorSessionBranchState | null {
    if (!this.activeBranchId) {
      return null
    }
    return this.branches.get(this.activeBranchId) || null
  }

  getBranches(): OperatorSessionBranchState[] {
    return [...this.branches.values()]
  }
}
