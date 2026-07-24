"""Phone terminal: drive a terminal session on this machine from your phone.

`python -m llm_gym.terminal` starts a small token-protected web terminal on the
laptop. It prints a URL (and a QR code, if the optional `qrcode` package is
installed) — open it on a phone on the same network and you get a full
interactive shell rendered with xterm.js, with a mobile key bar for Esc/Tab/
Ctrl/arrows.

By default the shell runs inside a tmux session (`tmux new-session -A`), so a
dropped connection — phone locks, Wi-Fi blips, browser tab dies — detaches
instead of killing your work; reconnecting reattaches to the same session.
Without tmux installed it falls back to a plain login shell per connection.

Security model (deliberately simple, documented in docs/PHONE_TERMINAL.md):
- A random 192-bit token is generated per run (or taken from
  LLMGYM_TERM_TOKEN). Every request must carry it — first via the URL, after
  that via an HttpOnly cookie. Compares are constant-time.
- Anyone with the token has a shell as your user. The transport is plain HTTP,
  so use it on networks you trust (home Wi-Fi) or through a VPN like
  Tailscale/WireGuard for access from outside — never port-forward it raw to
  the internet.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hmac
import json
import os
import pty
import shutil
import signal
import socket
import struct
import termios
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"

COOKIE = "llmgym_term"
DEFAULT_PORT = 7681  # same convention as ttyd, easy to remember
DEFAULT_SESSION = "llmgym-phone"

# Set once in main() before uvicorn starts; module-level because uvicorn.run()
# is handed the app object, not an import string.
config: dict = {
    "token": "",
    "shell": os.environ.get("SHELL") or "/bin/bash",
    "tmux": True,
    "session": DEFAULT_SESSION,
    # Public hostname when served through a tunnel/reverse proxy (Cloudflare
    # Tunnel, Tailscale serve, Caddy, ...). Whitelists that Origin and puts the
    # https:// URL + QR in the banner. Bare hostname, no scheme.
    "domain": "",
    # Cloudflare Access (Zero Trust). When access_aud is set, every request must
    # carry a valid Access JWT (Cf-Access-Jwt-Assertion header or CF_Authorization
    # cookie) signed by the team, so Cloudflare's SSO — not just the token — gates
    # the terminal. access_team is your <team>.cloudflareaccess.com; access_aud is
    # the Access application's AUD tag. access_only drops the token requirement and
    # lets Access be the sole gate. access_certs_url overrides the JWKS URL (tests).
    "access_team": "",
    "access_aud": "",
    "access_only": False,
    "access_certs_url": "",
}

app = FastAPI(title="LLM Gym phone terminal", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify_access_jwt(token: str, jwks: dict, aud: str, iss: str, now: float) -> str:
    """Verify a Cloudflare Access JWT (RS256) against the team's JWKS.

    Raises ValueError on any failure; on success returns the authenticated email.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    header = json.loads(_b64url(parts[0]))
    if header.get("alg") != "RS256":
        raise ValueError("unexpected alg")
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if not key:
        raise ValueError("unknown signing key")
    pub = rsa.RSAPublicNumbers(
        int.from_bytes(_b64url(key["e"]), "big"),
        int.from_bytes(_b64url(key["n"]), "big"),
    ).public_key()
    pub.verify(_b64url(parts[2]), f"{parts[0]}.{parts[1]}".encode(),
               padding.PKCS1v15(), hashes.SHA256())  # raises InvalidSignature
    payload = json.loads(_b64url(parts[1]))
    if float(payload.get("exp", 0)) <= now:
        raise ValueError("expired")
    if float(payload.get("nbf", 0)) > now + 60:
        raise ValueError("not yet valid")
    auds = payload.get("aud", [])
    if aud not in ([auds] if isinstance(auds, str) else auds):
        raise ValueError("aud mismatch")
    if iss and payload.get("iss") != iss:
        raise ValueError("iss mismatch")
    return str(payload.get("email", ""))


_jwks_cache: dict = {"at": 0.0, "jwks": None}


def _fetch_jwks(url: str) -> dict:
    """Cached fetch of the Access JWKS (Cloudflare rotates keys rarely)."""
    now = time.time()
    if _jwks_cache["jwks"] is not None and now - _jwks_cache["at"] < 600:
        return _jwks_cache["jwks"]
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    _jwks_cache.update(at=now, jwks=resp.json())
    return _jwks_cache["jwks"]


def _access_check(headers, cookies) -> tuple[bool, str]:
    """(ok, email). ok is True (email '') when Access isn't configured; when it
    is, verifies the forwarded Access JWT and returns the identity or (False,'')."""
    if not config["access_aud"]:
        return True, ""
    tok = headers.get("cf-access-jwt-assertion", "") or cookies.get("CF_Authorization", "")
    if not tok:
        return False, ""
    team = config["access_team"]
    url = config["access_certs_url"] or \
        f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"
    iss = f"https://{team}.cloudflareaccess.com" if team else ""
    try:
        email = _verify_access_jwt(tok, _fetch_jwks(url), config["access_aud"],
                                   iss, time.time())
        return True, email
    except Exception:  # noqa: BLE001 — any verification failure is a hard deny
        return False, ""


