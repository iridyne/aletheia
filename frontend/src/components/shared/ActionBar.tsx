type Action = {
  label: string
  onClick: () => void
  variant?: 'primary' | 'secondary' | 'danger'
  disabled?: boolean
}

export default function ActionBar({ actions }: { actions: Action[] }) {
  return (
    <div className="action-bar">
      {actions.map((a, i) => (
        <button
          key={i}
          className={a.variant === 'danger' ? 'danger-btn' : a.variant === 'secondary' ? 'secondary' : ''}
          onClick={a.onClick}
          disabled={a.disabled}
        >
          {a.label}
        </button>
      ))}
    </div>
  )
}
