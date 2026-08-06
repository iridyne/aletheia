import { createContext, useContext } from 'react'

/**
 * The id of the visible `SettingsRow` label, published to whatever control the
 * row wraps.
 *
 * `SettingsRow` renders its label as a plain `<div>`, not a `<label>`, because
 * a row may hold several controls (a toggle plus a number input, a button pair)
 * and a `<label>` can only name one. That leaves the visible text and the
 * control unassociated: screen readers announce "checkbox, unchecked" with no
 * indication of what it governs.
 *
 * Rather than force ~65 call sites to repeat a name that is already on screen,
 * the row publishes its label id here and label-less controls adopt it. Sites
 * outside a row read `null` and must name themselves.
 */
export const SettingsRowLabelContext = createContext<string | null>(null)

export function useSettingsRowLabelId(): string | null {
  return useContext(SettingsRowLabelContext)
}
