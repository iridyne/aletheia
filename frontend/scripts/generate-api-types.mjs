import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const frontendRoot = resolve(import.meta.dirname, '..')
const repositoryRoot = resolve(frontendRoot, '..')
const output = join(frontendRoot, 'src/api/generated/api.d.ts')
const checkOnly = process.argv.includes('--check')
const tempRoot = mkdtempSync(join(tmpdir(), 'aletheia-api-types-'))
const openapiPath = join(tempRoot, 'openapi.json')
const generatedPath = join(tempRoot, 'api.d.ts')

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options })
  if (result.status !== 0) {
    process.stderr.write(result.stdout || '')
    process.stderr.write(result.stderr || '')
    throw new Error(`${command} exited with ${result.status ?? 'unknown status'}`)
  }
  return result
}

try {
  run('uv', [
    'run', 'python', '-m', 'scripts.export_openapi_schema',
    '--output', openapiPath,
  ], { cwd: repositoryRoot })

  run(join(frontendRoot, 'node_modules/.bin/openapi-typescript'), [
    openapiPath, '--output', generatedPath,
  ], { cwd: frontendRoot })

  const generated = readFileSync(generatedPath, 'utf8')
  if (checkOnly) {
    if (!existsSync(output) || readFileSync(output, 'utf8') !== generated) {
      console.error(`${output} is out of date; run npm run generate:api-types`)
      process.exitCode = 1
    }
  } else {
    mkdirSync(dirname(output), { recursive: true })
    writeFileSync(output, generated)
    console.log(`Generated ${output} successfully.`)
  }
} finally {
  rmSync(tempRoot, { recursive: true, force: true })
}
