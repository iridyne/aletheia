import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Command } from 'cmdk'
import { api } from '../api/client'
import { useConfirm } from './ui'

interface CmdAction {
  id: string
  label: string
  group: string
  keywords?: string
  dangerous?: boolean
  action: () => unknown | Promise<unknown>
}

type CommandActionResult = { ok: true } | { ok: false; error: string }

type KillSwitchApiFn = (active: boolean, source: string, confirmRelease?: boolean) => Promise<{ active: boolean }>
type RefetchFn = (filters: { queryKey: unknown[] }, opts: { throwOnError: boolean }) => Promise<unknown>
type InvalidateFn = (filters: { queryKey: unknown[] }) => Promise<void>

/** Exported for unit tests: execute the kill-switch transition and refetch authority. */
export async function executeKillSwitch(
  active: boolean,
  setKillSwitchApi: KillSwitchApiFn,
  refetch: RefetchFn,
  invalidate: InvalidateFn,
): Promise<void> {
  const confirmed = await setKillSwitchApi(active, 'cmd_palette', !active)
  if (active && confirmed.active !== true) throw new Error('Kill switch engage was not confirmed by backend')
  if (!active && confirmed.active !== false) throw new Error('Kill switch release was not confirmed by backend')
  await Promise.all([
    refetch({ queryKey: ['kill-switch'] }, { throwOnError: true }),
    refetch({ queryKey: ['risk-status'] }, { throwOnError: true }),
    invalidate({ queryKey: ['ui-shell-bootstrap'] }),
  ])
}

export async function runCommandAction(
  action: () => unknown | Promise<unknown>,
): Promise<CommandActionResult> {
  try {
    await action()
    return { ok: true }
  } catch (error) {
    if (error instanceof Error && error.message) {
      return { ok: false, error: error.message }
    }
    if (typeof error === 'string' && error) {
      return { ok: false, error }
    }
    return { ok: false, error: 'request failed' }
  }
}

