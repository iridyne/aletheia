import assert from 'node:assert/strict'
import test from 'node:test'

import { BackendClient } from '../src/backend_client.js'
import { PiRuntimeAdapter } from '../src/pi_runtime_adapter.js'
import { OPERATOR_SERVICE_HEADER, OPERATOR_SERVICE_TOKEN_ENV } from '../src/service_auth.js'

test('BackendClient attaches the Operator service credential without replacing JSON headers', async () => {
  const token = 'operator-service-test-token'
  const previousToken = process.env[OPERATOR_SERVICE_TOKEN_ENV]
  const previousFetch = globalThis.fetch
  let capturedHeaders: Headers | null = null

  process.env[OPERATOR_SERVICE_TOKEN_ENV] = token
  globalThis.fetch = async (_input, init) => {
    capturedHeaders = new Headers(init?.headers)
    return new Response(JSON.stringify({ status: 'healthy', issues: [], version: 'test' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    await new BackendClient('http://127.0.0.1:8787').getHealth()
    assert.equal(capturedHeaders?.get(OPERATOR_SERVICE_HEADER), token)
    assert.equal(capturedHeaders?.get('Content-Type'), 'application/json')
  } finally {
    globalThis.fetch = previousFetch
    if (previousToken === undefined) {
      delete process.env[OPERATOR_SERVICE_TOKEN_ENV]
    } else {
      process.env[OPERATOR_SERVICE_TOKEN_ENV] = previousToken
    }
  }
})

test('PiRuntimeAdapter authenticates its raw internal LLM request', async () => {
  const token = 'operator-service-pi-token'
  const previousToken = process.env[OPERATOR_SERVICE_TOKEN_ENV]
  const previousFetch = globalThis.fetch
  let capturedHeaders: Headers | null = null

  process.env[OPERATOR_SERVICE_TOKEN_ENV] = token
  globalThis.fetch = async (_input, init) => {
    capturedHeaders = new Headers(init?.headers)
    return new Response(JSON.stringify({
      status: 'completed',
      turns_used: 1,
      commands_executed: [],
      final_answer: 'ok',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const result = await new PiRuntimeAdapter({} as never, 'http://127.0.0.1:8787').runSession({
      goal: 'check service auth',
    })
    assert.equal(result.status, 'completed')
    assert.equal(capturedHeaders?.get(OPERATOR_SERVICE_HEADER), token)
  } finally {
    globalThis.fetch = previousFetch
    if (previousToken === undefined) {
      delete process.env[OPERATOR_SERVICE_TOKEN_ENV]
    } else {
      process.env[OPERATOR_SERVICE_TOKEN_ENV] = previousToken
    }
  }
})
