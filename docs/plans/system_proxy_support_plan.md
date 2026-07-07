# System Proxy Support (managed network egress)

Owner directive 2026-07-04: **Option C** — a switchable proxy-enforcement policy,
defaulting to strict semantics, surfaced in the web settings panel with
structured inputs (protocol / host / port) and an enforce toggle.

## First-principles goal

Every outbound network interaction — exchange trading, market data, news, LLM,
webhooks — must be routable through a **single managed proxy path in our control
zone**. When `enforce` is on, the app **fails closed** (a proxy outage blocks the
request rather than silently connecting direct and leaking the real IP). When off,
it preserves the pre-existing fail-open behaviour (direct fallback for
availability).

The single choke point is `aletheia/net.py`.

## Audit result (2026-07-04)

Already correct: `net.py` (`httpx_client` / `aiohttp_session` / ccxt session
injection / `apply_proxy_env`), used by all LLM providers, data collectors, OKX
broker, Bitget market data, Telegram. TS operator-shell and the frontend are
localhost-only (no external egress). No WebSocket streaming (REST only).

Gaps closed by this work:

1. `exchanges/binance/client.py` — ccxt built with **no session** → proxy bypass
   (live broker). Fixed via `net.make_ccxt`.
2. `server/routes/bitget.py` — two `test-candidate` ccxt constructors with no
   session → bypass. Fixed via `net.make_ccxt`.
3. `config.network.no_proxy` was **defined but never wired**. Now
   `apply_proxy_env` sets `NO_PROXY` (loopback + config) and `ALL_PROXY`.
4. Fail-open only — no strict mode. Added `enforce`.
5. Webhook / polymarket-fallback raw `urllib` egress → routed through
   `net.build_urllib_opener`.

## Design

- **Config** (`NetworkConfig`): `proxy_protocol` (http/https/socks5) + `proxy_host`
  + `proxy_port` compose `proxy_url` (kept for consumers). Legacy `proxy_url`-only
  configs migrate back into parts. Empty host = disabled (direct). `enforce: bool`
  (default `False` — see note). `no_proxy` now consumed.
- **net.py**: `make_ccxt(name, **options) -> (exchange, session)` (always injects
  session); `_effective_proxy()` returns the proxy unconditionally under enforce
  (fail-closed) and reachable-gated otherwise; `httpx_client` uses a proxy-only
  client under enforce and the fallback transport otherwise; `aiohttp_session`
  gains a SOCKS branch (`aiohttp_socks`); `build_urllib_opener()` for sync callers.
- **Fail-closed at system level, for free**: the connectivity monitor
  (`agent_runtime/connectivity.py`) already probes via `net.httpx_client`, so an
  enforce-mode proxy outage fails that probe → existing connectivity gate
  auto-pauses trading and auto-resumes on recovery.
- **SOCKS deps**: `httpx[socks]` (socksio) + `aiohttp_socks`, imported lazily so a
  missing dep only affects users who select `socks5`.
- **Settings**: `/settings/network` update model + web panel expose
  protocol/host/port/enforce/no_proxy.
- **Doctor**: proxy reachability (exists) + enforce state + egress-IP leak check.

### Note on the `enforce` default

Schema default is `False` for portability (fresh installs / CI / any machine
without a proxy running would otherwise be bricked by a strict default). The web
panel recommends enabling it for production; it is enabled on the operating
machine after verifying the proxy is reachable.

## Spec

`tests/specs/spec_network_egress.py` — contract `network_egress_contract`,
claims NET-S01..S10. Registered in `test_spec_conformance._import_all_specs`.
