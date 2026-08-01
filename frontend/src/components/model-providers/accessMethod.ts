export type AccessMethod = 'oauth' | 'cli'

export function preferredAccessMethod({
  oauthName,
  cliName,
  defaultName,
  deepName,
  fastName,
  oauthEnabled,
  cliEnabled,
}: {
  oauthName: string
  cliName: string
  defaultName: string
  deepName: string | null
  fastName: string | null
  oauthEnabled: boolean
  cliEnabled: boolean
}): AccessMethod {
  const routedIdentity = [defaultName, deepName, fastName].find(
    name => name === oauthName || name === cliName,
  )
  if (routedIdentity === cliName) return 'cli'
  if (routedIdentity === oauthName) return 'oauth'
  if (oauthEnabled) return 'oauth'
  if (cliEnabled) return 'cli'
  return 'oauth'
}
