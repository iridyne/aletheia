"""Every API path a first-party consumer calls must exist and be authorized.

The incident this guards (2026-09-29, #3763): #3702 retired the Current Strategy
product surfaces and cleaned the routes, the CLI, the doctor probe, the operator
context section, the agent tool, two repository functions, the *frontend* client's
`getStrategies`, and `/api/strategies/summary` from the operator allowlist — and
missed the in-repo `operator-shell/`, which kept calling that path. Nothing told
anyone: the operator recorded `gateway_request_failed:404:GET
/api/strategies/summary`, degraded its own mode, and the status panel reported
BLOCKED while `/api/health` was `healthy` with zero issues.

So the three facts that must agree are checked together, in one place:

- every path a consumer calls exists in the application's route table;
- every path the operator shell calls is authorized by the operator allowlist;
- every path the operator allowlist authorizes still exists (no dead entries).

Scope note (SOP-14 discipline): this is a *static reference* check over two
first-party consumer surfaces. It cannot see a consumer outside this repository,
and it says nothing about whether a call is answered correctly — only that the
route it names exists and is permitted.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPERATOR_CLIENT = ROOT / "operator-shell" / "src" / "backend_client.ts"
FRONTEND_CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"

_PATH_RE = re.compile(r"/api/[A-Za-z0-9_\-/.]*")
_TEMPLATE_RE = re.compile(r"\$\{[^}]*\}")
_PLACEHOLDER = "consumer-path-segment"


def _app_routes() -> dict[str, set[str]]:
    """``{route path: {METHODS}}`` for the application that actually serves traffic."""
    from aletheia.server.app import app

    routes: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if isinstance(path, str) and path.startswith("/api") and methods:
            routes.setdefault(path, set()).update(m for m in methods if m != "HEAD")
    return routes


def _route_matcher(route_path: str) -> re.Pattern[str]:
    """A route template (``/api/orders/{order_id}``) as an exact-match regex."""
    parts = re.split(r"\{[^}]+\}", route_path)
    return re.compile("^" + "[^/]+".join(re.escape(part) for part in parts) + "$")


def _without_comments(text: str) -> str:
    """Drop ``//`` line comments and ``/* ... */`` blocks.

    The frontend's client documents endpoint shapes in prose (``/api/economics/*``
    appears in two comments and nowhere else), so a plain text scan would flag a
    path nobody calls. Only real call sites survive this.
    """
    # Line comments first: a prose mention of a glob like ``/api/economics/*``
    # contains ``/*`` and would otherwise start a block comment that swallows the
    # rest of the file.
    stripped = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
    return re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)


def _consumer_paths(source: Path) -> set[str]:
    """Every ``/api/...`` path a consumer source names, template parts masked.

    Comments are removed first: the frontend's client documents endpoint shapes in
    prose (`/api/economics/*` appears in two comments and nowhere else), so a raw
    text scan would flag a path nobody calls.
    """
    names: set[str] = set()
    for raw in _PATH_RE.findall(_without_comments(source.read_text(encoding="utf-8"))):
        masked = _TEMPLATE_RE.sub(_PLACEHOLDER, raw).split("?")[0].rstrip("/")
        names.add(masked)
    return {name for name in names if name.startswith("/api/")}


def _unmatched(paths: set[str], routes: dict[str, set[str]]) -> list[str]:
    matchers = [_route_matcher(route) for route in routes]
    return sorted(path for path in paths if not any(m.match(path) for m in matchers))


def test_the_retired_route_from_the_incident_is_gone_from_the_route_table():
    """Positive control: the checker must be able to fail on the real incident.

    A guard nobody has seen fail is a guard nobody should trust, so this pins the
    exact path from #3763: it is absent from the served routes, and `_unmatched`
    reports it.
    """
    routes = _app_routes()
    retired = "/api/strategies/summary"

    assert retired not in routes
    assert _unmatched({retired}, routes) == [retired]


def test_operator_shell_only_calls_routes_that_exist():
    routes = _app_routes()
    called = _consumer_paths(OPERATOR_CLIENT)
    assert called, "the operator client must name at least one API path"

    missing = _unmatched(called, routes)
    assert not missing, (
        "operator-shell calls API paths the backend does not serve: "
        f"{missing}. A retired route reached from here degrades the operator and "
        "the status panel reports BLOCKED (#3763)."
    )


def test_operator_shell_calls_are_authorized_by_the_allowlist():
    """Path-level authorization: the shell may reach this path at all.

    The check does not extract the HTTP method from the TypeScript call site, so a
    path is accepted when *any* method the operator surface permits allows it
    (reads are GET; `startAgentRun` is the one declared POST mutation). Method
    correctness is the operator's own business and is covered by
    `tests/test_operator_service_auth.py`.
    """
    from aletheia.server.operator_service_auth import operator_service_route_allowed

    unauthorized = sorted(
        path for path in _consumer_paths(OPERATOR_CLIENT)
        if not (operator_service_route_allowed("GET", path)
                or operator_service_route_allowed("POST", path))
    )
    assert not unauthorized, (
        f"operator-shell calls paths the operator allowlist refuses: {unauthorized}"
    )


def test_operator_allowlist_has_no_dead_entries():
    from aletheia.server.operator_service_auth import _OPERATOR_SERVICE_READ_PATHS

    missing = _unmatched(set(_OPERATOR_SERVICE_READ_PATHS), _app_routes())
    assert not missing, (
        f"the operator allowlist authorizes paths nothing serves: {missing}. "
        "Retire the entry with the route."
    )


def test_frontend_client_paths_are_not_checkable_here():
    """Record the limit honestly instead of implying coverage that does not exist.

    `frontend/src/api/client.ts` names no `/api/...` literal: it composes URLs from
    a base constant, so this static check cannot see the frontend's surface at all
    (its type-level coupling is covered by `npm run check:api-types`, which pins the
    generated schema, not route existence). If that surface regains literals, fold
    it into the two checks above rather than leaving this placeholder in place.
    """
    from tests.test_api_consumer_paths import _consumer_paths

    assert _consumer_paths(FRONTEND_CLIENT) == set(), (
        "the frontend client now names API paths directly — extend "
        "test_operator_shell_only_calls_routes_that_exist into a shared helper and "
        "cover this surface too, instead of keeping this placeholder."
    )
