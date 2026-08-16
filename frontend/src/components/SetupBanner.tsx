import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'

/**
 * OpenAlice-style onboarding nudge: if no model provider has ANY form of
 * credential (api_key OR oauth OR cli-provided) AND no exchange has demo/
 * testnet credentials configured (bitget / okx / binance), prompt the user
 * to finish setup. #121 fix: this used to only count `api_key.configured`
 * for models and only Bitget demo for exchanges, so an operator with OAuth-
 * authenticated Claude + OKX demo credentials still saw the setup banner.
 * Not a blocking wizard — just a dismissible-by-completion banner.
 */
export default function SetupBanner() {
  const [ready, setReady] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setReady(true), 800)
    return () => window.clearTimeout(timer)
  }, [])

  const { data: model } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig, enabled: ready })
  const { data: bitget } = useQuery({ queryKey: ['bitgetCreds'], queryFn: api.getBitgetCredStatus, enabled: ready })
  const { data: okx } = useQuery({ queryKey: ['okxCreds'], queryFn: api.getOkxCredStatus, enabled: ready })
  const { data: binance } = useQuery({ queryKey: ['binanceCreds'], queryFn: api.getBinanceCredStatus, enabled: ready })

  if (!model || !bitget || !okx || !binance) return null

  // A model is usable when the provider carries any credential channel — api_key,
  // OAuth, or a CLI-provided binding. Missing all three means the provider is dead
  // to the app, so we only consider the operator "set up" when at least one is live.
  const anyModelUsable = Object.values(model.providers || {}).some((p: any) => {
    if (!p || typeof p !== 'object') return false
    if (p.api_key?.configured) return true
    if (p.oauth?.configured || p.oauth?.active) return true
    if (p.cli?.configured || p.cli_available) return true
    return false
  })
  // Any exchange with demo/testnet credentials counts — OKX and Binance are just
  // as valid as Bitget for the "can start" bar.
  const anyExchangeCreds =
    !!bitget.demo?.api_key?.configured ||
    !!okx.demo?.api_key?.configured ||
    !!binance.testnet?.api_key?.configured
  const needsSetup = !anyModelUsable && !anyExchangeCreds

  if (!needsSetup) return null

  return (
    <div className="setup-banner">
      <span>👋 Finish setup: add a model endpoint (API key / OAuth / CLI) and at least one exchange's demo/testnet credentials to start.</span>
      <Link to="/settings">Go to Settings →</Link>
    </div>
  )
}
