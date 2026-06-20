import type { ReactNode } from 'react'
import { Switch } from './Switch'

export interface NumField {
  key: string
  label: string
  unit: string
  min: number
  max: number
  step: number
  isInt?: boolean
}

export interface BoolField {
  key: string
  label: string
}

export function SettingsSection({
  id,
  title,
  description,
  children,
}: {
  id?: string
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section className="settings-section" id={id ? `settings-section-${id}` : undefined}>
      <div className="settings-section-head">
        <h3>{title}</h3>
        {description && <p className="settings-section-desc">{description}</p>}
      </div>
      <div className="settings-section-rule" />
      {children}
    </section>
  )
}

export function SettingsRow({
  label,
  hint,
  children,
  danger,
}: {
  label: string
  hint?: string
  children: ReactNode
  danger?: boolean
}) {
  return (
    <div className="settings-row">
      <div>
        <div className={`settings-row-label${danger ? ' danger' : ''}`}>{label}</div>
        {hint && <div className="settings-row-hint">{hint}</div>}
      </div>
      <div className="settings-row-control">
        {children}
      </div>
    </div>
  )
}

export function SaveMessage({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null
  return <div className={`settings-save-msg ${msg.ok ? 'ok' : 'err'}`} title={msg.text}>{msg.text}</div>
}

export function NumberFieldGrid({
  fields,
  vals,
  set,
}: {
  fields: NumField[]
  vals: Record<string, unknown>
  set: (key: string, value: number) => void
}) {
  return (
    <div className="settings-field-grid">
      {fields.map(f => (
        <label key={f.key} className="settings-field">
          <span>{f.label}{f.unit ? ` (${f.unit})` : ''}</span>
          <input
            type="number"
            min={f.min}
            max={f.max}
            step={f.step}
            value={(vals[f.key] as string | number | undefined) ?? ''}
            onChange={e => {
              const raw = e.target.value
              if (raw === '' || raw === '-') return
              const n = f.isInt ? parseInt(raw, 10) : parseFloat(raw)
              if (!Number.isFinite(n)) return
              set(f.key, n)
            }}
          />
        </label>
      ))}
    </div>
  )
}

export function SwitchGrid({
  fields,
  vals,
  set,
}: {
  fields: BoolField[]
  vals: Record<string, unknown>
  set: (key: string, value: boolean) => void
}) {
  return (
    <div className="settings-toggle-grid">
      {fields.map(f => (
        <Switch
          key={f.key}
          checked={!!vals[f.key]}
          label={f.label}
          onCheckedChange={v => set(f.key, v)}
        />
      ))}
    </div>
  )
}

