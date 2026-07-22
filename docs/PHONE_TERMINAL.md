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
| `LLMGYM_TERM_TOKEN` | random per run | fix the token (e.g. to bookmark the URL) |

## Security model — read this once

- Every request needs the **token** (192-bit random per run, or your
  `LLMGYM_TERM_TOKEN`). The first request carries it in the URL; the server
  immediately moves it into an HttpOnly cookie and redirects, so it doesn't
  linger in the address bar. Compares are constant-time. No token, no page —
  and no WebSocket.
- **Anyone with the token has a shell as your user.** Treat the URL like a
  password: don't paste it into chats, don't screenshot it.
- The transport is **plain HTTP** — fine on your own Wi-Fi, not on networks
  you don't trust. For access from outside your network, do **not**
  port-forward this. Put laptop and phone on a VPN such as
  [Tailscale](https://tailscale.com) or WireGuard and use the laptop's VPN IP
  — you get encryption and authentication at the network layer, and the
  terminal stays unreachable from the open internet.
- Stopping the process (Ctrl-C in the terminal that started it) kills the
  server; with tmux, the tmux session itself keeps running on the laptop.

## When you'd rather use SSH

This tool optimizes for "zero setup on the phone" — any mobile browser works.
If you already run an SSH server on the laptop, a dedicated SSH app
(Termius, Blink Shell, JuiceSSH) plus `tmux` gives you the same persistence
with SSH's transport security, and [mosh](https://mosh.org) handles flaky
mobile connections even better. The phone terminal exists for when you don't
want to set any of that up.
