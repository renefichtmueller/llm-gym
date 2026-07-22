# Reaching the terminal and the whole fleet — via Cloudflare Zero Trust

You already use Cloudflare, so this is the recommended path: publish the phone
terminal and reach every server through **Cloudflare Zero Trust**, so nothing
listens on the open internet and a real **Cloudflare Access** login (SSO /
email one-time-code) — not just a token — stands in front of everything.

Two products do the work:

| Need | Cloudflare product |
|---|---|
| Publish the phone terminal under `term.example.com`, no open port | **Cloudflare Tunnel** (`cloudflared`) |
| Real login in front of it (SSO / email OTP), deny-by-default | **Cloudflare Access** (self-hosted app) |
| SSH to `erik` & the fleet, short-lived certs, per-server policy, command logs | **Access for Infrastructure (SSH)** |

The guiding rule from before still holds — you never expose a raw web shell per
server. Cloudflare gives you per-user identity, central policy, and an audit
trail on top.

---

## Part A — Publish the phone terminal with Tunnel + Access

**1. Run the terminal bound to localhost** (only the local tunnel needs it):

```bash
./run.sh terminal --host 127.0.0.1 --domain term.example.com \
  --access-team YOURTEAM --access-aud <AUD> --access-only
```

`--access-team` is your `YOURTEAM.cloudflareaccess.com`; `--access-aud` is the
Access application's **AUD tag** (you get it in step 3). `--access-only` makes a
valid Access login the *sole* gate, so there's no token to fumble on the phone —
drop it if you'd rather keep the token as a second factor. (All three also read
from `LLMGYM_TERM_ACCESS_TEAM` / `_AUD` / `_ONLY`.)

**2. Point a Tunnel at it.** With `cloudflared` installed and logged in:

```bash
cloudflared tunnel create phone-term
cloudflared tunnel route dns phone-term term.example.com
cloudflared tunnel run --url http://localhost:7681 phone-term
```

(Or configure the same as a **remotely-managed tunnel** in the Zero Trust
dashboard under **Networks → Tunnels** — same result, managed in the UI.)

**3. Put Access in front.** In the [Zero Trust dashboard](https://one.dash.cloudflare.com)
→ **Access controls → Applications → Add an application → Self-hosted**:

- Public hostname: `term.example.com`.
- Add a **policy**: e.g. *Allow* where **Emails** = your address(es), or your
  whole `@flexoptix.net` domain, or require your identity provider + MFA. Access
  is deny-by-default — only a matching Allow policy gets in.
- After you save, copy the application's **Application Audience (AUD) tag** and
  pass it as `--access-aud` above.

Now open `https://term.example.com` on the phone: Cloudflare shows *your* login
first, and only after you pass it does the request reach the terminal — which
**independently verifies the Access JWT** (`Cf-Access-Jwt-Assertion`) before
serving anything. Two locks, both Cloudflare-issued.

> **How the terminal enforces Access:** on every request and every WebSocket
> handshake it verifies the RS256 signature of the Access JWT against your
> team's JWKS, checks the `aud`, issuer and expiry, and denies on any mismatch.
> So even if the tunnel hostname leaked, no Cloudflare login = no shell.

## Part B — Reach `erik` & the fleet with Access for Infrastructure (SSH)

This replaces long-lived SSH keys with **short-lived certificates** minted per
Access login, adds per-server / per-username policy, and logs the SSH commands —
the audit-log guardrail, delivered by Cloudflare.

Per server (`erik`, then the rest):

1. **Connect the server to Cloudflare** with a Tunnel (Zero Trust → **Networks
   → Tunnels**), or the WARP-to-Tunnel model. Install the **Cloudflare One
   client (WARP)** on the laptop/phone you connect *from*.
2. **Generate the Cloudflare SSH CA** once, in **Access controls → Service
   credentials → SSH → Generate SSH CA**, and copy its public key.
3. **Trust the CA on each server**: add the CA public key to
   `/etc/ssh/ca.pub` and set `TrustedUserCAKeys /etc/ssh/ca.pub` in
   `sshd_config`, then reload `sshd`.
4. **Add an Infrastructure application** (**Access controls → Applications →
   Add → Infrastructure**) targeting the server, and a **policy**: who may
   connect, as which SSH usernames (use a non-root `ops` user — see Part D).
5. **Add a Gateway network policy** allowing *Access Infrastructure Target is
   Present*, so the traffic routes.

Then, from the phone terminal (or any native SSH client on a WARP-connected
device), `ssh ops@erik` just works — no key to manage, login gated by your
Access policy + MFA, every command logged. Revoke by changing the Access policy;
certificates are short-lived so access expires on its own.

## Part C — Claude helping operate the fleet (session-based)

The honest shape of "reachable for Claude" is **a box, on WARP, ready for you to
start a Claude session on** — not a standing agent with a live shell into prod.

- Run Claude Code (or the phone terminal, then Claude in it) on that box.
- Within a session *you* start, Claude reaches `erik` & co. through the same
  `ssh ops@erik` hops — every hop still passing your Access policy and landing
  in the command log.
- Session ends → access ends. There is no always-on agent; that's what keeps a
  bug or a prompt-injection from quietly touching the fleet.

## Part D — Unattended automation, guarded (also on Cloudflare)

If tasks must run without you watching, keep the guardrails — Cloudflare
supplies several of them:

- **Non-root ops user + `sudo` allowlist**, per server. Never blanket sudo.
- **Non-interactive auth = Cloudflare Access service token**, not a shared
  password. Scope it to only the infrastructure/apps that task needs, and rotate
  or revoke it centrally.
- **Task allowlist, not raw shell.** Automation invokes named tasks; anything
  else is refused.
- **Audit log** comes largely for free: Access logs + SSH command logs (export
  via **Logpush** on Enterprise). If you want a task-level ledger, a small
  **Cloudflare Worker + D1** makes a tidy append-only store and approval webhook.
- **Approval gate** for irreversible actions — a Worker that pings you and waits
  for an explicit OK before the task proceeds.
- **Kill switch:** disable the Access service token (or the Infrastructure
  policy) and every automated path dies at once.

Tell me the first concrete task you want automated and I'll build it against
this — likely a small Worker + service-token runner in its own repo, not the
training tool.

---

### Security recap

- **Nothing is exposed** — Tunnel means no open ports; servers are reachable only
  through Cloudflare.
- **Access is the real gate** — SSO/MFA, deny-by-default, and the terminal
  *verifies the Access JWT itself* so a leaked hostname is still useless.
- **SSH uses short-lived certs**, per-server/username policy, and command logs —
  no long-lived keys to steal.
- **Automation** gets a non-root user, a scoped/revocable Access service token, a
  task allowlist, Access + command-log audit trails, an approval gate, and a
  one-switch kill — never a shared token wired to root.
