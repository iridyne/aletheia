import { brotliCompressSync, constants, gzipSync } from 'node:zlib'
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const output = resolve(process.env.VITE_BUILD_OUT_DIR || '../aletheia/dashboard_static')
const minimumBytes = 256
const textExtensions = new Set(['.html', '.css', '.js', '.json', '.svg', '.txt', '.xml', '.webmanifest'])

function walk(directory) {
  const files = []
  for (const name of readdirSync(directory)) {
    const path = join(directory, name)
    const stat = statSync(path)
    if (stat.isDirectory()) files.push(...walk(path))
    else files.push(path)
  }
  return files
}

for (const path of walk(output)) {
  if (path.endsWith('.br') || path.endsWith('.gz')) continue
  const dot = path.lastIndexOf('.')
  if (dot < 0 || !textExtensions.has(path.slice(dot).toLowerCase())) continue
  const source = readFileSync(path)
  if (source.length < minimumBytes) continue
  writeFileSync(`${path}.br`, brotliCompressSync(source, {
    params: { [constants.BROTLI_PARAM_QUALITY]: 5 },
  }))
  writeFileSync(`${path}.gz`, gzipSync(source, { level: 6, mtime: 0 }))
}
