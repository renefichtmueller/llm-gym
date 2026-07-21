"""Encrypt secrets at rest (e.g. a Confluence API token stored on an adapter).

AES-256-GCM (authenticated). The key comes from the LLMGYM_SECRET_KEY environment
variable (urlsafe-base64 of 32 bytes) if set, otherwise from a local key file
(.secret.key, auto-generated, chmod 600, gitignored). Encrypted blobs are useless
without that key, and the key never lives in the repo.

On macOS you can instead point LLMGYM_SECRET_KEY at a key pulled from the Keychain
in your shell profile — the gym does not care where the env value comes from.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import ROOT

_KEYFILE = ROOT / ".secret.key"


def _key() -> bytes:
    env = os.environ.get("LLMGYM_SECRET_KEY")
    if env:
        return base64.urlsafe_b64decode(env)
    if _KEYFILE.exists():
        return base64.urlsafe_b64decode(_KEYFILE.read_text().strip())
    key = AESGCM.generate_key(bit_length=256)
    # Create the key file 0o600 from the start (O_EXCL, mode on open) so there is
    # never a window where another local user can read the AES key — a plain
    # write_text()+chmod() leaves it world-readable under the default umask until
    # the chmod lands, and swallows a chmod failure entirely.
    blob = base64.urlsafe_b64encode(key).decode()
    fd = os.open(_KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(blob)
    return key


def encrypt(plaintext: str) -> str:
    """Return a urlsafe-base64 blob (nonce + ciphertext), or "" for empty input."""
    if not plaintext:
        return ""
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(blob: str) -> str:
    """Inverse of encrypt(). Returns "" on empty/invalid input."""
    if not blob:
        return ""
    try:
        raw = base64.urlsafe_b64decode(blob)
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        # A non-empty blob that won't decrypt means a wrong/rotated key or a
        # tampered/corrupt value — surface it (without leaking the blob) instead
        # of silently masquerading as "no secret set".
        print(f"[llm_gym] could not decrypt a stored secret ({type(exc).__name__}) "
              "— wrong LLMGYM_SECRET_KEY / .secret.key, or a corrupted value.")
        return ""
