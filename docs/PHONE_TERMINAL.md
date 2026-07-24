# Phone terminal — drive your laptop's shell from your phone

Long training runs don't need you at the desk. The phone terminal is a small,
token-protected web terminal that runs on the laptop; open the printed URL on
your phone and you have a full interactive shell in the mobile browser — watch
the training queue, tail logs, kick off `llm-gym`, or anything else.

```
./run.sh terminal          # or:  python -m llm_gym.terminal
                           # or:  llm-gym-terminal  (if pip-installed)
```

It prints something like:

```
LLM Gym phone terminal
  Session -> tmux session 'llmgym-phone' (survives disconnects)
  Open on your phone (same Wi-Fi):
    http://192.168.1.23:7681/?token=kJx1...Hq3w
```

Open that URL on the phone (same Wi-Fi as the laptop). With the optional
`pip install qrcode` it also prints a QR code, so you scan instead of typing.

## What you get on the phone

- A real terminal (xterm.js) sized to the phone screen, with a key bar for the
  keys soft keyboards don't have: **Esc, Tab, Ctrl, Alt, arrows, ^C**.
  Ctrl/Alt are one-shot latches — tap **Ctrl**, then tap `r`, and the shell
  receives Ctrl-R.
- **A−/A+** adjust the font size (remembered per device).
- **Einfügen** reads the clipboard only after you tap the button and sends the
  text through xterm's bracketed-paste handling. Clipboard contents are never
  displayed or logged by the page.
- **Update** checks the installed PWA for a new version, activates it, and
  reloads the terminal once the new service worker is ready.
- **Sessions survive disconnects.** The shell runs inside a tmux session
  (`tmux new-session -A -s llmgym-phone`), so when the phone locks or Wi-Fi
  blips, your processes keep running; reopening the page reattaches to the
  same session — same scrollback, same running command. Opening the page from
  the laptop browser too attaches the *same* session (tmux mirrors it).
  Without tmux installed it falls back to one plain login shell per connection
  (`--no-tmux` forces that mode).

## Options

| Flag / env | Default | Meaning |
|---|---|---|
| `--port` | `7681` | HTTP port |
| `--host` | `0.0.0.0` | bind address (all interfaces, so the phone can reach it) |
| `--shell` | `$SHELL` | shell to run |
| `--session` | `llmgym-phone` | tmux session name to attach/create |
| `--no-tmux` | off | plain shell per connection, nothing persists |
| `--domain` / `LLMGYM_TERM_DOMAIN` | — | public hostname when serving through a TLS tunnel or reverse proxy — prints the `https://` URL + QR and accepts that Origin |
| `--access-team` / `LLMGYM_TERM_ACCESS_TEAM` | — | Cloudflare Access team (`<team>.cloudflareaccess.com`) — turns on Access JWT verification |
| `--access-aud` / `LLMGYM_TERM_ACCESS_AUD` | — | Access application AUD tag; setting it makes a valid Access login mandatory |
| `--access-only` / `LLMGYM_TERM_ACCESS_ONLY` | off | let Cloudflare Access be the sole gate (no token needed on the phone) |
| `LLMGYM_TERM_TOKEN` | random per run | fix the token (e.g. to bookmark the URL) |

## Use it via a domain — from anywhere, with HTTPS

On your own Wi-Fi the printed `http://<lan-ip>` URL is all you need. To use the
terminal away from home under a real domain, put a TLS tunnel in front of it.

### Recommended: Cloudflare Tunnel + Cloudflare Access

If you're on Cloudflare, this is the strongest and best-integrated option: no
open port, and a real login (SSO / email one-time-code) in front — the terminal
even verifies the Access token itself. Full step-by-step, including the SSH path
to your other servers, is in [`FLEET_ACCESS.md`](FLEET_ACCESS.md). In short:

```bash
./run.sh terminal --host 127.0.0.1 --domain term.example.com \
  --access-team YOURTEAM --access-aud <AUD> --access-only
cloudflared tunnel --url http://localhost:7681        # or a named tunnel
```

Add a **Self-hosted Access application** for `term.example.com` with an Allow
policy (your email / domain / IdP + MFA), copy its **AUD tag** into
`--access-aud`, and open `https://term.example.com` on the phone. Cloudflare
challenges you for a login first; only then does the request reach the
terminal, which checks the Access JWT before serving. `--access-only` means no
token to juggle on the phone — Access is the gate. (For a throwaway test,
`cloudflared tunnel --url http://localhost:7681` gives a random
`*.trycloudflare.com` URL you can pass as `--domain`.)

### Alternative: Tailscale (no domain to buy, not public)

Install [Tailscale](https://tailscale.com) on laptop and phone (same account);
your laptop gets a name like `laptop.tail1234.ts.net` (`tailscale status`).

```bash
./run.sh terminal --host 127.0.0.1 --domain laptop.tail1234.ts.net
tailscale serve --bg 7681        # terminates TLS, cert is automatic
```

The domain resolves **only inside your tailnet** — nothing public — and
Tailscale authenticates the devices on top of the token. Good when you don't
want to attach a public hostname.

### Any other TLS reverse proxy

Caddy/nginx work the same: proxy `https://your-domain` → `http://127.0.0.1:7681`
(WebSockets enabled, `Host` or `X-Forwarded-Host` passed through) and start with
`--domain your-domain`. What we advise **against** is the classic router
port-forward of the plain HTTP port — no TLS, and a permanently exposed shell
port. A public hostname without Access in front means the token is the only lock
on a shell, so either use `--access-*` or keep that URL secret and short-lived.

## Security model — read this once

- Every request needs the **token** (192-bit random per run, or your
  `LLMGYM_TERM_TOKEN`). The first request carries it in the URL; the server
  immediately moves it into an HttpOnly cookie and redirects, so it doesn't
  linger in the address bar. Compares are constant-time. No token, no page —
  and no WebSocket.
- **Anyone with the token has a shell as your user.** Treat the URL like a
  password: don't paste it into chats, don't screenshot it.
- Locally the transport is **plain HTTP** — fine on your own Wi-Fi, not on
  networks you don't trust. For access from outside, do **not** port-forward
  this; front it with a TLS tunnel as described in
  [Use it via a domain](#use-it-via-a-domain--from-anywhere-with-https).
  Behind a TLS proxy the token cookie is automatically marked `Secure`.
- **Cloudflare Access (`--access-*`) is the strongest gate.** When configured,
  every request and WebSocket handshake must carry a valid, signature-verified
  Access JWT for your application, so a leaked hostname or token alone can't get
  in — the visitor must also pass your Access login/MFA policy. With
  `--access-only` the Access login fully replaces the token.
- Stopping the process (Ctrl-C in the terminal that started it) kills the
  server; with tmux, the tmux session itself keeps running on the laptop.

## Reaching your other servers from here

The terminal gives you a shell on this one machine. To reach the rest of your
fleet from it — and to let a Claude session help operate them — connect out
over authenticated SSH rather than exposing a web shell per server. See
[`FLEET_ACCESS.md`](FLEET_ACCESS.md) for the safe setup (Tailscale SSH,
inventory, session-based Claude ops, and a guarded design for unattended
automation).

## When you'd rather use SSH

This tool optimizes for "zero setup on the phone" — any mobile browser works.
If you already run an SSH server on the laptop, a dedicated SSH app
(Termius, Blink Shell, JuiceSSH) plus `tmux` gives you the same persistence
with SSH's transport security, and [mosh](https://mosh.org) handles flaky
mobile connections even better. The phone terminal exists for when you don't
want to set any of that up.
