import type { HTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

type BadgeTone = 'green' | 'red' | 'yellow' | 'blue' | 'muted' | 'neutral' | 'warning'

export function Badge({
  tone = 'neutral',
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span className={cx('badge', tone !== 'neutral' && `badge-${tone}`, className)} {...props}>
      {children}
    </span>
  )
}