def _authed(request: Request) -> bool:
    got = request.cookies.get(COOKIE, "")
    return bool(got) and hmac.compare_digest(got, config["token"])


@app.get("/")
def index(request: Request) -> Response:
    # Cloudflare Access, when configured, is the outer gate: no valid Access
    # JWT, no page — regardless of the token.
    access_ok, _ = _access_check(request.headers, request.cookies)
    if config["access_aud"] and not access_ok:
        return Response("Cloudflare Access authentication required.",
                        status_code=403)
    token = request.query_params.get("token", "")
    if token:
        if not hmac.compare_digest(token, config["token"]):
            return Response("Bad token.", status_code=403)
        # Move the token out of the URL (history, screenshots, shoulder surfing)
        # and into an HttpOnly cookie before serving anything. Behind a TLS
        # tunnel/proxy mark it Secure, so it never travels over a later
        # accidental http:// visit to the same domain.
        https = (request.url.scheme == "https"
                 or request.headers.get("x-forwarded-proto", "") == "https")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=https, max_age=12 * 3600)
        return resp
    # access_only: Access alone gates the terminal, no token needed.
    if config["access_only"] and access_ok:
        return FileResponse(STATIC / "terminal.html")
    if not _authed(request):
        return Response("Missing token. Open the exact URL printed by "
                        "`python -m llm_gym.terminal` on the laptop.",
                        status_code=403)
    return FileResponse(STATIC / "terminal.html")


@app.get("/service-worker.js")
def service_worker(request: Request) -> Response:
    """Serve the root-scoped worker only to an authenticated terminal client."""
    access_ok, _ = _access_check(request.headers, request.cookies)
    if config["access_aud"] and not access_ok:
        return Response("Cloudflare Access authentication required.",
                        status_code=403)
    access_only_ok = config["access_only"] and access_ok
    if not access_only_ok and not _authed(request):
        return Response("Terminal authentication required.", status_code=403)
    return FileResponse(
        STATIC / "terminal-sw.js",
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )


def _spawn() -> tuple[int, int]:
    """Fork a shell on a fresh PTY; returns (pid, master_fd)."""
    if config["tmux"] and shutil.which("tmux"):
        cmd = ["tmux", "new-session", "-A", "-s", config["session"]]
    else:
        cmd = [config["shell"], "-l"]
    pid, fd = pty.fork()
    if pid == 0:  # child: become the shell, or die — never return into uvicorn
        try:
            os.environ["TERM"] = "xterm-256color"
            os.chdir(os.path.expanduser("~"))
            os.execvp(cmd[0], cmd)
        finally:
            os._exit(1)
    return pid, fd


def _resize(fd: int, cols: int, rows: int) -> None:
    if 0 < cols < 1000 and 0 < rows < 1000:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _reap(pid: int) -> None:
    """SIGHUP the PTY child and collect it without blocking the event loop.

    With tmux this only hangs up the attach client — the tmux session (and
    whatever is running in it) stays alive for the next connection.
    """
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        for _ in range(20):
            try:
                done, _st = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if done:
                return
            await asyncio.sleep(0.05)


