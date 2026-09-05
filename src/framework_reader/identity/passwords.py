"""Password hashing. scrypt is in the standard library - no new dependency for this."""
import hashlib
import hmac
import os

# Lower bound of the scrypt parameters recommended by OWASP 2023. Changing these
# numbers would make old hashes fail verification, so they are stored alongside
# each hash and read back from that string at verify time - not from here.
_N, _R, _P = 2 ** 14, 8, 1
_SALT_BYTES = 16
_KEY_LEN = 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt = os.urandom(_SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_LEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verification failure always returns False, never raises - raising would
    distinguish "malformed hash" from "wrong password", and that is an
    observable difference."""
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(key_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key, bytes.fromhex(key_hex))
