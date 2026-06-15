import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import { humanize } from '../lib/format'

interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T, any>[]
  onRowClick?: (row: T) => void
  pageSize?: number
  emptyMessage?: string
}

export function DataTable<T>({ data, columns, onRowClick, pageSize = 25, emptyMessage = 'No data.' }: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
  })

  if (data.length === 0) {
    return <p className="text-muted">{emptyMessage}</p>
  }

  const pageCount = table.getPageCount()
  const currentPage = table.getState().pagination.pageIndex

  return (
    <div>
      {data.length > pageSize && (
        <div className="dt-toolbar">
          <input
            placeholder="Filter..."
            value={globalFilter}
            onChange={e => setGlobalFilter(e.target.value)}
            className="dt-filter"
          />
        </div>
      )}
      <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {table.getHeaderGroups()[0].headers.map(header => (
              <th
                key={header.id}
                onClick={header.column.getCanSort() ? header.column.getToggleSortingHandler() : undefined}
                className={header.column.getCanSort() ? 'sortable' : undefined}
              >
                {flexRender(header.column.columnDef.header, header.getContext())}
                <span className="sort-indicator">
                  {{ asc: ' ▲', desc: ' ▼' }[header.column.getIsSorted() as string] ?? ''}
                </span>
              </th>
            ))}
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
