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

  // Precedence: an explicit `label` renders visible text that names the input on
  // its own. Failing that, an aria-label / aria-labelledby from the call site
  // wins. Only a switch with no name of its own adopts the enclosing
  // SettingsRow's visible label.
  const named = Boolean(label) || Boolean(props['aria-label']) || Boolean(props['aria-labelledby'])
  const ariaLabelledBy = named ? props['aria-labelledby'] : (rowLabelId ?? undefined)

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
