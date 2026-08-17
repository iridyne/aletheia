import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const frontendRoot = resolve(import.meta.dirname, '..')
const repositoryRoot = resolve(frontendRoot, '..')
const output = join(frontendRoot, 'src/api/generated/settings.d.ts')
const checkOnly = process.argv.includes('--check')
const tempRoot = mkdtempSync(join(tmpdir(), 'aletheia-settings-types-'))
const openapiPath = join(tempRoot, 'openapi.json')
const miniPath = join(tempRoot, 'settings-openapi.json')
const generatedPath = join(tempRoot, 'settings.d.ts')

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options })
  if (result.status !== 0) {
    process.stderr.write(result.stdout || '')
    process.stderr.write(result.stderr || '')
    throw new Error(`${command} exited with ${result.status ?? 'unknown status'}`)
  }
  return result
}

function collectSchemaRefs(value, refs) {
  if (!value || typeof value !== 'object') return
  if (typeof value.$ref === 'string' && value.$ref.startsWith('#/components/schemas/')) {
    refs.add(value.$ref.slice('#/components/schemas/'.length))
  }
  for (const child of Object.values(value)) collectSchemaRefs(child, refs)
}

try {
  run('uv', [
    'run', 'python', join(repositoryRoot, 'scripts/export_openapi_schema.py'),
    '--output', openapiPath,
  ], { cwd: repositoryRoot })

  const document = JSON.parse(readFileSync(openapiPath, 'utf8'))
  const settingsPath = document.paths?.['/api/settings']
  if (!settingsPath) throw new Error('OpenAPI document has no /api/settings path')

  const refs = new Set()
  collectSchemaRefs(settingsPath, refs)
  for (const name of [...refs]) collectSchemaRefs(document.components?.schemas?.[name], refs)
  let changed = true
  while (changed) {
    const before = refs.size
    for (const name of refs) collectSchemaRefs(document.components?.schemas?.[name], refs)
    changed = refs.size !== before
  }

  const schemas = Object.fromEntries(
    [...refs].sort().map((name) => [name, document.components.schemas[name]]),
  )
  writeFileSync(miniPath, JSON.stringify({
    openapi: document.openapi,
    info: document.info,
    paths: { '/api/settings': settingsPath },
    components: { schemas },
  }) + '\n')

  run(join(frontendRoot, 'node_modules/.bin/openapi-typescript'), [
    miniPath, '--output', generatedPath,
  ], { cwd: frontendRoot })

  const generated = readFileSync(generatedPath, 'utf8')
  if (checkOnly) {
    if (!existsSync(output) || readFileSync(output, 'utf8') !== generated) {
      console.error(`${output} is out of date; run npm run generate:settings-types`)
      process.exitCode = 1
    }
  } else {
    mkdirSync(dirname(output), { recursive: true })
    writeFileSync(output, generated)
  }
} finally {
  rmSync(tempRoot, { recursive: true, force: true })
}
