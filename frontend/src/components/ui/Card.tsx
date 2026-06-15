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

export function CardHeader({
  title,
  meta,
  actions,
  className,
}: {
  title: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cx('ui-card-header', className)}>
      <div className="ui-card-heading">
        {typeof title === 'string' ? <h3>{title}</h3> : title}
        {meta && <div className="ui-card-meta">{meta}</div>}
      </div>
      {actions && <div className="ui-card-actions">{actions}</div>}
    </div>
  )
}

export function ActionRow({ children, align = 'end' }: { children: ReactNode; align?: 'start' | 'end' }) {
  return <div className={cx('ui-action-row', align === 'start' && 'ui-action-row-start')}>{children}</div>
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="table-wrap">{children}</div>
}

