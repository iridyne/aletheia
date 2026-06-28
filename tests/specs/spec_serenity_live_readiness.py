"""Spec: Serenity live-readiness — Phase 5 (D4 structural live-prep only)

Phase 5 of the Serenity execution path (docs/plans/serenity_execution_path.md §6).
Goal: confirm that the trade_school discriminator threads through the LIVE
execution paths exactly the way it threads through paper, so the system is
ready to flip a single live unlock switch without any further model-layer
plumbing. **No live behavior is enabled by Phase 5** — the live unlock route
and the risk-engine live guard are explicitly verified to be unchanged.

Phase 4 (spec_serenity_holding SH-S05) already established that:
  - paper_tools.create_paper_proposal + gateway.submit_proposal accept trade_school
  - all four broker Position(...) constructors propagate it
  - position_reconciler stamps it onto adopted positions

Phase 5 promotes those checks to a stricter live-readiness contract by asserting
the EXACT live dispatch paths — the three real-exchange broker entry points
(`bitget_broker.execute_live_order`, `binance_broker.execute_live_order`,
`okx_broker.execute_live_order`) — accept and forward the field, the gateway
threads `proposal.trade_school` into ALL THREE live broker call sites (both the
*_demo path and the *_live_limited path), and the live unlock surface area
(`server/routes/live.py` + the risk engine's live-disabled guard) is structurally
untouched: the school discriminator does NOT and CANNOT bypass live mode gating.

Contract: serenity_live_readiness (logical contract — registered in the
conformance matrix but NOT in REQUIRED_CONTRACTS, mirroring the Phase 2/3/4
behavioral pipeline specs).
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_live_readiness"
_ROOT = Path(__file__).parent.parent.parent
_GATEWAY = _ROOT / "aletheia" / "execution" / "gateway.py"
_BITGET_BROKER = _ROOT / "aletheia" / "execution" / "bitget_broker.py"
_BINANCE_BROKER = _ROOT / "aletheia" / "execution" / "binance_broker.py"
_OKX_BROKER = _ROOT / "aletheia" / "execution" / "okx_broker.py"
_RISK_ENGINE = _ROOT / "aletheia" / "risk" / "engine.py"
_LIVE_ROUTE = _ROOT / "aletheia" / "server" / "routes" / "live.py"
_SCHEMA = _ROOT / "aletheia" / "config" / "schema.py"


def _slice_function(src: str, signature_prefix: str) -> str:
    """Return the body of a top-level function from `signature_prefix` (e.g.
    "def execute_live_order(") to the next top-level `def `/`async def `.

    Used to anchor structural assertions inside a specific function rather than
    relying on whole-file substring checks.
    """
    start = src.index(signature_prefix)
    tail = src[start:]
    # Look for the next top-level def/async def — column 0
    nxt_def = tail.find("\ndef ", 1)
    nxt_async = tail.find("\nasync def ", 1)
    candidates = [c for c in (nxt_def, nxt_async) if c > 0]
    end = min(candidates) if candidates else len(tail)
    return tail[:end]


# ---------------------------------------------------------------------------
# L2a — Structural live-readiness
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LR-S01", _CONTRACT, "§live brokers — execute_live_order accepts trade_school", "L2a",
        "All three real-exchange brokers (bitget/binance/okx) expose an "
        "execute_live_order(...) signature that accepts trade_school: str with a "
        "default of 'short_term', so the field can be threaded from the gateway "
        "without breaking either the demo (*_demo / *_testnet) or live-limited "
        "(*_live_limited) call paths",
    ),
)
def test_live_brokers_accept_trade_school():
    for path in (_BITGET_BROKER, _BINANCE_BROKER, _OKX_BROKER):
        src = path.read_text(encoding="utf-8")
        body = _slice_function(src, "def execute_live_order(")
        assert "trade_school" in body, (
            f"{path.name}.execute_live_order is missing the trade_school parameter — "
            f"live-readiness regression: the gateway cannot forward the school field "
            f"to this broker without an arg mismatch (Phase 4 SH-S05 invariant)"
        )
        # Must default to 'short_term' so legacy callers (or any code path that
        # forgets to set it) get the safe short-term behavior, never accidentally
        # serenity (which has wider stops + 90d hold cap).
        assert (
            'trade_school: str = "short_term"' in body
            or "trade_school: str = 'short_term'" in body
        ), (
            f"{path.name}.execute_live_order trade_school default is not "
            f"'short_term' — a legacy caller would silently put the position into "
            f"the long-horizon serenity school (clone-divergence guard)"
        )


@spec_claim(
    SpecClaim(
        "LR-S02", _CONTRACT, "§gateway — proposal.trade_school threads into all 3 live dispatch paths", "L2a",
        "execution_gateway.submit_proposal forwards proposal.trade_school into the "
        "demo broker call AND the live_limited broker call. Both call sites pass "
        "trade_school=proposal.trade_school by keyword so adding a new live mode "
        "later cannot silently drop the field",
    ),
)
def test_gateway_threads_trade_school_into_live_paths():
    src = _GATEWAY.read_text(encoding="utf-8")
    # The gateway calls execute_live_order TWICE inside submit_proposal — once for
    # bitget_demo/binance_testnet/okx_demo, once for *_live_limited. We anchor each
    # block on its discriminator (the elif allowed_mode in (...) line) and assert
    # trade_school=proposal.trade_school is present in each.

    # Demo / testnet path (the *_demo branch)
    demo_anchor = 'elif allowed_mode in ("bitget_demo", "binance_testnet", "okx_demo")'
    assert demo_anchor in src, (
        "gateway lost the demo/testnet branch anchor — submit_proposal structure "
        "has drifted; re-verify the trade_school dispatch path"
    )
    demo_start = src.index(demo_anchor)
    # Next elif/except marker ends the block
    demo_tail = src[demo_start:]
    nxt_elif = demo_tail.find("\n        elif allowed_mode")
    nxt_else = demo_tail.find("\n        else:")
    demo_candidates = [c for c in (nxt_elif, nxt_else) if c > 0]
    demo_end = min(demo_candidates) if demo_candidates else len(demo_tail)
    demo_block = demo_tail[:demo_end]
    assert "trade_school=proposal.trade_school" in demo_block, (
        "gateway demo/testnet broker call does NOT pass "
        "trade_school=proposal.trade_school — paper-vs-demo divergence: paper "
        "positions get the school, demo (real-exchange-record) positions don't"
    )

    # Live-limited path
    live_anchor = 'elif allowed_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited")'
    assert live_anchor in src, (
        "gateway lost the live_limited branch anchor — submit_proposal structure "
        "has drifted; re-verify the trade_school dispatch path"
    )
    live_start = src.index(live_anchor)
    live_tail = src[live_start:]
    nxt_elif_l = live_tail.find("\n        elif allowed_mode")
    nxt_else_l = live_tail.find("\n        else:")
    live_candidates = [c for c in (nxt_elif_l, nxt_else_l) if c > 0]
    live_end = min(live_candidates) if live_candidates else len(live_tail)
    live_block = live_tail[:live_end]
    assert "trade_school=proposal.trade_school" in live_block, (
        "gateway live_limited broker call does NOT pass "
        "trade_school=proposal.trade_school — flipping live unlock would put REAL "
        "funds into positions with NULL/default school (the exact clone-divergence "
        "bug aletheia-broker-stop-drop-bug.md memory warns against)"
    )


@spec_claim(
    SpecClaim(
        "LR-S03", _CONTRACT, "§live brokers — Position(...) constructor sets trade_school", "L2a",
        "Each live broker's Position(...) construction inside execute_live_order "
        "assigns trade_school=trade_school so position_manager can branch on the "
        "school. Without this assignment the field would be defaulted to 'short_term' "
        "by Pydantic and serenity holdings would silently get the 72h max_hold cap",
    ),
)
def test_live_brokers_assign_trade_school_to_position():
    for path in (_BITGET_BROKER, _BINANCE_BROKER, _OKX_BROKER):
        src = path.read_text(encoding="utf-8")
        # Find every Position(...) construction in execute_live_order's body and
        # require at least one carries trade_school=trade_school. (Some brokers may
        # create + update; we only need the open-position write.)
        body = _slice_function(src, "def execute_live_order(")
        assert "Position(" in body, (
            f"{path.name}.execute_live_order has no Position(...) construction — "
            f"the live broker is no longer opening positions locally; "
            f"live-readiness invariant cannot be verified"
        )
        assert "trade_school=trade_school" in body, (
            f"{path.name}.execute_live_order does not stamp "
            f"trade_school=trade_school on the new Position — long-horizon "
            f"serenity holdings opened via this live broker would silently fall "
            f"back to 'short_term' (Pydantic default) and get exited at the 72h cap"
        )


@spec_claim(
    SpecClaim(
        "LR-S04", _CONTRACT, "§trust boundary — live unlock route is untouched by school", "L2a",
        "server/routes/live.py does NOT reference trade_school anywhere — the live "
        "unlock policy is school-agnostic. A serenity proposal cannot bypass "
        "live_disabled, and the live unlock route does not branch on school. This "
        "is the change-controlled trust boundary (CLAUDE.md) — Phase 5 explicitly "
        "verifies it stays untouched",
    ),
)
def test_live_unlock_route_is_school_agnostic():
    src = _LIVE_ROUTE.read_text(encoding="utf-8")
    assert "trade_school" not in src, (
        "server/routes/live.py mentions trade_school — the live unlock surface "
        "MUST be school-agnostic: any per-school live policy is a trust-boundary "
        "change requiring owner approval (CLAUDE.md). Phase 5 is structural-only"
    )
    # Structural defense-in-depth: confirm the live unlock route still hard-requires
    # explicit confirm=true and still defaults to live_disabled via the route's
    # state machine (we anchor on the literal strings to catch silent rewrites).
    assert "confirm=true" in src or "confirm: bool = False" in src, (
        "live unlock route lost its explicit-confirmation gate — autonomous live "
        "unlock could now succeed (CLAUDE.md safety rule violation)"
    )
    assert '"live_disabled"' in src, (
        "live unlock route no longer references the live_disabled state — the "
        "state machine has been rewritten; live unlock policy needs re-audit"
    )


@spec_claim(
    SpecClaim(
        "LR-S05", _CONTRACT, "§risk engine — live-disabled guard still enumerates all 3 live modes", "L2a",
        "risk_engine still rejects proposals whose execution_mode is any "
        "*_live_limited when cfg.live.state == 'live_disabled'. The serenity school "
        "does NOT and CANNOT bypass this guard — the discriminator lives at the "
        "EXECUTION level, the live gate lives at the MODE level, and they are "
        "orthogonal. Phase 5 verifies the live gate is structurally unchanged",
    ),
)
def test_risk_engine_live_guard_unchanged():
    src = _RISK_ENGINE.read_text(encoding="utf-8")
    # The live-disabled guard
    assert "live_disabled" in src, (
        "risk engine no longer mentions live_disabled — the live gate has been "
        "removed (or renamed); live trading could now run without explicit unlock"
    )
    # Must enumerate all 3 live modes — losing one is a hole (e.g. a new exchange
    # added without updating the guard would slip past).
    for mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited"):
        assert mode in src, (
            f"risk engine no longer enumerates {mode!r} in the live guard — that "
            f"exchange's live mode could bypass the live_disabled rejection"
        )
    # Crucial: the risk engine must NOT branch on trade_school for the live gate.
    # If it did, a serenity proposal could conceivably bypass live_disabled. Phase 5
    # is paper-only — the school is INVISIBLE to the live gate.
    # Slice the function that applies the live_disabled guard and assert
    # trade_school is not referenced inside the guard block.
    guard_anchor = 'if proposal.execution_mode in ("bitget_live_limited"'
    assert guard_anchor in src, (
        "risk engine live-disabled guard anchor missing — structure drifted; "
        "Phase 5 live-readiness invariant cannot be re-verified"
    )
    guard_start = src.index(guard_anchor)
    # Bound the slice tightly: 5 lines is enough to capture the if + violation append
    guard_block = src[guard_start:guard_start + 500]
    assert "trade_school" not in guard_block, (
        "risk engine live-disabled guard branches on trade_school — the school "
        "MUST be invisible to the live gate (orthogonality invariant). A serenity "
        "proposal cannot have a different live unlock requirement than a "
        "short-term proposal"
    )


@spec_claim(
    SpecClaim(
        "LR-S06", _CONTRACT, "§config — LiveConfig default is live_disabled, autonomous unlock impossible", "L2a",
        "LiveConfig.state defaults to 'live_disabled' and first_live_confirmation_done "
        "defaults to False — a fresh install or a re-init can NEVER autonomously "
        "enable live trading. Phase 5 does not change this; the structural live-prep "
        "is paper-only by construction",
    ),
)
def test_live_config_default_is_disabled():
    from aletheia.config.schema import LiveConfig

    cfg = LiveConfig()
    assert cfg.state == "live_disabled", (
        f"LiveConfig.state default is not 'live_disabled' (got {cfg.state!r}) — "
        f"a fresh config or reset would put the system into a live-eligible state "
        f"without explicit operator action (CLAUDE.md safety rule violation)"
    )
    assert cfg.first_live_confirmation_done is False, (
        f"LiveConfig.first_live_confirmation_done default is not False (got "
        f"{cfg.first_live_confirmation_done!r}) — the first-time acknowledgement "
        f"gate is structurally bypassed, the live unlock route's "
        f"acknowledge_first_unlock=true requirement does not apply"
    )
