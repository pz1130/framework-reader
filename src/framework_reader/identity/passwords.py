"""口令哈希。scrypt 在标准库里——不为这件事引入新依赖。"""
import hashlib
import hmac
import os

# OWASP 2023 建议的 scrypt 参数下限。改这几个数会让旧哈希验不过，
# 所以它们跟着哈希一起存，验证时从字符串里读，不从这里读。
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
    """验不过一律返回 False，不抛异常——异常会把「格式坏了」和「口令错了」
    区分开，而那是一个可观测的差别。"""
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
