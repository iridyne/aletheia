# Aletheia Trading OS — Operations Runbook

How to take the system live for **paper trading** and run it unattended for long
stretches, accumulating real paper PnL and strategy lessons. Live (real-money)
trading stays disabled until you explicitly unlock it — see [Live trading](#8-live-trading-stays-locked).

> Safety posture (always true): live is `live_disabled` by default; the agent
> cannot unlock live, place live orders, modify risk limits, credentials, or the
> execution gateway; every order flows through the Risk Engine; every important
> action is audited. See `AGENTS.md` and `docs/governance/policies/trust_and_change_control.md`.

---

## 1. Install

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run aletheia version          # Aletheia Trading OS v0.5.0
```

## 2. Start the gateway (dashboard)

```bash
uv run aletheia gateway start    # serves http://127.0.0.1:8787 and opens a browser
uv run aletheia gateway status   # Gateway: running (PID ...)
```

Other lifecycle commands: `gateway stop | restart | pause | resume | logs | open-dashboard`.
`pause` halts all autonomous jobs without stopping the dashboard (the operator brake).

Persistent service commands are separate from runtime control:

```bash
uv run aletheia gateway service install --backend session
uv run aletheia gateway enable          # next login/boot activation; does not start now
uv run aletheia gateway service status
uv run aletheia gateway disable         # does not stop a running Gateway
uv run aletheia gateway service uninstall  # requires stopped; preserves data
```

The gateway runs under a **supervisor**: if the inner server crashes it is
respawned with backoff; after too many restarts in a window it gives up, writes a
`gateway_supervisor_gave_up` audit event, and alerts through the operator
notification sink (Windows toast and Telegram when configured). The inner
server's stderr goes to `~/.aletheia/logs/errors.log` by default.

`8787` is owned by a small local **front door**. The backend worker runs on an
internal loopback port behind it, so the dashboard port can keep serving static
UI and `/_infra/*` diagnostics while the worker is restarting. During that
window, `/api/*` returns a structured `503 backend_unavailable` instead of a raw
connection failure.

The front door reads its active backend from `~/.aletheia/frontdoor-route.json`
by default. The
supervisor refreshes that file on backend spawn, and runtime-continuity promotion
can atomically switch it to a ready shadow backend without rebinding `:8787`.
Inspect `http://127.0.0.1:8787/_infra/status` to see the active
`backend_base_url`, backend readiness, and route metadata.

The production dashboard also has a global backend connection guard. If `/api`
becomes unreachable during restart/self-heal, the page shows a fail-loud banner
and, after a prolonged outage, an overlay with the recovery command. Browser
notifications are opt-in from that banner because browsers require permission.

## 2.5 Worktree frontend and backend preview

`http://127.0.0.1:8787/` remains the daily-use gateway. Worktree UI changes use
Vite HMR on `http://127.0.0.1:5173`; its default proxy can read `:8787` but
rejects writes. It does not restart the gateway. For a backend change, explicitly
start an isolated API on an unused `879x` port; it uses separate data and blocks
secrets and execution. Follow [Development preview](../development/DEVELOPMENT_PREVIEW.md) for
the exact commands and safety contract.

When you're done iterating, run the normal production build once:

```powershell
cd frontend; npm run build
```

The `build` step writes content-hashed assets into `aletheia/dashboard_static/`,
which is what the gateway serves on `:8787`.

## 3. Configure a model provider (required for real analysis)

Everything is configurable from the dashboard **Settings** page (OpenAlice-style),
or via CLI. The agents degrade gracefully if no LLM is reachable (they record a
clean error and keep running), but they only produce *meaningful* research and
directional signals once a provider is configured.

```bash
# Dashboard: Settings → Model Provider → base URL + model + API key → Test  (primary)
# or CLI (deprecated — still works, dashboard is the maintained surface):
uv run aletheia model set --provider openai_compatible --base-url URL --model NAME --api-key
uv run aletheia model status
uv run aletheia model test
```

API keys are write-only: they are stored encrypted (keyring / `secrets.enc`) and
never returned by any GET endpoint, never written to logs, never put in agent
prompts.

## 4. (Optional) Bitget demo credentials

Market data (BTC/ETH/SOL tickers/candles) works **without** credentials via public
endpoints, so the observe/research/paper loop runs out of the box. Demo
credentials are only needed if you want authenticated demo calls.

```bash
# Dashboard: Settings → Exchange Credentials → demo → keys → Test Connection  (primary)
# or CLI (deprecated — still works, dashboard is the maintained surface):
uv run aletheia secrets set bitget.demo.api_key
uv run aletheia secrets set bitget.demo.api_secret
uv run aletheia secrets set bitget.demo.passphrase
uv run aletheia bitget test
```

## 5. Run the autonomous paper loop

The current supported permission mode is `Paper` and is the default. There is no
separate `autonomous_paper_trading` switch. Before expecting entries, verify the
effective Settings/Doctor projection rather than editing `config.yaml` manually:

1. **Permission mode** → `Paper` (`Research`/`ReadOnly` never trade). Dashboard
   Settings → Permission Mode (primary), or CLI:
   ```bash
   uv run aletheia permission set Paper
   ```
2. Confirm the scheduler is enabled and not held by any durable pause owner:
   ```bash
   uv run aletheia inspect config
   uv run aletheia gateway resume
   ```
   The centralized resume gate refuses to run non-essential jobs while any pause
   owner remains; clear the owning condition instead of bypassing it.

Once on, the active paper trading pipeline runs on a schedule with no dashboard clicks:

```
market data → market_observer / universe scanners → durable opportunity
  → paper ranker → signal_pending queue carrier
  → System Operator: trade or evidenced skip
  → immutable Proposal → Risk Engine → Execution Gateway
  → paper Order + Fill + Position (one causal chain)
  → position_manager: point mark + exact completed-1m protective barriers,
     or Operator horizon review
  → close Order + Fill + net PnL + paper cash ledger + eligible Outcome
  → post-trade review / cases / counterfactuals / Learning measurement
  → owner-gated bounded influence on a later Paper decision
```

This proves lifecycle continuity, not profitability. Behavioural Learning influence
is available by default only for the registry-declared measurement-supported path,
but no experiment self-admits: a protected owner action, matching prospective
treatment/control evidence, sufficient actual intervention, one consumed fixed-look
confirmatory verdict, clear harm state, and every authority gate are still required.
Missing, harmful, invalid, monitoring-only, or underpowered evidence falls back to
or retains the declared static behavior.

New protected native Paper positions also receive an immutable exact-instrument 1m
cursor. Missing bars remain visible gaps; the protective worker refreshes only the
frozen venue and never substitutes another venue. The separate entry-lock exit
experiment is prospective and read-only: it records paired net-PnL evidence for new
eligible positions and never changes their real stop or target.

Watch it on the **Agent Runs**, **Opportunities**, **Proposals**, **Positions**,
and **Charts** dashboard pages.

## 6. Tuning (config.yaml)

```yaml
trading:
  default_stop_loss_pct: 2.0       # protective stop distance
  default_take_profit_pct: 4.0     # take-profit distance
  max_position_hold_hours: 72.0    # time-based exit
  min_confidence_to_trade: medium  # high | medium | speculative
risk:                              # HARD limits — agent cannot change these
  max_daily_loss_pct: 1.0          # breached → kill switch auto-trips
  max_single_trade_risk_pct: 0.25
  # ... see Settings → Risk Boundaries (read-only display)
retention:                         # telemetry pruning (trade/audit/knowledge never pruned)
  agent_runs_days: 90
  tool_calls_days: 90
  runtime_sessions_days: 90
  market_snapshots_days: 30
  jsonl_max_mb: 50.0
```

`agent_runs_days`, `tool_calls_days`, and `runtime_sessions_days` default to 90
days and can be set from 1 to 90 in **Settings → Data Retention**. Agent-run
artifact links follow their parent AgentRun retention; changing local settings
does not rewrite a remote Runtime's saved configuration.

The Risk Engine limits are **hard** — they can only be changed by editing config
as a human; the agent can propose changes in a report but cannot apply them.

## 7. The brakes

- **Pause everything:** `aletheia gateway pause` (or dashboard) — stops all
  autonomous jobs, dashboard stays up. `resume` to re-enable.
- **Kill switch (global trading brake):** rejects *every* proposal (paper and
  live) until released. Auto-trips if the daily-loss limit is breached.
  Primary: dashboard **Risk panel**. CLI (deprecated, still works):
  ```bash
  uv run aletheia risk status      # shows kill-switch state + key limits
  uv run aletheia risk kill        # engage
  uv run aletheia risk resume      # release
  ```
  Note: the kill switch blocks *opening* trades but does **not** block the
  position_manager from *closing* existing positions (closing reduces risk).

## 8. Live trading stays locked

Live is `live_disabled` by default and the agent can never unlock it. Moving to
`live_limited` requires explicit human action (Settings → Live State → unlock, or
the live API route) and passes the strategy promotion gates (≥14 paper days, ≥30
trades, positive expectancy, drawdown/consecutive-loss/risk-violation checks, a
post-trade review). LIVE positions are **never** auto-closed by the position
manager. Treat enabling live as a separate, deliberate decision.

## 9. Updating the code (on-demand self-upgrade)

This is owner-triggered, not a daemon. In a source checkout, the primary command is:

```bash
uv run aletheia update
```

That single command fetches and fast-forwards to the configured
`origin/<update.branch>` HEAD, reinstalls changed Python/frontend dependencies,
builds the complete dashboard into an unserved staging directory, runs the smoke
gate, atomically activates the frontend, and restarts the Gateway with readiness
verification. A failed smoke/build/restart rolls back to the recorded pre-update
commit when the migration boundary permits it.

Useful explicit controls remain:

```bash
uv run aletheia update check
uv run aletheia update apply             # confirmation plus default restart
uv run aletheia update apply --no-restart
```

The bare command is source-checkout-only. Packaged runtimes continue through
`aletheia update apply` and the signed-release/Tray activation path. Dirty,
wrong-branch, ahead, or diverged source checkouts fail closed rather than
clobbering local work or inventing a deployment state.

## 10. Health & observability

The detailed stability roadmap and phase gates live in
[`STABILITY_TARGET_ARCHITECTURE.md`](../architecture/STABILITY_TARGET_ARCHITECTURE.md). The key
runtime probes are `/_infra/livez` (process liveness), `/_infra/readyz`
(API+dashboard readiness), and `/api/health` (business/runtime health).

- **Dashboard:** Overview (gateway/live/permission/kill-switch state, account/PnL),
  Agent Runs, Positions, Charts, Reports, Knowledge Base.
- **CLI (bootstrap):** `aletheia doctor` (full ground-truth probe), `aletheia doctor --verbose`
  or `--deep` (redacted log tails + recent failures + next-log pointers),
  `aletheia doctor --bundle ~/.aletheia/diagnostics/doctor.json` (redacted JSON bundle for AI/support),
  `aletheia gateway status`, `aletheia gateway logs` (multi-log redacted tails). (`aletheia risk status` still works but the Risk panel is primary.)
- **Files** under the effective Runtime root (`~/.aletheia/` unless an absolute
  `ALETHEIA_DATA_DIR` is configured):
  - `logs/audit.jsonl` — every state transition (rotates at `retention.jsonl_max_mb`, default 50 MB, numeric generations)
  - `logs/errors.jsonl`, `logs/agent_runs.jsonl` — structured diagnostic streams, same JSONL rotation cap
  - `logs/gateway.log`, `logs/errors.log`, `logs/frontdoor-errors.log`, `logs/operator-shell-errors.log` — text logs split at 50 MB and kept for many generations during MVP/Round 1/Round 2 diagnosis
  - `diagnostics/*.json` — optional redacted doctor bundles generated by `aletheia doctor --bundle`
  - `app.db` — SQLite state; `config.yaml` — config; `secrets.enc` — encrypted secrets
- **Secret rule:** verbose diagnostics are allowed to be noisy, but must stay redacted:
  no API key, secret, passphrase, bearer token, request signature, or raw credential may appear in logs or doctor output.
- **Telegram (optional):** set `telegram.enabled: true` + `allowed_chat_ids`, store
  the token via `aletheia secrets set telegram.bot_token`. A 10-minute health check
  alerts you (transition-only, de-duped) when the agent-failure ratio is high or the
  kill switch trips. Commands: `/status /pause /resume /latest /help`.

## 11. First-run checklist

```text
[ ] aletheia gateway start  → dashboard opens
[ ] Settings → Model Provider configured + Test passes
[ ] (optional) Bitget demo creds + Test Connection
[ ] permission_mode = Paper
[ ] Scheduler is enabled, Gateway is not paused, and no pause owner blocks resume
[ ] Agent Runs shows market_observer producing opportunities
[ ] Positions shows a paper position open, then close with a realized PnL
[ ] (optional) Telegram configured for health alerts
[ ] Leave it running. Check back via dashboard / Telegram.
```
