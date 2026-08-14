import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cx } from './common'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md'

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize; children: ReactNode }>(
  function Button({
    variant = 'primary',
    size = 'md',
    className,
    children,
    ...props
  }, ref) {
    return (
      <button
        ref={ref}
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
)
Button.displayName = 'Button'

