import type { InputHTMLAttributes } from 'react'
import { useSettingsRowLabelId } from './SettingsRowLabelContext'

export type SwitchPersistence = 'immediate' | 'staged'

export function Switch({
  checked,
  onCheckedChange,
  onChange,
  label,
  disabled,
  persistence,
  'aria-label': explicitAriaLabel,
  'aria-labelledby': explicitAriaLabelledBy,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'checked' | 'onChange'> & {
  checked: boolean
  /** Explicitly states whether the value is persisted by this interaction or by a later Save. */
  persistence: SwitchPersistence
  onCheckedChange?: (checked: boolean) => void
  onChange?: (checked: boolean) => void
  label?: string
}) {
  const setChecked = onCheckedChange ?? onChange
  const rowLabelId = useSettingsRowLabelId()

  if (label !== undefined && !label.trim()) {
    throw new Error('Switch label must be non-empty; omit label inside a SettingsRow')
  }
  if (explicitAriaLabel !== undefined && !explicitAriaLabel.trim()) {
    throw new Error('Switch aria-label must be non-empty')
  }
  if (explicitAriaLabelledBy !== undefined && !explicitAriaLabelledBy.trim()) {
    throw new Error('Switch aria-labelledby must be non-empty')
  }

  // Precedence: an explicit `label` renders visible text that names the input on
  // its own. Failing that, an aria-label / aria-labelledby from the call site
  // wins. Only a switch with no name of its own adopts the enclosing
  // SettingsRow's visible label. Keep only one naming path on the input so
  // accidental combinations cannot concatenate unrelated labels.
  const named = Boolean(label?.trim() || explicitAriaLabel?.trim() || explicitAriaLabelledBy?.trim())
  const ariaLabel = label ? undefined : explicitAriaLabel
  const ariaLabelledBy = label || explicitAriaLabel
    ? undefined
    : (explicitAriaLabelledBy || (named ? undefined : rowLabelId ?? undefined))

  // The wrapper stays a <label> even with no text: the input is
  // `pointer-events: none` and visually hidden, so label association is the only
  // thing that forwards a click on the track. A text-less <label> does not name
  // the input, which is what aria-labelledby above is for. Call sites must not
  // add a <label> of their own around this — pass `label` instead.
  const staged = persistence === 'staged'
  return (
    <label
      className={`settings-toggle${staged ? ' settings-staged-toggle' : ''}`}
      data-persistence={persistence}
    >
      <input
        {...props}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        className={staged ? 'settings-staged-input' : 'settings-switch-input'}
        data-persistence={persistence}
        type="checkbox"
        checked={checked}
        onChange={e => setChecked?.(e.target.checked)}
        disabled={disabled}
      />
      {!staged && <span className="settings-switch-track" aria-hidden="true"><span className="settings-switch-thumb" /></span>}
      {label && <span className="settings-switch-text">{label}</span>}
      {staged && <span className="settings-staged-badge" aria-hidden="true">draft</span>}
    </label>
  )
}
