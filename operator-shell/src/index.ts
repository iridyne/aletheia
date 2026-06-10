import { loadConfig } from './config.js'
import { OperatorShellServer } from './server.js'

async function main(): Promise<void> {
  const config = loadConfig()
  const server = new OperatorShellServer(config)

  const shutdown = async () => {
    await server.stop()
  }

  process.on('SIGINT', () => {
    void shutdown().finally(() => process.exit(0))
  })
  process.on('SIGTERM', () => {
    void shutdown().finally(() => process.exit(0))
  })

  await server.start()
}

void main().catch((error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error)
  console.error(message)
  process.exit(1)
})
