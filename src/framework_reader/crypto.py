"""Ciphertext at rest. See the 2026-08-23 web service design §6⑥

**The master key is never in the database.** In the database equals not encrypted - one leak walks off with both.
It comes from `FR_SECRET_KEY` (in production, injected from a secrets manager as an environment variable).

**Refuse to persist without a master key.** Silently storing plaintext is the one unacceptable
failure here: the admin would believe their configured key is encrypted when it is not.
"""
import os

MASTER_ENV = "FR_SECRET_KEY"


class SecretError(Exception):
    """One sentence safe to show the user. **Never put the plaintext key in it.**"""


def new_master_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.getenv(MASTER_ENV, "").strip()
    if not raw:
        raise SecretError(
            f"No {MASTER_ENV} configured, so I will not store API keys."
            "Generate one with `fr secret new`, inject it as an environment variable, then come back to configure models.")
    try:
        return Fernet(raw.encode("ascii"))
    except Exception as exc:
        # A casual passphrase as the key makes "encrypted" a figure of speech. Rather refuse.
        raise SecretError(
            f"{MASTER_ENV} is not a valid key (want 32 bytes base64)."
            "Generate one with `fr secret new`; do not invent one.") from exc


def configured() -> bool:
    try:
        _fernet()
    except SecretError:
        return False
    return True


def seal(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def open_secret(sealed: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(sealed.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        # The master key was rotated, or the ciphertext was altered. Neither may "best-effort decrypt something".
        raise SecretError(
            "Could not decrypt the stored API key - FR_SECRET_KEY has probably changed."
            "Re-enter the key on the Models page.") from exc


def mask(key: str) -> str:
    """For display. `sk-…cdef` - enough to recognise "was it the one I entered", not enough to use."""
    if not key:
        return ""
    if len(key) < 12:
        # A short key revealed head-and-tail is too high a fraction; saying nothing is better.
        return "(set)"
    return f"{key[:3]}...{key[-4:]}"
