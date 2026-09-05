import { loadConfig } from './config.js'
import { OperatorLoop } from './operator_loop.js'

async function main(): Promise<void> {
  const config = loadConfig()
  const loop = new OperatorLoop(config)

  const shutdown = async () => {
    await loop.stop()
  }

  process.on('SIGINT', () => {
    void shutdown().finally(() => process.exit(0))
  })
  process.on('SIGTERM', () => {
    void shutdown().finally(() => process.exit(0))
  })

  await loop.start()
}

void main().catch((error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error)
  console.error(message)
  process.exit(1)
})
