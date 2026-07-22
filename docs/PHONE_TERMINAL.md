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
| `LLMGYM_TERM_TOKEN` | random per run | fix the token (e.g. to bookmark the URL) |

## Use it via a domain — from anywhere, with HTTPS

On your own Wi-Fi the printed `http://<lan-ip>` URL is all you need. To use
the terminal away from home under a real domain, put a TLS tunnel in front of
it. Two good ways:

### Tailscale (recommended — free, no domain to buy, not public)

Install [Tailscale](https://tailscale.com) on the laptop and the phone (same
account). Your laptop then has a real DNS name like
`laptop.tail1234.ts.net` — check it with `tailscale status`. Then:

```bash
./run.sh terminal --host 127.0.0.1 --domain laptop.tail1234.ts.net
tailscale serve --bg 7681        # terminates TLS, cert is automatic
```

Open `https://laptop.tail1234.ts.net/?token=...` (printed, with QR) on the
phone — works from anywhere the phone has internet, as long as the Tailscale
app is connected. The big win: the domain resolves **only inside your
tailnet**; nothing is exposed to the public internet, and Tailscale
authenticates the devices on top of the token. `--host 127.0.0.1` keeps the
plain-HTTP port off the LAN since only the local tunnel needs it.
(`tailscale serve reset` turns it off.)

### Your own domain via Cloudflare Tunnel (public URL)

If you own a domain and want e.g. `term.example.com` without opening any
router port, use [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):

```bash
./run.sh terminal --host 127.0.0.1 --domain term.example.com
cloudflared tunnel create phone-term
cloudflared tunnel route dns phone-term term.example.com
cloudflared tunnel run --url http://localhost:7681 phone-term
```

(For a quick throwaway test, `cloudflared tunnel --url http://localhost:7681`
gives you a random `*.trycloudflare.com` URL — pass that as `--domain`.)

**A public URL means the whole internet can knock.** The token is then the
only lock on a shell — so put
[Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
in front of the hostname (a free e-mail one-time-code policy takes minutes to
set up), keep the token secret, and stop the tunnel when you don't need it.
Prefer the Tailscale route whenever you don't specifically need a public URL.

Any other TLS reverse proxy (Caddy, nginx) works the same way: proxy
`https://your-domain` → `http://127.0.0.1:7681` (WebSockets enabled, `Host`
or `X-Forwarded-Host` passed through) and start with `--domain your-domain`.
What we advise **against** is the classic router port-forward of the plain
HTTP port — no TLS, and a permanently exposed shell port.

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
