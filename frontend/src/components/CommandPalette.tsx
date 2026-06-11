import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Command } from 'cmdk'
import { api } from '../api/client'

interface CmdAction {
  id: string
  label: string
  group: string
  keywords?: string
  dangerous?: boolean
  action: () => unknown
}

export default function CommandPalette() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const qc = useQueryClient()

  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setOpen(prev => !prev)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const exec = useCallback((action: () => unknown) => {
    setOpen(false)
    action()
  }, [])

  const commands: CmdAction[] = [
    { id: 'nav-command-center', label: 'Open Command Center', group: 'Navigation', keywords: 'operator chat control room shell', action: () => navigate('/command-center') },
    { id: 'nav-overview', label: 'Go to Overview', group: 'Navigation', keywords: 'cockpit home dashboard', action: () => navigate('/') },
    { id: 'nav-charts', label: 'Go to Markets', group: 'Navigation', keywords: 'charts price', action: () => navigate('/charts') },
    { id: 'nav-news', label: 'Go to News Intel', group: 'Navigation', action: () => navigate('/news') },
    { id: 'nav-proposals', label: 'Go to Approval Desk', group: 'Navigation', keywords: 'proposals approve', action: () => navigate('/proposals') },
    { id: 'nav-agent-activity', label: 'Go to Agent Activity', group: 'Navigation', keywords: 'agent runs opportunities transparency', action: () => navigate('/agent-activity') },
    { id: 'nav-trades', label: 'Go to Trades & Positions', group: 'Navigation', keywords: 'paper orders fills positions blotter', action: () => navigate('/trades') },
    { id: 'nav-strategies', label: 'Go to Strategies', group: 'Navigation', action: () => navigate('/strategies') },
    { id: 'nav-learning', label: 'Go to Learning', group: 'Navigation', keywords: 'dreaming knowledge base reports memory consolidation', action: () => navigate('/learning') },
    { id: 'nav-risk', label: 'Go to Risk & Controls', group: 'Navigation', action: () => navigate('/risk') },
    { id: 'nav-settings', label: 'Go to Settings', group: 'Navigation', action: () => navigate('/settings') },

    { id: 'sys-pause', label: 'Pause Gateway', group: 'System', keywords: 'stop halt', action: () => pauseMut.mutate() },
    { id: 'sys-resume', label: 'Resume Gateway', group: 'System', keywords: 'start continue', action: () => resumeMut.mutate() },
    { id: 'sys-kill', label: 'Engage Kill Switch', group: 'System', dangerous: true, action: () => { if (confirm('Engage kill switch? All trading will halt.')) api.setKillSwitch(true, 'cmd_palette') } },
    { id: 'sys-unkill', label: 'Release Kill Switch', group: 'System', action: () => api.setKillSwitch(false) },

    { id: 'agent-observer', label: 'Run Market Observer', group: 'Agents', keywords: 'trigger start market', action: () => api.startAgentRun('market_observer') },
    { id: 'agent-research', label: 'Run Research Agent', group: 'Agents', keywords: 'trigger start research', action: () => api.startAgentRun('research_agent') },
    { id: 'agent-dreaming', label: 'Run Dreaming Agent', group: 'Agents', keywords: 'trigger start dream consolidate', action: () => api.startAgentRun('dreaming_agent') },
    { id: 'agent-paper', label: 'Run Paper Trader', group: 'Agents', keywords: 'trigger start paper trade', action: () => api.startAgentRun('paper_trader') },
  ]

  const groups = [...new Set(commands.map(c => c.group))]

  if (!open) return null

  return (
    <div className="cmd-backdrop" onClick={() => { setOpen(false) }}>
      <div className="cmd-dialog" onClick={e => e.stopPropagation()}>
        <Command
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const input = (e.target as HTMLInputElement)?.value?.trim()
              if (input) {
                e.preventDefault()
                setOpen(false)
                navigate(`/command-center?draft=${encodeURIComponent(input)}`)
              }
            }
          }}
        >
          <Command.Input
            className="cmd-input"
            placeholder="Jump somewhere, or type a draft for Command Center..."
            autoFocus
          />
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
                    onSelect={() => exec(cmd.action)}
                    className={`cmd-item ${cmd.dangerous ? 'dangerous' : ''}`}
                  >
                    <span>{cmd.label}</span>
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
