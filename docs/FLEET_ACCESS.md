# Reaching the whole fleet from the phone terminal — safely

The phone terminal ([`PHONE_TERMINAL.md`](PHONE_TERMINAL.md)) gives you a shell
on **one** machine. This doc is about the next step you asked for: from that
machine, reach the rest of the fleet (the box we'll call `erik` here, plus the
others), and let a Claude session help operate them — **without** turning the
terminal into a standing, fleet-wide web shell.

The guiding rule: **you connect out to each server over authenticated SSH.**
You do not expose a web shell per server, and you do not wire a single token to
root on everything. One leaked token on a fleet-wide web shell = the whole
company compromised, with no per-device revoke and no audit trail. The setup
below gives the same reach with per-device identity, central revoke, and a log.

---

## 1. One authenticated overlay across the fleet — Tailscale SSH

Install [Tailscale](https://tailscale.com) on the **terminal host** (your
laptop or a small jump box) **and on every server** (`erik` & co.), all under
the same tailnet. On each server:

```bash
tailscale up --ssh
```

That makes the server's SSH reachable **inside the tailnet only** — nothing is
exposed to the public internet, and Tailscale (not a shared password)
authenticates every connection. From the phone terminal you then simply:

```bash
ssh erik            # or: tailscale ssh erik
```

Who may reach which server — and as which user — is decided centrally by the
tailnet **ACL policy**, editable in the Tailscale admin console and revocable
in seconds. A minimal, least-privilege example:

```jsonc
{
  // Tag the fleet so rules target the group, not individual machines.
  "tagOwners": { "tag:fleet": ["autogroup:admin"] },

  // Network reachability: members can open SSH to tagged servers.
  "acls": [
    { "action": "accept", "src": ["autogroup:member"], "dst": ["tag:fleet:22"] }
  ],

  // Tailscale SSH: members may log in as the unprivileged `ops` user.
  // Prefer this over "root" — see section 4. "checkPeriod" forces periodic
  // re-auth so a lost laptop can't stay logged in forever.
  "ssh": [
    {
      "action": "accept",
      "src": ["autogroup:member"],
      "dst": ["tag:fleet"],
      "users": ["ops"],
      "checkPeriod": "12h"
    }
  ]
}
```

Tag each server when you bring it up (`tailscale up --ssh
--advertise-tags=tag:fleet`). To cut a device off entirely, disable it in the
admin console — every hop through it dies at once.

> If you already run plain SSH with keys and prefer to keep it, that's fine
> too — the rest of this doc doesn't require Tailscale. Tailscale is the
> recommendation because it gives you the central identity + revoke + "no
> public port" properties for free. For flaky mobile links, add
> [mosh](https://mosh.org) on top.

## 2. Make "all servers" one list — an inventory

So `erik` and the rest are one command away, drop an SSH config on the terminal
host (`~/.ssh/config`):

```sshconfig
Host erik
    HostName erik                 # tailnet name resolves inside the tailnet
    User ops

Host db-*
    User ops

# ... one stanza (or wildcard) per server
```

Now `ssh erik`, `ssh db-1`, etc. just work — for you at the keyboard and for a
Claude session on the same host.

## 3. Claude helping operate the fleet — session-based

This is the honest shape of "always reachable for Claude": **not** a daemon
sitting on an open shell, but **a box that's ready for you to start a Claude
session on**, which already has the SSH reach from sections 1–2.

- Run Claude Code (or the phone terminal, then Claude inside it) on the
  terminal host / a dedicated ops box.
- Within a session *you* start, Claude reaches `erik` & co. through the same
  `ssh erik` hops — under your supervision, with your approval on anything that
  matters.
- When the session ends, so does the access. There is no standing agent with a
  live shell into production. That property is a feature, not a limitation:
  it's what keeps a prompt-injection or a bug from quietly touching every
  server at 3am.

## 4. Unattended automation — the guarded design

If you want tasks to run **without** someone watching, the answer is still not
"point an autonomous agent at root on the fleet." Build it in layers, each of
which limits the blast radius of the one below:

- **Least-privilege ops user, not root.** A dedicated `ops` account per server.
  It gets `sudo` only for *specific, named* commands via a `sudoers` allowlist —
  never blanket `sudo`.
- **A task runner, not raw shell.** Automation invokes *named tasks*
  ("restart-service X", "rotate-logs") from an allowlist, not arbitrary command
  strings. Anything not on the list is refused, not run.
- **Audit log.** Every action — who/what/when/target/exit code — appended to a
  log the automation account cannot rewrite (append-only / shipped off-box). If
  you can't answer "what did it do last night?", the design is wrong.
- **Approval gate for anything irreversible.** Destructive tasks (deletes,
  restarts, deploys, `db` writes) pause and require an explicit human OK — a
  push notification or chat approval — before they proceed.
- **Scoped, rotatable credentials.** Per-task, time-limited, revocable creds.
  No single god-token that unlocks the whole fleet.
- **A kill switch.** One command that disables all automation at once, and a
  documented "how to revoke everything" (here: disable the tailnet key).

None of this is bureaucracy for its own sake — it's the difference between
"handy ops automation" and "one bad input owns the company." Tell me which of
these layers you want first and I'll build it (it likely belongs in its own
small ops repo, not the training tool), scoped to a concrete task you actually
want automated.

---

### Security recap

- Reach servers by **connecting out over authenticated SSH**, not by exposing a
  web shell on each.
- **Tailscale SSH** gives per-device identity, central ACLs, instant revoke, and
  no public SSH port. Prefer a non-root `ops` user.
- **Claude ops help is session-based** — access exists only while you're in a
  session, and dies when it ends. There is deliberately no always-on agent with
  a standing shell into production.
- **Unattended automation** gets least-privilege users, a task allowlist, an
  append-only audit log, an approval gate for irreversible actions, scoped
  rotatable creds, and a kill switch — never a shared token wired to root.
