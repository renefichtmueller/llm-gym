"""Cross-origin (CSRF) defense for the state-changing API.

Why this exists — the AgentForger class of attack
--------------------------------------------------
Zenity Labs' "AgentForger" (June 2026) showed that a single crafted link,
opened by a logged-in user, was enough to make a web app perform privileged
state-changing actions on the victim's behalf — a tailored Cross-Site Request
Forgery (CSRF) that stood up an autonomous agent inside the org. OpenAI's fix
was to stop honouring the abusable request as if it came from the user.

LLM Gym has the same structural exposure: its state-changing endpoints (train,
deploy, collect, pool push/pull, pipeline start, settings) are plain
POST/DELETE handlers. Several take only query parameters and no JSON body, so a
browser will send them cross-site with NO CORS preflight — the classic CSRF
sink. On the default local install there is no auth at all, so any web page the
operator happens to open could drive the gym: kick off outbound web collection,
push the training pool to a configured Git remote (data exfiltration), deploy a
model, or start the auto-pipeline (bypassing the human gates).

The control
-----------
Verify the request's `Origin` (falling back to `Referer`) on every unsafe
method and reject anything that isn't same-site. This is the OWASP-recommended
CSRF defense for header-authenticated / no-cookie APIs, and it composes cleanly
with the optional HTTP Basic auth already in app.py.

It is deliberately self-configuring for the common cases and fails safe:

  * Same-site is proven WITHOUT config by comparing the source origin's host to
    the request's own `Host` header — a forged request from evil.example carries
    `Origin: https://evil.example` but `Host:` the gym's own address, so the two
    never match. This covers localhost AND reverse-proxy/hostname installs where
    the proxy preserves Host.
  * An explicit allowlist (`settings.allowed_origins` /
    `LLMGYM_ALLOWED_ORIGINS`) covers the rest (e.g. a proxy that rewrites Host).
  * A request with NO Origin and NO Referer is allowed: those are non-browser
    clients (curl, scripts, the CLI) which are not the CSRF threat model, and a
    browser always attaches Origin to a cross-site unsafe request.
  * `Origin: null` (sandboxed iframe, `data:`/`file:` document) is rejected.

Safe, side-effect-free methods (GET/HEAD/OPTIONS) are never blocked, so the UI,
the OPTIONS preflight and all read paths are untouched.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Methods that change state and therefore need an origin check. GET/HEAD/OPTIONS
# are safe (and OPTIONS must pass so CORS preflight works).
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Always-trusted loopback hosts (the default 127.0.0.1 bind, reachable as any of
# these). IPv6 loopback is stored without brackets — _host_of strips them.
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _host_of(value: str) -> str:
    """Lowercased host from an Origin, a Referer/URL, or a Host header.

    Strips scheme, any userinfo, path/query, and the port, and unwraps an IPv6
    literal's brackets. Returns "" when nothing host-like can be parsed. The
    special Origin value "null" is preserved (returned as "null") so the caller
    can reject it explicitly rather than treating it as unparseable.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if v.lower() == "null":
        return "null"
    # Drop scheme.
    if "://" in v:
        v = v.split("://", 1)[1]
    # Drop path/query/fragment.
    for sep in ("/", "?", "#"):
        if sep in v:
            v = v.split(sep, 1)[0]
    # Drop userinfo (user:pass@host).
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    if not v:
        return ""
    # IPv6 literal: [::1]:8000 -> ::1
    if v.startswith("["):
        end = v.find("]")
        if end != -1:
            return v[1:end].lower()
        return v.lower()
    # host[:port] -> host
    if ":" in v:
        v = v.split(":", 1)[0]
    return v.lower()


def build_allowed_hosts(settings) -> set[str]:
    """The set of hosts that count as same-site, from settings + loopback.

    Includes the configured bind host (unless it's a wildcard, which names no
    real origin) and every host parsed out of `allowed_origins`. Entries in
    `allowed_origins` may be bare hosts ("gym.internal") or full origins
    ("https://gym.internal:8443") — both are reduced to their host.
    """
    hosts: set[str] = set(_LOOPBACK)
    bind = _host_of(getattr(settings, "host", "") or "")
    # 0.0.0.0 / :: mean "all interfaces", not a browsable origin — skip them.
    if bind and bind not in {"0.0.0.0", "::", "0"}:
        hosts.add(bind)
    for entry in getattr(settings, "allowed_origins", None) or []:
        h = _host_of(str(entry))
        if h and h != "null":
            hosts.add(h)
    return hosts


def origin_is_allowed(origin: str, referer: str, host_header: str,
                      allowed_hosts: set[str]) -> bool:
    """Core, side-effect-free CSRF decision. See module docstring for the model.

    - No Origin and no Referer -> allowed (non-browser client).
    - Origin "null" -> rejected.
    - Source host equals the request's own Host header -> allowed (same-site).
    - Source host in the configured allowlist -> allowed.
    - Otherwise -> rejected.
    """
    source = origin if (origin or "").strip() else referer
    src_host = _host_of(source)
    if not src_host:
        return True                      # no browser-supplied origin context
    if src_host == "null":
        return False                     # opaque origin on a state change
    if src_host == _host_of(host_header):
        return True                      # same-site by construction
    return src_host in allowed_hosts


class CsrfMiddleware(BaseHTTPMiddleware):
    """Reject cross-site state-changing requests (see module docstring).

    `get_settings` is a zero-arg callable returning the live Settings object, so
    the check tracks runtime settings changes (the /api/settings handler rebinds
    the global). When `settings.csrf_protect` is False the check is skipped.
    """

    def __init__(self, app, get_settings):
        super().__init__(app)
        self._get_settings = get_settings

    async def dispatch(self, request, call_next):
        if request.method in UNSAFE_METHODS:
            settings = self._get_settings()
            if getattr(settings, "csrf_protect", True):
                h = request.headers
                if not origin_is_allowed(h.get("origin", ""), h.get("referer", ""),
                                         h.get("host", ""), build_allowed_hosts(settings)):
                    origin = h.get("origin") or h.get("referer") or "(none)"
                    return JSONResponse(
                        status_code=403,
                        content={
                            "ok": False,
                            "error": "Cross-site request blocked (CSRF protection).",
                            "origin": origin,
                            "hint": "If this is a legitimate reverse-proxy or hostname "
                                    "setup, add the origin to settings.allowed_origins "
                                    "or the LLMGYM_ALLOWED_ORIGINS environment variable. "
                                    "To disable this check set csrf_protect=false.",
                        },
                    )
        return await call_next(request)
