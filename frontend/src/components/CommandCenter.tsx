import { useState, useRef, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '../api/client'

type Message = {
  role: 'user' | 'system'
  text: string
  type?: string
  cards?: any[]
  actions?: any[]
}

export default function CommandCenter({ embedded }: { embedded?: boolean }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const bodyRef = useRef<HTMLDivElement>(null)

  const sendMut = useMutation({
    mutationFn: (text: string) => api.postCommand(text),
    onSuccess: (res) => {
      setMessages(m => [...m, {
        role: 'system',
        text: res.message,
        type: res.type,
        cards: res.cards,
        actions: res.actions,
      }])
    },
    onError: (err: any) => {
      setMessages(m => [...m, { role: 'system', text: `Error: ${err.message}`, type: 'error' }])
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text) return
    setMessages(m => [...m, { role: 'user', text }])
    setInput('')
    sendMut.mutate(text)
  }

  const handleAction = (action: string) => {
    setMessages(m => [...m, { role: 'user', text: action }])
    sendMut.mutate(action)
  }

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  return (
    <div className={`cc-container ${embedded ? 'cc-embedded' : ''}`}>
      <div className="cc-messages" ref={bodyRef}>
        {messages.length === 0 && (
          <div className="cc-welcome">
            <p className="cc-welcome-title">Command Center</p>
            <p className="cc-welcome-hint">Ask, command, or propose. Try:</p>
            <div className="cc-suggestions">
              {['status', 'pause', 'resume', 'latest proposals'].map(s => (
                <button key={s} className="cc-suggestion" onClick={() => handleAction(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`cc-msg cc-msg-${msg.role}`}>
            {msg.role === 'user' ? (
              <span className="cc-user-text">{msg.text}</span>
            ) : (
              <div className="cc-response">
                <p className="cc-response-text">{msg.text}</p>
                {msg.cards && msg.cards.map((card: any, ci: number) => (
                  <div key={ci} className="cc-card">
                    <div className="cc-card-title">{card.title}</div>
                    {card.fields && (
                      <div className="cc-card-fields">
                        {Object.entries(card.fields).map(([k, v]) => (
                          <div key={k} className="cc-field">
                            <span className="cc-field-key">{k}</span>
                            <span className="cc-field-val">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {card.items && (
                      <div className="cc-card-items">
                        {card.items.map((item: any, ii: number) => (
                          <div key={ii} className="cc-card-item">
                            {Object.values(item).filter(Boolean).join(' — ')}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {msg.actions && (
                  <div className="cc-actions">
                    {msg.actions.map((a: any, ai: number) => (
                      <button key={ai} className="cc-action-btn" onClick={() => handleAction(a.action)}>
                        {a.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {sendMut.isPending && <div className="cc-msg cc-msg-system"><span className="cc-loading">thinking...</span></div>}
      </div>
      <form className="cc-input-form" onSubmit={handleSubmit}>
        <input
          className="cc-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask, command, or propose..."
          autoFocus
        />
        <button type="submit" className="cc-send" disabled={sendMut.isPending || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
