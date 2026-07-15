const MASTER_API_TARGET = 'http://127.0.0.1:8787'

function isMasterTarget(target: string): boolean {
  try {
    const url = new URL(target)
    return ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(url.hostname.toLowerCase())
      && (url.port || '80') === '8787'
  } catch {
    return false
  }
}

export function devPreviewLabel(): string | null {
  if (!import.meta.env.DEV) return null
  const target = import.meta.env.VITE_API_TARGET || MASTER_API_TARGET
  return isMasterTarget(target)
    ? 'Worktree HMR · master API read-only'
    : `Worktree HMR · API ${target} · execution blocked by preview API`
}
