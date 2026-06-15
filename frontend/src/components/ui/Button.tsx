import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './common'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export function Button({
  variant = 'primary',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; children: ReactNode }) {
  return (
    <button
      className={cx(
        variant === 'secondary' && 'secondary',
        variant === 'ghost' && 'btn-ghost',
        variant === 'danger' && 'danger',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

