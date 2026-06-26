import { type ReactNode, useState } from 'react'
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

export function CollapsibleSection({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={`settings-collapsible${open ? ' open' : ''}`}>
      <button className="settings-collapsible-toggle" onClick={() => setOpen(!open)}>
        <span className="settings-collapsible-chevron">{open ? '▾' : '▸'}</span>
        <span>{title}</span>
      </button>
      {open && <div className="settings-collapsible-body">{children}</div>}
    </div>
  )
}

export function SaveMessage({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null
  return <div className={`settings-save-msg ${msg.ok ? 'ok' : 'err'}`} title={msg.text}>{msg.text}</div>
}

function NumInput({ field: f, value, set }: { field: NumField; value: unknown; set: (v: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const displayed = draft ?? String((value as string | number | undefined) ?? '')
  return (
    <label className="settings-field">
      <span>{f.label}{f.unit ? ` (${f.unit})` : ''}</span>
      <input
        type="number"
        step={f.step}
        value={displayed}
        onChange={e => setDraft(e.target.value)}
        onBlur={() => {
          if (draft == null) return
          const n = f.isInt ? parseInt(draft, 10) : parseFloat(draft)
          if (Number.isFinite(n)) set(n)
          setDraft(null)
        }}
      />
    </label>
  )
}

export function NumberFieldGrid({
  fields,
  vals,
  set,
  errors,
}: {
  fields: NumField[]
  vals: Record<string, unknown>
  set: (key: string, value: number) => void
  errors?: Record<string, string>
}) {
  return (
    <div className="settings-field-grid">
      {fields.map(f => (
        <div key={f.key}>
          <NumInput field={f} value={vals[f.key]} set={v => set(f.key, v)} />
          {errors?.[f.key] && <div className="settings-field-error">{errors[f.key]}</div>}
        </div>
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