@app.websocket("/ws")
async def ws_terminal(ws: WebSocket) -> None:
    # Cloudflare Access gate on the handshake too (the JWT rides along on the
    # upgrade request). Verification does a possibly-blocking JWKS fetch, so run
    # it off the event loop.
    if config["access_aud"]:
        access_ok, _ = await asyncio.get_running_loop().run_in_executor(
            None, _access_check, ws.headers, ws.cookies)
        if not access_ok:
            await ws.close(code=4403)
            return
    got = ws.cookies.get(COOKIE, "")
    token_ok = bool(got) and hmac.compare_digest(got, config["token"])
    # access_only: a valid Access JWT (checked above) stands in for the token.
    if not token_ok and not config["access_only"]:
        await ws.close(code=4403)
        return
    # Cross-site WebSocket hijacking guard: a browser page from another origin
    # can open a ws:// connection, and old/lenient browsers may attach the
    # cookie. Same-origin pages send a matching Origin; non-browser clients
    # send none — both pass. Behind a tunnel/proxy the Host header may be
    # rewritten to the local backend, so the forwarded host and the configured
    # --domain are also legitimate origins. Anything else is another site
    # riding the cookie.
    origin = ws.headers.get("origin", "")
    allowed = {ws.headers.get("host", ""),
               ws.headers.get("x-forwarded-host", ""),
               config["domain"]} - {""}
    if origin and origin.split("://", 1)[-1] not in allowed:
        await ws.close(code=4403)
        return
    await ws.accept()

    pid, fd = _spawn()
    loop = asyncio.get_running_loop()
    out: asyncio.Queue[bytes | None] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        out.put_nowait(data or None)  # None = shell exited / PTY closed

    loop.add_reader(fd, on_readable)

    async def pump_output() -> None:
        while (chunk := await out.get()) is not None:
            await ws.send_bytes(chunk)
        await ws.close()

    pump = asyncio.create_task(pump_output())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if data := msg.get("bytes"):  # keystrokes travel as binary frames
                os.write(fd, data)
            elif text := msg.get("text"):  # control frames travel as JSON text
                try:
                    ctl = json.loads(text)
                except ValueError:
                    continue
                if isinstance(r := ctl.get("r"), list) and len(r) == 2:
                    _resize(fd, int(r[0]), int(r[1]))
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        pump.cancel()
        loop.remove_reader(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        await _reap(pid)


def _lan_ips() -> list[str]:
    """Best-effort list of this machine's LAN IPv4 addresses."""
    ips: list[str] = []
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    ips.append(a.address)
    except Exception:  # noqa: BLE001 — fall back to the UDP-connect trick
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                ips.append(s.getsockname()[0])
        except OSError:
            pass
    return ips or ["<laptop-ip>"]


def _print_banner(host: str, port: int) -> None:
    if config["domain"]:
        # Served through a TLS tunnel/proxy — the domain URL is the one to use.
        urls = [f"https://{config['domain']}/?token={config['token']}"]
        where = "Open on your phone:"
    else:
        urls = [f"http://{ip}:{port}/?token={config['token']}"
                for ip in (_lan_ips() if host in ("0.0.0.0", "::") else [host])]
        where = "Open on your phone (same Wi-Fi):"
    mode = (f"tmux session '{config['session']}' (survives disconnects)"
            if config["tmux"] and shutil.which("tmux")
            else f"plain shell {config['shell']} (one per connection)")
    print("LLM Gym phone terminal")
    print(f"  Session -> {mode}")
    print(f"  {where}")
    for u in urls:
        print(f"    {u}")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(urls[0])
        qr.print_ascii(invert=True)
    except ImportError:
        print("  (pip install qrcode  ->  get a scannable QR code here)")
    if config["access_aud"]:
        gate = ("Cloudflare Access (SSO) alone"
                if config["access_only"] else "Cloudflare Access (SSO) + token")
        print(f"  Gated by -> {gate}. Only your Access policy can reach it.")
    else:
        print("  Anyone with this URL has a shell as your user.")
        if not config["domain"]:
            print("  Plain HTTP: use it on trusted Wi-Fi or through a tunnel/VPN "
                  "(docs/PHONE_TERMINAL.md); never port-forward it.")


def main() -> None:
    import secrets

    import uvicorn

    ap = argparse.ArgumentParser(
        description="Token-protected web terminal for controlling this "
                    "machine from a phone browser.")
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default: all interfaces, so the phone "
                         "can reach it)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--shell", default=config["shell"],
                    help="shell to run (default: $SHELL)")
    ap.add_argument("--session", default=DEFAULT_SESSION,
                    help="tmux session name to attach/create")
    ap.add_argument("--no-tmux", action="store_true",
                    help="plain shell per connection instead of a persistent "
                         "tmux session")
    ap.add_argument("--domain", default=os.environ.get("LLMGYM_TERM_DOMAIN", ""),
                    help="public hostname when serving through a TLS tunnel or "
                         "reverse proxy (e.g. term.example.com via Cloudflare "
                         "Tunnel) — prints the https URL and accepts that Origin")
    ap.add_argument("--access-team",
                    default=os.environ.get("LLMGYM_TERM_ACCESS_TEAM", ""),
                    help="Cloudflare Access team name (<team>.cloudflareaccess.com) "
                         "— enables verifying the Access SSO JWT on every request")
    ap.add_argument("--access-aud",
                    default=os.environ.get("LLMGYM_TERM_ACCESS_AUD", ""),
                    help="Cloudflare Access application AUD tag; setting it makes a "
                         "valid Access login mandatory")
    ap.add_argument("--access-only", action="store_true",
                    default=os.environ.get("LLMGYM_TERM_ACCESS_ONLY", "") not in ("", "0"),
                    help="let Cloudflare Access be the sole gate (no token needed "
                         "on the phone)")
    args = ap.parse_args()

    # Accept sloppy --domain values ("https://x.ts.net/") — keep the bare host.
    domain = args.domain.strip().split("://", 1)[-1].strip("/")
    config.update(
        token=os.environ.get("LLMGYM_TERM_TOKEN") or secrets.token_urlsafe(24),
        shell=args.shell, session=args.session, tmux=not args.no_tmux,
        domain=domain,
        access_team=args.access_team.strip().split("://", 1)[-1].strip("/")
                    .replace(".cloudflareaccess.com", ""),
        access_aud=args.access_aud.strip(),
        access_only=bool(args.access_only) and bool(args.access_aud.strip()))

    _print_banner(args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
