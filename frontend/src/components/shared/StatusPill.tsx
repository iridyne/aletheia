type StatusPillProps = {
  status: string
  variant?: 'green' | 'red' | 'yellow' | 'blue' | 'purple' | 'muted'
  dot?: boolean
}

const AUTO_VARIANTS: Record<string, StatusPillProps['variant']> = {
  running: 'green', active: 'green', completed: 'green', approved: 'green', open: 'green', passed: 'green',
  stopped: 'red', failed: 'red', rejected: 'red', error: 'red', blocked: 'red',
  paused: 'yellow', pending: 'yellow', draft: 'yellow', warning: 'yellow',
  live_disabled: 'muted', retired: 'muted', archived: 'muted',
}

export default function StatusPill({ status, variant, dot }: StatusPillProps) {
  const v = variant || AUTO_VARIANTS[status] || 'blue'
  return (
    <span className={`pill pill-${v}`}>
      {dot && <span className={`dot dot-${v}`} />}
      {status}
    </span>
  )
}
