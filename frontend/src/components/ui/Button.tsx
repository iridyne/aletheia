import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md'

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize; children: ReactNode }) {
  return (
    <button
      className={cx(
        variant === 'secondary' && 'secondary',
        variant === 'ghost' && 'btn-ghost',
        variant === 'danger' && 'danger',
        size === 'sm' && 'btn-sm',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

