import type { HTMLAttributes } from 'react'
import { cx } from './common'

type StatusTone = 'green' | 'red' | 'yellow' | 'muted'

export function StatusDot({ tone = 'muted', className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: StatusTone }) {
  return <span className={cx('dot', `dot-${tone}`, className)} {...props} />
}

