"""Optional Vaultwarden / Bitwarden credential source.

By default the gym reads secrets (push tokens, API keys) from the environment. If
you run a Vaultwarden or Bitwarden vault, store your tokens there instead of in
plaintext env files and let the gym fetch them by item name at use time — the
value is never written to disk or logged.

Setup (once, on the machine running the gym):
    bw config server https://your-vault.example.org   # YOUR vault, not a shared one
    bw login
At runtime the gym needs an unlocked session:
    export BW_SESSION=$(bw unlock --raw)

Then map a secret to a vault item via settings/env, e.g. set
LLMGYM_GIT_TOKEN_ITEM="My Gitea Token" and the gym will pull the password field of
that item when it needs the git push token. Nothing about any specific vault is
baked into this code — you point it at your own.
"""
from __future__ import annotations

import os
import shutil
import subprocess


def available() -> bool:
    """True if the `bw` CLI is installed and an unlocked session is present."""
    return bool(shutil.which("bw")) and bool(os.environ.get("BW_SESSION"))


def get_secret(item_name: str, field: str = "password") -> str | None:
    """Fetch a field (password by default, or any field name) of a vault item by
    name. Returns None if the vault isn't available or the item/field is missing.
    The value is never logged or persisted."""
    if not item_name or not available():
        return None
    session = os.environ.get("BW_SESSION", "")
    try:
        r = subprocess.run(["bw", "get", field, item_name, "--session", session],
                           capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 — vault problems must never crash the gym
        return None
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else None


def resolve(env_var: str, item_name: str | None = None,
            field: str = "password") -> str:
    """Resolve a secret: the environment variable first, then the named vault item
    if one is configured. Returns '' when neither yields a value, so callers can
    treat 'no secret' uniformly."""
    val = os.environ.get(env_var, "")
    if val:
        return val
    if item_name:
        return get_secret(item_name, field) or ""
    return ""
