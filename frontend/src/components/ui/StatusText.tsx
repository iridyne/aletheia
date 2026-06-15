import type { HTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

export type StatusTone = 'green' | 'red' | 'yellow' | 'blue' | 'muted'

export function StatusText({
  tone = 'muted',
  children,
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: StatusTone; children: ReactNode }) {
  return (
    <span className={cx('status-text', `status-text-${tone}`, className)} {...props}>
      {children}
    </span>
  )
}

export function StatusPair({
  label,
  value,
  tone = 'muted',
}: {
  label: ReactNode
  value: ReactNode
  tone?: StatusTone
}) {
  return (
    <span className="status-pair">
      <span className="status-pair-label">{label}</span>
      <StatusText tone={tone}>{value}</StatusText>
    </span>
  )
}

