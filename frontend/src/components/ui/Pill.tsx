import type { HTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

export type PillTone = 'green' | 'red' | 'yellow' | 'blue' | 'accent' | 'muted'

export function Pill({
  tone = 'muted',
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: PillTone; children: ReactNode }) {
  return (
    <span className={cx('pill', `pill-${tone}`, className)} {...props}>
      {children}
    </span>
  )
}
