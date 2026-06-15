import type { HTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

export function Card({
  className,
  children,
  flush = false,
  ...props
}: HTMLAttributes<HTMLDivElement> & { children: ReactNode; flush?: boolean }) {
  return (
    <div className={cx('card', flush && 'card-flush', className)} {...props}>
      {children}
    </div>
  )
}

