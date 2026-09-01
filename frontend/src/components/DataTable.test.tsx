// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createColumnHelper } from '@tanstack/react-table'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataTable } from './DataTable'

const col = createColumnHelper<{ name: string }>()
const columns = [col.accessor('name', { header: 'Name' })]
let root: Root | undefined

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  root = undefined
})

describe('DataTable state contract', () => {
  it('distinguishes unavailable from empty and retains stale rows', async () => {
    const container = document.createElement('div')
    root = createRoot(container)

    await act(async () => root?.render(<DataTable state="unavailable" data={[]} columns={columns} />))
    expect(container.textContent).toContain('not an empty result')
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(container.querySelector('table')).toBeNull()

    await act(async () => root?.render(<DataTable state="stale" data={[{ name: 'BTC' }]} columns={columns} />))
    expect(container.textContent).toContain('Retained rows')
    expect(container.textContent).toContain('BTC')
    expect(container.querySelector('table')).not.toBeNull()

    await act(async () => root?.render(<DataTable state="empty" data={[]} columns={columns} emptyMessage="No successful runs yet." />))
    expect(container.textContent).toBe('No successful runs yet.')
    expect(container.querySelector('table')).toBeNull()
  })

  it('does not intercept keyboard activation from a nested action', async () => {
    const container = document.createElement('div')
    const onRowClick = vi.fn()
    const actionColumns = [
      col.accessor('name', { header: 'Name' }),
      col.display({ id: 'action', cell: () => <button type="button">Close</button> }),
    ]
    root = createRoot(container)

    await act(async () => root?.render(
      <DataTable data={[{ name: 'BTC' }]} columns={actionColumns} onRowClick={onRowClick} />,
    ))
    const button = container.querySelector('tbody button')!
    const row = container.querySelector('tbody tr')!

    for (const key of ['Enter', ' ']) {
      button.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    }
    expect(onRowClick).not.toHaveBeenCalled()

    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    expect(onRowClick).toHaveBeenCalledTimes(1)
  })
})
