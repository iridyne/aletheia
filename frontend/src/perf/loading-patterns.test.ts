/**
 * Static analysis: enforce loading-state coverage per page.
 *
 * A page that calls useQuery N times should show a loading indicator for
 * at least half those queries. If ratio < MIN_RATIO, the page likely renders
 * a blank body while data is in-flight, producing the visible 100ms+ gap.
 *
 * Run: npx vitest run src/perf/loading-patterns.test.ts
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const PAGES_DIR = resolve(__dirname, '../pages')

// Regex anchored to real call-sites, not import lines.
// Handles plain useQuery({ and generic useQuery<Type>({ forms.
// ponytail: RE_LOADING counts all isPending/isLoading/isFetching occurrences,
//   including those on useMutation results. Acceptable false-positive rate for a
//   ratio heuristic; switch to AST analysis if precision becomes important.
const RE_QUERY    = /\buseQuery(?:<[^>]*>)?\s*\(/g
const RE_LOADING  = /\b(?:isPending|isLoading|isFetching)\b|class(?:Name)?=["'`][^"'`]*(?:skeleton|Skeleton)[^"'`]*["'`]/g

// Any loading check per query. 0.5 = at least one indicator for every two queries.
const MIN_RATIO = 0.5

// Lines above which a page is flagged as "large" (informational, not a failure).
const LARGE_PAGE_LINES = 900

const pages = readdirSync(PAGES_DIR)
  .filter(f => f.endsWith('.tsx') && !f.includes('.test.'))
  .map(f => {
    const source = readFileSync(join(PAGES_DIR, f), 'utf-8')
    const queries      = (source.match(RE_QUERY)   ?? []).length
    const loadingChecks = (source.match(RE_LOADING) ?? []).length
    return {
      name: f.replace('.tsx', ''),
      lines: source.split('\n').length,
      queries,
      loadingChecks,
      ratio: queries > 0 ? loadingChecks / queries : 1,
    }
  })

describe('page loading-state coverage', () => {
  it('prints per-page audit table', () => {
    const rows = pages.map(p => ({
      page: p.name,
      lines: p.lines,
      queries: p.queries,
      loading_checks: p.loadingChecks,
      ratio: p.ratio.toFixed(2),
      large: p.lines > LARGE_PAGE_LINES ? '⚠' : '',
      coverage: p.queries > 0 && p.ratio < MIN_RATIO ? '⚠ LOW' : 'ok',
    }))
    console.table(rows)
  })

  it(`each page has loading-state ratio ≥ ${MIN_RATIO}`, () => {
    const failing = pages.filter(p => p.queries > 0 && p.ratio < MIN_RATIO)
    if (failing.length === 0) return

    const details = failing
      .map(p =>
        `  ${p.name}: ${p.queries} queries, ${p.loadingChecks} loading checks` +
        ` (ratio=${p.ratio.toFixed(2)}, need ≥${MIN_RATIO})`
      )
      .join('\n')

    expect.fail(
      `${failing.length} page(s) lack loading-state coverage:\n${details}\n\n` +
      `Fix: add isPending/isLoading guards or skeleton states for each useQuery.\n` +
      `If a query is background-only (never blocks visible render), annotate the\n` +
      `useQuery call with  // perf: non-blocking  to exclude it from this count.`
    )
  })

  it('pages marked non-blocking do not inflate failure count', () => {
    // Validate that any  // perf: non-blocking  annotation is on a useQuery line
    const pages2 = readdirSync(PAGES_DIR)
      .filter(f => f.endsWith('.tsx') && !f.includes('.test.'))
      .map(f => ({ name: f, src: readFileSync(join(PAGES_DIR, f), 'utf-8') }))
    const misused = pages2.flatMap(({ name, src }) =>
      src.split('\n')
        .map((line, i) => ({ line, n: i + 1 }))
        .filter(({ line }) => line.includes('// perf: non-blocking') && !line.includes('useQuery'))
        .map(({ n }) => `${name}:${n}`)
    )
    expect(misused, `// perf: non-blocking must only appear on useQuery lines`).toEqual([])
  })
})
