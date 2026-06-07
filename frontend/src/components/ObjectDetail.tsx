import { ReactNode } from 'react'
import { DrawerSection, DrawerField } from './DetailDrawer'

export interface ObjectField {
  label: string
  value: ReactNode
  mono?: boolean
}

export interface ObjectSection {
  label: string
  fields?: ObjectField[]
  content?: ReactNode
}

export interface ObjectAction {
  label: string
  onClick: () => void
  variant?: 'primary' | 'secondary' | 'danger'
  disabled?: boolean
}

export interface ObjectDetailSchema {
  sections: ObjectSection[]
  actions?: ObjectAction[]
}

export function ObjectDetail({ schema }: { schema: ObjectDetailSchema }) {
  return (
    <>
      {schema.sections.map(section => (
        <DrawerSection key={section.label} label={section.label}>
          {section.fields?.map(f => (
            <DrawerField key={f.label} label={f.label} value={f.value} mono={f.mono} />
          ))}
          {section.content}
        </DrawerSection>
      ))}
      {schema.actions && schema.actions.length > 0 && (
        <div className="flex gap-2 mt-3">
          {schema.actions.map(a => (
            <button
              key={a.label}
              className={a.variant === 'danger' ? 'secondary' : a.variant === 'secondary' ? 'secondary' : ''}
              style={a.variant === 'danger' ? { borderColor: 'rgba(255,93,108,0.4)', color: 'var(--red)' } : undefined}
              onClick={a.onClick}
              disabled={a.disabled}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </>
  )
}
