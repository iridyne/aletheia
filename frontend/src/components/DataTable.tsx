import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useEffect, useState } from 'react'
import { humanize } from '../lib/format'
import { DataStateMessage, type DataState } from './DataState'

interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T, any>[]
  onRowClick?: (row: T) => void
  pageSize?: number
  emptyMessage?: string
  /** Show a top-right "Columns" button to toggle column visibility. */
  columnToggle?: boolean
  /** When set (with columnToggle), persist the chosen visibility to localStorage. */
  tableId?: string
  /** Force true horizontal scroll (cells never wrap) — for wide, many-column tables. */
  noWrap?: boolean
  /** Explicit presentation truth. Stale/partial retain rows; blocking states do not. */
  state?: DataState
  /** State-specific copy supplied by the caller when the default is not precise enough. */
  stateMessage?: string
}

function loadVisibility(tableId?: string): VisibilityState {
  if (!tableId) return {}
  try {
    const raw = localStorage.getItem(`dt-cols:${tableId}`)
    return raw ? (JSON.parse(raw) as VisibilityState) : {}
  } catch {
    return {}
  }
}

export function DataTable<T>({
  data, columns, onRowClick, pageSize = 25, emptyMessage = 'No data.',
  columnToggle = false, tableId, noWrap = true, state, stateMessage,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() => loadVisibility(tableId))
  const [colMenuOpen, setColMenuOpen] = useState(false)

  // Persist column visibility per table so the user's choice survives reloads.
  useEffect(() => {
    if (!tableId) return
    try {
      localStorage.setItem(`dt-cols:${tableId}`, JSON.stringify(columnVisibility))
    } catch { /* storage unavailable — visibility stays session-only */ }
  }, [tableId, columnVisibility])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, columnVisibility },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
  })

  const resolvedState: DataState = state ?? (data.length === 0 ? 'empty' : 'available')
  const visibleState = resolvedState === 'available' && data.length === 0 ? 'empty' : resolvedState
  const stateNotice = visibleState === 'available'
    ? null
    : <DataStateMessage state={visibleState} message={stateMessage ?? (visibleState === 'empty' ? emptyMessage : undefined)} />
  const showTable = data.length > 0 && ['available', 'stale', 'partial'].includes(visibleState)

  if (!showTable) return stateNotice

  const pageCount = table.getPageCount()
  const currentPage = table.getState().pagination.pageIndex
  const showToolbar = data.length > pageSize || columnToggle

  return (
    <div>
      {stateNotice}
      {showToolbar && (
        <div className="dt-toolbar">
          {data.length > pageSize ? (
            <input
              placeholder="Filter..."
              aria-label="Filter table rows"
              value={globalFilter}
              onChange={e => setGlobalFilter(e.target.value)}
              className="dt-filter"
            />
          ) : <span />}
          {columnToggle && (
            <div className="dt-colmenu-wrap">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                aria-expanded={colMenuOpen}
                onClick={() => setColMenuOpen(o => !o)}
              >
                Columns ▾
              </button>
              {colMenuOpen && (
                <>
                  <div className="dt-colmenu-backdrop" onClick={() => setColMenuOpen(false)} />
                  <div className="dt-colmenu" role="menu">
                    {table.getAllLeafColumns().filter(c => c.getCanHide()).map(col => {
                      const label = typeof col.columnDef.header === 'string'
                        ? col.columnDef.header
                        : humanize(col.id)
                      return (
                        <label key={col.id} className="dt-colmenu-item">
                          <input
                            type="checkbox"
                            checked={col.getIsVisible()}
                            onChange={col.getToggleVisibilityHandler()}
                          />
                          {label}
                        </label>
                      )
                    })}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}
      <div className={noWrap ? 'table-wrap table-wrap-nowrap' : 'table-wrap'}>
      <table>
        <thead>
          <tr>
            {table.getHeaderGroups()[0].headers.map(header => {
              const canSort = header.column.getCanSort()
              const sorted = header.column.getIsSorted()
              const label = flexRender(header.column.columnDef.header, header.getContext())
              return (
                <th
                  key={header.id}
                  className={canSort ? 'sortable' : undefined}
                  aria-sort={canSort ? sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none' : undefined}
                >
                  {canSort ? (
                    <button type="button" className="dt-sort-button" onClick={header.column.getToggleSortingHandler()}>
                      {label}
                      <span className="sort-indicator" aria-hidden="true">
                        {{ asc: ' ▲', desc: ' ▼' }[sorted as string] ?? ''}
                      </span>
                    </button>
                  ) : label}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => (
            <tr
              key={row.id}
              className={onRowClick ? 'clickable' : undefined}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(row.original) } } : undefined}
              tabIndex={onRowClick ? 0 : undefined}
            >
              {row.getVisibleCells().map(cell => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {pageCount > 1 && (
        <div className="dt-pagination">
          <button className="secondary" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
            ← Prev
          </button>
          <span className="dt-page-info">{currentPage + 1} / {pageCount}</span>
          <button className="secondary" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
            Next →
          </button>
        </div>
      )}
    </div>
  )
}

// Canonical status → badge-color mapping for the whole dashboard. Any surface showing a
// run/proposal/strategy/position status routes through here so the same status is the
// same color everywhere (the old per-page RunStatusBadge variants are removed in favor
// of this). Label is humanized (no raw snake_case) via the shared humanize().
export function statusBadgeClass(status: string): string {
  const s = String(status).toLowerCase()
  // green — success / live / healthy / terminal-good
  if (['completed', 'executed', 'approved', 'open', 'paper_passed', 'live_candidate', 'live_limited', 'healthy', 'filled', 'done'].includes(s))
    return 'badge-green'
  // red — failure / rejection / danger
  if (['rejected', 'failed', 'error', 'blocked', 'retired', 'cancelled', 'canceled', 'killed'].includes(s))
    return 'badge-red'
  // yellow — in-flight / pending / warning / throttled
  if (['pending', 'running', 'in_progress', 'rate_limited', 'paused', 'degraded', 'warning', 'flagged', 'under_review', 'paper_running'].includes(s))
    return 'badge-yellow'
  // muted — empty / inert / closed-neutral
  if (['completed_empty', 'idle', 'closed', 'reviewed', 'archived', 'deprecated', 'not_evaluated', 'not_available'].includes(s))
    return 'badge-muted'
  // blue — informational / draft / queued (default)
  return 'badge-blue'
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${statusBadgeClass(status)}`}>{humanize(status)}</span>
}
