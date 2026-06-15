import type { InputHTMLAttributes } from 'react'

export function Switch({
  checked,
  onCheckedChange,
  onChange,
  label,
  disabled,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'checked' | 'onChange'> & {
  checked: boolean
  onCheckedChange?: (checked: boolean) => void
  onChange?: (checked: boolean) => void
  label?: string
}) {
  const setChecked = onCheckedChange ?? onChange
  return (
    <label className="settings-toggle">
      <input
        {...props}
        className="settings-switch-input"
        type="checkbox"
        checked={checked}
        onChange={e => setChecked?.(e.target.checked)}
        disabled={disabled}
      />
      <span className="settings-switch-track" aria-hidden="true"><span className="settings-switch-thumb" /></span>
      {label && <span className="settings-switch-text">{label}</span>}
    </label>
  )
}
