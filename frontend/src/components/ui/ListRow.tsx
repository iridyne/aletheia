import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

export function ListRowButton({
  selected,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean; children: ReactNode }) {
  return (
    <button className={cx('ui-list-row', selected && 'selected', className)} {...props}>
      {children}
    </button>
  )
}

