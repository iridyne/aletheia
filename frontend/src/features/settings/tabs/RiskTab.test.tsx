// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import type { RiskPolicyDraft } from './RiskTab'
import { RiskPolicyEditor, RiskTab } from './RiskTab'
import SRC from './RiskTab.tsx?raw'

const DEMO_POLICY = {
  policy_id: 'demo-policy',
  revision: 1,
  capital: { base_source: 'account_equity', base_usdt: 100 },
  loss: { max_daily_loss_pct: 1 },
} as const satisfies RiskPolicyDraft

function changeNumber(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
}

function renderRiskTab() {
  const client = new QueryClient()
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <RiskTab
        settings={{ universe: {}, event_triggers: {}, risk: { symbol_filter_mode: 'blacklist', symbol_list: [] }, market_data: {} }}
        qc={client}
        onDirty={() => undefined}
      />
    </QueryClientProvider>,
  )
}

describe('RiskTab', () => {
  it('preserves section order and the Universe deep-link anchor', () => {
    const html = renderRiskTab()
    const ids = ['universe', 'event-triggers', 'tradable-symbols', 'risk-limits']
    let previous = -1
    for (const id of ids) {
      const position = html.indexOf(`id="settings-section-${id}"`)
      expect(position).toBeGreaterThan(previous)
      previous = position
    }
  })

  it('keeps every risk domain component and safety control composed', () => {
    const html = renderRiskTab()

    expect(SRC).toContain('<UniverseConfigCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(SRC).toContain('<EventTriggerCard settings={settings} qc={qc} onDirty={onDirty} />')
    expect(html).toContain('Empty — all symbols are tradable.')
    expect(html).toContain('Max daily loss')
    expect(html).toContain('Kill switch auto-trigger (live)')
    expect(SRC).toContain('<RiskPolicyProfiles risk={risk} qc={qc} onDirty={onDirty} />')
  })

  it('preserves a dirty risk draft across a sibling settings refresh', async () => {
    const client = new QueryClient()
    const onDirty = vi.fn()
    const savedPolicy = { ...DEMO_POLICY, revision: 2, capital: { base_source: 'custom', base_usdt: 275 } }
    const replacePolicy = vi.spyOn(api, 'replaceRiskPolicy').mockResolvedValue({
      demo: savedPolicy,
      live: {},
    })
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
    const renderEditor = (initial = DEMO_POLICY) => (
      <QueryClientProvider client={client}>
        <RiskPolicyEditor mode="demo" initial={initial} qc={client} onDirty={onDirty} />
      </QueryClientProvider>
    )

    await act(async () => root.render(renderEditor()))
    const source = container.querySelector<HTMLSelectElement>('select')

    await act(async () => {
      if (source) {
        source.value = 'custom'
        source.dispatchEvent(new Event('change', { bubbles: true }))
      }
    })
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Custom capital base"]')
    expect(input).not.toBeNull()
    await act(async () => {
      changeNumber(input as HTMLInputElement, '250')
      root.render(renderEditor({ ...DEMO_POLICY, capital: { ...DEMO_POLICY.capital } }))
    })

    expect(input?.value).toBe('250')
    expect(onDirty).toHaveBeenLastCalledWith('risk-policy-demo', true)

    const save = Array.from(container.querySelectorAll('button'))
      .find(button => button.textContent === 'Save demo policy')
    expect(save).toBeDefined()
    await act(async () => {
      save?.click()
      await Promise.resolve()
    })

    expect(replacePolicy).toHaveBeenCalledWith(
      'demo',
      expect.objectContaining({
        revision: 1,
        capital: expect.objectContaining({ base_source: 'custom', base_usdt: 250 }),
      }),
      1,
      'demo policy edited in Settings',
    )
    expect(input?.value).toBe('275')
    expect(onDirty).toHaveBeenLastCalledWith('risk-policy-demo', false)
    await act(async () => root.unmount())
    container.remove()
    replacePolicy.mockRestore()
  })

  it('defaults to account equity and enables the amount only for Custom', async () => {
    const client = new QueryClient()
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

    await act(async () => root.render(
      <QueryClientProvider client={client}>
        <RiskPolicyEditor mode="demo" initial={DEMO_POLICY} qc={client} />
      </QueryClientProvider>,
    ))

    const source = container.querySelector<HTMLSelectElement>('select')
    expect(source?.value).toBe('account_equity')
    expect(container.querySelector('input[aria-label="Custom capital base"]')).toBeNull()

    await act(async () => {
      if (source) {
        source.value = 'custom'
        source.dispatchEvent(new Event('change', { bubbles: true }))
      }
    })
    expect(container.querySelector('input[aria-label="Custom capital base"]')).not.toBeNull()

    await act(async () => root.unmount())
    container.remove()
  })
})
