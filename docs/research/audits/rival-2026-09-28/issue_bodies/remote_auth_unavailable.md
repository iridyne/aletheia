# [auth][trust-boundary] Config-read errors fail open on Frontdoor diagnostics and WebSocket admission

## Why

配置不可读不能被解释为无需鉴权。当前两个绕过普通 HTTP 中间件的私有入口会在此故障下放行；仅修 WebSocket 不足以关闭本 issue 的 Frontdoor + WS 两个受影响入口。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/gateway/frontdoor.py:71](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/gateway/frontdoor.py#L71) `_remote_auth_check` 捕获配置错误后 return None，调用者将其解释为允许；/_infra/status 和 /_infra/doctor 均使用它。
- [core/aletheia/server/routes/ws.py:69](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/server/routes/ws.py#L69) `_ws_remote_auth_required` 捕获后 return False，ws_realtime 跳过 ticket 检查并 accept/add 到 hub。
- 普通 HTTP `_tunnel_auth_guard` 不把读配置错误变为 local/no-auth。已开启的外部通道可能在配置读取故障期间仍可达；本轮没有创建通道或实际连接。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

让 required、not_required、unavailable 三种事实保持可区分。可复用现有策略解析或最小只读 helper，使 Frontdoor、WS 与普通 HTTP 不发生错误降级；不要求新鉴权框架或缓存。

unavailable 时 Frontdoor 私有 diagnostics 返回不含私有 payload 的明确非成功响应，WS 在 accept/hub.add/采样启动前拒绝，即便请求带旧 ticket 也不能把未知策略当作许可。已知 local 模式保持原行为；已知 remote 模式保留 owner token 与 ticket 的现行验证。公用最小 liveness/readiness 仍按原契约工作。更新现有“fail open”注释，不改变 token/ticket 生命周期。

## Non-goals

不修改 Live/Risk/执行权限，不生成/更换真实 token，不开 Tunnel，不重启 Gateway，不合并 WS 广播并发/超时重构，不增加 last-known-good 授权缓存。

## Affected Authorities

[docs/governance/policies/trust_and_change_control.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/governance/policies/trust_and_change_control.md)；现行普通 HTTP owner-auth 边界及 [docs/operations/development/agent-guides/SAFETY_AND_TRUST.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/operations/development/agent-guides/SAFETY_AND_TRUST.md)

## Verification — 本项验收

- 扩展 `tests/test_frontdoor.py` 和 `tests/test_ws_realtime.py`：注入 load_config 抛错，不要仅 mock helper 返回 True/False。
- /_infra/status、/_infra/doctor 不返回私有诊断；WS 无 ticket、无效/过期 ticket、旧有效 ticket 在策略未知时均不 accept、不入 hub、不启动 sampler。
- 读取成功的 local、Tunnel、Remote Control require_token 矩阵保留；有效 ticket 成功且只能消费一次，重用失败。
- public livez/readyz 保持最小信息；日志不包含 token、配置原文或异常中的秘密。

## 执行授权与交付边界

Owner 于 2026-09-28 要求将已审查修复整理为可独立领取的 issue，并明确期望领取 Agent 完成修复、验证、PR 和合并到 `master`。本条记录该范围内的授权：承担本 issue 的 Agent 应完成该 D0 修复及必要测试、自审、提交、推送、review-ready PR，并在仓库适用门禁满足后合并到 master、核实实际合并结果；不需要在每个常规工程步骤重复请求批准。**Issue 本身不覆盖更高优先级约束，也不授予新的 D1/D2、部署、重启共享 Gateway、修改真实凭证、Live 解锁、历史数据重写或资格窗口重置权限。**

从领取时最新 master 创建隔离分支/工作区，先读该版本 AGENTS.md、SYSTEM_MAP 和匹配指南，核对本 issue 的函数与生产 caller。本文固定 SHA 是证据，不要求从旧 SHA 开发。若该根因已有在途 PR，复用/协调，避免两个 Agent 同时修；若当前 master 已满足，提交实际代码及验收证据说明，不制造空 PR。保留他人改动。

## 验证与合并完成条件

- 使用合成数据、临时目录/SQLite、替身 provider。不得读取真实密钥、连交易所或使用主库来复现。
- 为下文验收建立最小行为回归，证明原行为失败、修复后同一条件通过；不要只匹配源码字符串，也不要跳过或弱化失败断言。
- 首轮反馈执行 `uv run python scripts/dev_check.py`，再执行下文聚焦用例及改动所需的本地 PR 验证。具体 selector 以当前真实测试收集结果为准，零收集/skip 不算通过。不要在 macOS 跑全套；全套不是本 issue 的默认要求。
- 按 `.github/PULL_REQUEST_TEMPLATE.md` 填 PR body，执行 `uv run --no-sync python scripts/ci_preflight.py --pr-body-file <实际文件>` 和当前 `scripts/ci_local.py` 对该变更要求的本地检查；保留命令、结果、失败日志及其对应 HEAD。此处 `<实际文件>` 是领取者创建的 PR body 文件，不能原样执行。
- 远程 GitHub CI 在本次复核的 root AGENTS.md 中已退役，不能启动或等待它。遵循领取时适用规则；若分支保护仍要求不可用检查，报告具体阻塞，不绕过、不改保护、不伪造绿灯。
- 按 PR_REVIEW_LIFECYCLE 处理当前 HEAD 上的有效反馈，记录 `MERGE_READY_SHA`。合并前重新核对 PR HEAD、目标 master、测试证据及硬阻塞；若解决冲突产生新代码，重新验证受影响路径。
- 完成必须提供：修复 commit、PR URL、实际 `MERGED` 状态、merge commit SHA、更新后的 master 已包含该 merge 的证据，以及本 issue 验收结果。开启自动合并、PR 可合并、测试通过均不等于已合并。合并并核实后关闭 issue；真实门禁阻塞时保持未完成并报告，不能提前关闭。

Runtime / Economic Verification：本条交付是仓库级契约修复与合并，不要求部署或真实经济实验；明确记录 Fedora 未验证，不声称线上已修好。

<details>
<summary>历史 issue 正文（保留原始证据；旧路径、数量、建议以本次复核为准）</summary>

## Severity

**High**

## Review baseline

Reviewed against authoritative `iridyne/aletheia` `master@fae85eab218efc248cdcdfb6b6fe4054da2da092`.

## Problem

The remote-owner authentication policy is fail-closed in the main HTTP middleware, but the two non-standard public-boundary helpers introduced to close #1075 and #1076 both interpret **“config cannot be read” as “remote auth is not required.”**

That creates a deterministic trust-boundary regression while a previously-started Tunnel / Remote Control endpoint remains reachable:

1. `/_infra/status` becomes anonymously readable and returns rich private runtime diagnostics.
2. `/api/ws` stops requiring a short-lived owner-authenticated ticket and accepts anonymous WebSocket clients into the realtime hub.

This is distinct from the already-closed #1075/#1076 defects: ordinary remote mode with a readable config is now protected. The residual failure occurs specifically when the security policy cannot be resolved.

## Code evidence

### Frontdoor private diagnostics fail open

`aletheia/gateway/frontdoor.py::_remote_auth_check()` explicitly does:

```python
try:
    cfg = load_config(persist_migrations=False)
except Exception:
    return None
```

`None` means “allowed” to its callers. `create_app()` then handles `/_infra/status` as:

```python
if (denied := _remote_auth_check(request)):
    return denied
payload = _frontdoor_payload(...)
payload["backend"] = await _probe_backend(...)
payload["events"] = list_infra_events(...)
return JSONResponse(...)
```

The rich payload contains `pid`, uptime, absolute `static_dir`, `backend_base_url`, runtime route metadata, backend readiness and recent warning/critical infra events.

`/_infra/doctor` uses the same fail-open helper before proxying to the backend. The backend is likely to fail its own config read rather than authorize the request, but the Frontdoor boundary itself still makes the wrong authorization decision.

### WebSocket admission has the same fail-open policy

`aletheia/server/routes/ws.py::_ws_remote_auth_required()` does:

```python
try:
    cfg = load_config(persist_migrations=False)
except Exception:
    return False
```

`ws_realtime()` only consumes a ticket when that helper returns true:

```python
if _ws_remote_auth_required():
    if not ticket or not _consume_ws_ticket(ticket):
        await ws.close(code=4403)
        return
...
await ws.accept()
await _hub.add(ws)
```

Therefore an unreadable config converts an unknown security state into anonymous WebSocket admission. The hub broadcasts private activity categories such as positions, fills, proposals, risk decisions, agent runs, runtime events and infra events. It has a 50-client cap, so this is bounded but still unauthorized metadata access and resource use.

### Main HTTP auth does not make this downgrade

`aletheia/server/app.py::_tunnel_auth_guard()` directly calls `load_config(persist_migrations=False)` for private routes and does not translate a config exception into `tunnel_enabled=False` / `remote_token_required=False`. A config failure therefore cannot silently authorize private HTTP API access.

The Frontdoor and WebSocket helpers are thus weaker than the repository's own default-deny owner-auth contract.

### Regression coverage misses the failure mode

- `tests/test_frontdoor.py` covers readable local config and readable remote config with missing/wrong/correct tokens, but not `load_config()` raising.
- `tests/test_ws_realtime.py` monkeypatches `_ws_remote_auth_required()` directly to `True` or `False`; it does not prove that a config-read exception rejects an unauthenticated WebSocket.

## Trigger / reproduction

A practical sequence is:

1. Start Aletheia with Tunnel or token-required Remote Control enabled and a valid owner token. The external tunnel/frontdoor process is already running.
2. Make `config.yaml` temporarily unreadable or invalid after startup (manual edit, permission/storage failure, or another config-path fault). The already-running cloudflared/frontdoor route does not disappear merely because the application config can no longer be parsed.
3. Send unauthenticated `GET /_infra/status` to the exposed Frontdoor.
4. `_remote_auth_check()` catches the config error and returns `None`; the rich status payload is returned instead of failing closed.
5. Open `wss://<remote-origin>/api/ws` with no ticket.
6. `_ws_remote_auth_required()` catches the same class of error and returns `False`; the connection is accepted and receives realtime change-category metadata.

This does not require guessing the configured token. It requires only a period where the security policy is unreadable while the already-established network exposure remains reachable.

## Impact

- Private runtime paths, backend route information, PID/uptime and recent infra-event details can leak through `/_infra/status` during a config incident.
- Anonymous remote clients can observe timing/categories of trading and operator activity through `/api/ws` during the same incident.
- Unknown policy state is silently downgraded to local/no-auth semantics at exactly the two boundaries that bypass normal HTTP middleware.
- A config integrity incident and an authentication downgrade become coupled: the condition in which policy authority is least trustworthy is the condition in which these helpers grant the most access.

## Root cause

Remote-auth policy resolution is duplicated across three places with different failure semantics:

- backend HTTP middleware: effectively fail-closed on config-read failure;
- Frontdoor diagnostics helper: exception -> allow;
- WebSocket helper: exception -> auth not required.

The security decision is therefore represented as a boolean/`None` instead of a three-state result such as `required | not_required | unavailable`, and `unavailable` is being collapsed into the permissive state.

## Recommended fix

Create one shared, read-only remote-auth policy resolver used by backend HTTP, Frontdoor and WebSocket admission.

Required semantics:

- `required`: validate owner token/ticket normally;
- `not_required`: preserve frictionless local mode;
- `unavailable`: **fail closed for every private surface**.

For `unavailable`:

- Frontdoor `/_infra/status` / `/_infra/doctor` should return a typed 503 or authentication-unavailable response without private payloads;
- `/api/ws` should reject before `accept()` / `_hub.add()` (for example 4403/1013 with no data); and
- only the intentionally public minimal `/_infra/livez`, `/_infra/readyz`, and `/api/ready` surfaces should remain reachable.

If last-known-good policy caching is introduced for resilience, an unknown/error state must never downgrade a previously-required policy to unauthenticated access.

## Required regression coverage

- Tunnel enabled + `load_config()` raises: anonymous `/_infra/status` returns no private payload and is non-2xx/private-denied.
- Token-required Remote Control + `load_config()` raises: same result.
- Config error + `/api/ws` with no ticket: rejected before hub admission.
- Config error + stale/invalid ticket: rejected.
- Public `/_infra/livez` / `/_infra/readyz` remain minimal and reachable during the same config error.
- Readable local config still permits the intended local no-auth behavior.
- A shared policy-resolver test prevents Frontdoor/WebSocket semantics from drifting from backend HTTP again.

## Related

- #1075 / PR #1162 — Frontdoor infra owner-auth repair; this is a residual fail-open branch after that fix.
- #1076 — WebSocket owner-auth repair; this is the corresponding residual fail-open branch.
- #1178 / PR #1315 — network-policy config-read errors were explicitly changed to fail closed; the same unknown-policy principle should apply here.

## Confidence

**High.** Both permissive exception branches are explicit, both sit before authorization/admission, and the main HTTP middleware demonstrates the stronger intended default-deny behavior.

</details>
