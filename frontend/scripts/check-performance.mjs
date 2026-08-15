import { brotliCompressSync, constants, gzipSync } from 'node:zlib'
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const output = resolve(process.env.VITE_BUILD_OUT_DIR || '../aletheia/dashboard_static')
const html = readFileSync(join(output, 'index.html'), 'utf8')
const manifestPath = join(output, '.vite', 'manifest.json')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const entry = manifest['index.html']
const failures = []
const forbidden = ['recharts', 'lightweight-charts', 'cmdk']
const brotliOptions = { params: { [constants.BROTLI_PARAM_QUALITY]: 5 } }

if (!html.includes('id="boot-skeleton"')) failures.push('generated index.html is missing #boot-skeleton')
if (!entry?.file) failures.push('manifest is missing index.html entry')
if (entry?.file && !existsSync(join(output, entry.file))) failures.push(`manifest entry is missing: ${entry.file}`)

const entries = Object.values(manifest).filter(item => item && item.file)
const byFile = new Map(entries.map(item => [item.file, item]))
for (const item of entries) {
  byFile.set(item.file.split('/').pop(), item)
  if (item.file.startsWith('assets/')) byFile.set(`_${item.file.slice('assets/'.length)}`, item)
}
const closure = new Set()
function visit(item) {
  if (!item || closure.has(item.file)) return
  closure.add(item.file)
  for (const imported of item.imports || []) visit(byFile.get(imported))
}
visit(entry)

const preload = [...html.matchAll(/rel="modulepreload"[^>]+href="([^"]+)"/g)].map(match => match[1].split('/').pop())
for (const name of preload) if (!existsSync(join(output, 'assets', name))) failures.push(`modulepreload target is missing: ${name}`)
for (const name of [...preload, ...closure]) {
  const lower = name.toLowerCase()
  for (const library of forbidden) if (lower.includes(library)) failures.push(`${library} is in initial production graph: ${name}`)
}
for (const file of closure) {
  if (!file.endsWith('.js')) continue
  const source = readFileSync(join(output, file), 'utf8').toLowerCase()
  for (const library of forbidden) if (source.includes(library)) failures.push(`${library} marker is in initial chunk: ${file}`)
}

const entryJs = [...closure].filter(file => file.endsWith('.js'))
const entryCss = [...new Set([...closure, ...(entry?.css || [])])].filter(file => file.endsWith('.css'))
const size = (files, codec) => files.reduce((total, file) => {
  const source = readFileSync(join(output, file))
  if (codec === 'raw') return total + source.length
  if (codec === 'gzip') return total + gzipSync(source, { level: 6, mtime: 0 }).length
  return total + brotliCompressSync(source, brotliOptions).length
}, 0)
const metric = {
  schema_version: 'frontend-performance-v1',
  entry: entry?.file || null,
  modulepreload: preload,
  initial_graph: [...closure],
  critical_js: { raw: size(entryJs, 'raw'), gzip: size(entryJs, 'gzip'), br: size(entryJs, 'br'), files: entryJs },
  critical_css: { raw: size(entryCss, 'raw'), gzip: size(entryCss, 'gzip'), br: size(entryCss, 'br'), files: entryCss },
  total_static: {
    html_raw: Buffer.byteLength(html),
    html_gzip: gzipSync(Buffer.from(html), { level: 6, mtime: 0 }).length,
    html_br: brotliCompressSync(Buffer.from(html), brotliOptions).length,
  },
}
metric.total_static.critical_br = metric.total_static.html_br + metric.critical_js.br + metric.critical_css.br

const budgets = {
  critical_js_br: Number(process.env.ALETHEIA_CRITICAL_JS_BR || 200 * 1024),
  critical_css_br: Number(process.env.ALETHEIA_CRITICAL_CSS_BR || 30 * 1024),
  critical_static_br: Number(process.env.ALETHEIA_CRITICAL_STATIC_BR || 350 * 1024),
}
if (metric.critical_js.br > budgets.critical_js_br) failures.push(`critical JS Brotli ${metric.critical_js.br} exceeds ${budgets.critical_js_br}`)
if (metric.critical_css.br > budgets.critical_css_br) failures.push(`critical CSS Brotli ${metric.critical_css.br} exceeds ${budgets.critical_css_br}`)
if (metric.total_static.critical_br > budgets.critical_static_br) failures.push(`critical static Brotli ${metric.total_static.critical_br} exceeds ${budgets.critical_static_br}`)

const staticFiles = [join(output, 'index.html'), manifestPath, ...readdirSync(join(output, 'assets')).map(file => join(output, 'assets', file))]
for (const path of staticFiles) {
  if (!/\.(html|js|css|json|svg)$/.test(path) || statSync(path).size < 256) continue
  if (!existsSync(`${path}.br`) || !existsSync(`${path}.gz`)) failures.push(`missing compression sidecar for ${path}`)
}
const artifact = { ...metric, budgets }
writeFileSync(join(output, 'performance.json'), `${JSON.stringify(artifact, null, 2)}\n`)

if (failures.length) {
  console.error(failures.map(failure => `performance gate: ${failure}`).join('\n'))
  process.exitCode = 1
}
console.log(JSON.stringify({ ...artifact, failures }, null, 2))