export default function CommandPalette({ initialOpen = false }: { initialOpen?: boolean }) {
  const [open, setOpen] = useState(initialOpen)
  const [commandError, setCommandError] = useState('')
  const [executingCommand, setExecutingCommand] = useState<string | null>(null)
  const executingRef = useRef(false)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        if (!executingRef.current) setOpen(prev => !prev)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const exec = useCallback(async (command: CmdAction) => {
    if (executingRef.current) return
    executingRef.current = true
    setExecutingCommand(command.id)
    setCommandError('')

    const result = await runCommandAction(command.action)
    if (result.ok) {
      setOpen(false)
    } else {
      setCommandError(`${command.label}: ${result.error}`)
    }

    executingRef.current = false
    setExecutingCommand(null)
  }, [])

  const setKillSwitch = (active: boolean) =>
    executeKillSwitch(active, api.setKillSwitch, qc.refetchQueries.bind(qc), qc.invalidateQueries.bind(qc))

  const setGatewayPaused = async (paused: boolean) => {
    await (paused ? api.pause() : api.resume())
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['status'] }),
      qc.invalidateQueries({ queryKey: ['ui-shell-bootstrap'] }),
    ])
  }

  const startAgent = async (agentName: string) => {
    await api.startAgentRun(agentName)
    await qc.invalidateQueries({ queryKey: ['agent-runs'] })
  }

  const commands: CmdAction[] = [
    { id: 'nav-command-center', label: 'Open Command Center', group: 'Navigation', keywords: 'operator chat control room shell', action: () => navigate('/command-center') },
    { id: 'nav-overview', label: 'Go to Overview', group: 'Navigation', keywords: 'cockpit home dashboard', action: () => navigate('/') },
    { id: 'nav-charts', label: 'Go to Markets', group: 'Navigation', keywords: 'charts price', action: () => navigate('/charts') },
    { id: 'nav-news', label: 'Go to News Intel', group: 'Navigation', action: () => navigate('/news') },
    { id: 'nav-serenity-lens', label: 'Go to Serenity Lens', group: 'Navigation', keywords: 'research lens chokepoint opportunities', action: () => navigate('/serenity-lens') },
    { id: 'nav-prediction', label: 'Go to Prediction Markets', group: 'Navigation', keywords: 'prediction polymarket probability events', action: () => navigate('/prediction-markets') },
    { id: 'nav-blotter', label: 'Go to Blotter', group: 'Navigation', keywords: 'blotter proposals approve sign-off candidates history', action: () => navigate('/blotter') },
    { id: 'nav-agent-activity', label: 'Go to Agent Activity', group: 'Navigation', keywords: 'agent runs opportunities transparency', action: () => navigate('/agent-activity') },
    // #125 fix: /universe was reachable via the NAV bar but missing from the
    // command palette — an operator using the keyboard shortcut couldn't
    // navigate to the Universe Board.
    { id: 'nav-universe', label: 'Go to Universe Board', group: 'Navigation', keywords: 'universe scanner instruments', action: () => navigate('/universe') },
    { id: 'nav-trades', label: 'Go to Trades & Positions', group: 'Navigation', keywords: 'paper orders fills positions blotter', action: () => navigate('/trades') },
    { id: 'nav-learning', label: 'Go to Learning', group: 'Navigation', keywords: 'dreaming knowledge base reports memory consolidation', action: () => navigate('/learning') },
    { id: 'nav-risk', label: 'Go to Risk & Controls', group: 'Navigation', action: () => navigate('/risk') },
    { id: 'nav-settings', label: 'Go to Settings', group: 'Navigation', action: () => navigate('/settings') },

    { id: 'sys-pause', label: 'Pause Gateway', group: 'System', keywords: 'stop halt', action: () => setGatewayPaused(true) },
    { id: 'sys-resume', label: 'Resume Gateway', group: 'System', keywords: 'start continue', action: () => setGatewayPaused(false) },
    { id: 'sys-kill', label: 'Engage Kill Switch', group: 'System', dangerous: true, action: async () => { if (await confirm({ title: 'Engage kill switch?', description: 'All trading will halt.', confirmLabel: 'Engage', variant: 'danger' })) await setKillSwitch(true) } },
    { id: 'sys-unkill', label: 'Release Kill Switch', group: 'System', action: async () => { if (await confirm({ title: 'Release kill switch?', description: 'Trading will resume immediately.', confirmLabel: 'Release', variant: 'warning' })) await setKillSwitch(false) } },

    { id: 'agent-observer', label: 'Run Market Observer', group: 'Agents', keywords: 'trigger start market', action: () => startAgent('market_observer') },
    { id: 'agent-research', label: 'Run Research Agent', group: 'Agents', keywords: 'trigger start research', action: () => startAgent('research_agent') },
    { id: 'agent-dreaming', label: 'Run Dreaming Agent', group: 'Agents', keywords: 'trigger start dream consolidate', action: () => startAgent('dreaming_agent') },
    { id: 'agent-paper', label: 'Run Paper Trader', group: 'Agents', keywords: 'trigger start paper trade', action: () => startAgent('paper_trader') },
  ]

  const groups = [...new Set(commands.map(c => c.group))]

  if (!open) return null

  return (
    <div className="cmd-backdrop" onClick={() => { if (!executingRef.current) setOpen(false) }}>
      <div className="cmd-dialog" onClick={e => e.stopPropagation()}>
        <Command
          onKeyDown={(e) => {
            // #126 fix: previously ANY non-empty input intercepted Enter and
            // drafted a Command Center message — even a 3-char search that
            // happened to match a suggestion. cmdk couldn't fire its own
            // Enter selection because our handler had already navigated
            // away. Only hijack for LONG inputs that look like a real
            // freeform prompt (≥20 chars), matching the placeholder's
            // "20+ chars for AI" hint. Short inputs fall through to cmdk
            // so the highlighted suggestion selects normally.
            if (e.key === 'Enter') {
              const input = (e.target as HTMLInputElement)?.value?.trim() || ''
              if (input.length >= 20) {
                e.preventDefault()
                setOpen(false)
                navigate(`/command-center?draft=${encodeURIComponent(input)}`)
              }
            }
          }}
        >
          <Command.Input
            className="cmd-input"
            placeholder="Jump somewhere, or type a natural-language command (20+ chars) for AI..."
            autoFocus
          />
          {commandError && <div className="lever-err" role="alert">{commandError}</div>}
          <Command.List className="cmd-list">
            <Command.Empty className="cmd-empty">
              Press Enter to open Command Center with this draft
            </Command.Empty>
            {groups.map(group => (
              <Command.Group key={group} heading={group} className="cmd-group">
                {commands.filter(c => c.group === group).map(cmd => (
                  <Command.Item
                    key={cmd.id}
                    value={`${cmd.label} ${cmd.keywords || ''}`}
                    onSelect={() => { void exec(cmd) }}
                    className={`cmd-item ${cmd.dangerous ? 'dangerous' : ''}`}
                    disabled={executingCommand !== null}
                  >
                    <span>{cmd.label}{executingCommand === cmd.id ? '…' : ''}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            ))}
          </Command.List>
        </Command>
      </div>
    </div>
  )
}
