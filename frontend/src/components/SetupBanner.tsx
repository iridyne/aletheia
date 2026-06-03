import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'

/**
 * OpenAlice-style onboarding nudge: if no model provider has an API key AND
 * no Bitget credentials are configured, prompt the user to finish setup.
 * Not a blocking wizard — just a dismissible-by-completion banner.
 */
export default function SetupBanner() {
  const { data: model } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: creds } = useQuery({ queryKey: ['bitgetCreds'], queryFn: api.getBitgetCredStatus })

  if (!model || !creds) return null

  const anyModelKey = Object.values(model.providers || {}).some((p: any) => p?.api_key?.configured)
  const anyDemoCreds = creds.demo?.api_key?.configured
  const needsSetup = !anyModelKey && !anyDemoCreds

  if (!needsSetup) return null

  return (
    <div className="setup-banner">
      <span>👋 Finish setup: add a model endpoint and Bitget demo credentials to start.</span>
      <Link to="/settings">Go to Settings →</Link>
    </div>
  )
}
