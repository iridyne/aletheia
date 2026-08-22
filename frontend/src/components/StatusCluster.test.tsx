import { describe, expect, it } from 'vitest'
import SRC from './StatusCluster.tsx?raw'
import BOOT_SRC from '../api/boot.ts?raw'

describe('StatusCluster permission scope presentation', () => {
  it('types and renders worker and operator permissions separately', () => {
    expect(BOOT_SRC).toContain('permission_mode?: string')
    expect(SRC).toContain("const operatorPermission = String(operator?.permission_mode || data?.operator?.permission_mode || '')")
    expect(SRC).toContain('Worker / Trading permission')
    expect(SRC).toContain('Operator permission')
  })

  it('names the cycle control as the worker/trading scope', () => {
    expect(SRC).toContain('Worker / Trading permission mode:')
    expect(SRC).not.toContain('title={`Permission mode: ${permission} — click to cycle`}')
  })
})
