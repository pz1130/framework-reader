"""落库的密文。见 2026-08-23 网页服务化设计 §6⑥

**主密钥不在库里。** 在库里就等于没加密——一次库泄漏连密文带钥匙一起走。
它走 `FR_SECRET_KEY`（生产上应当来自密钥管理服务，注入成环境变量）。

**没配主密钥就拒绝落库。** 悄悄明文存下来是这里唯一不可接受的失败方式：
管理员会以为自己配的 key 是加密的，而它不是。
"""
import os

MASTER_ENV = "FR_SECRET_KEY"


class SecretError(Exception):
    """能直接给用户看的一句话。**永远不要把明文 key 放进这句话里。**"""


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
        # 随手写一句口令当密钥，加密就只是个说法。宁可不收。
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
        # 换过主密钥、或者密文被改过。两种都不能「尽力而为地解出点什么」。
        raise SecretError(
            "Could not decrypt the stored API key - FR_SECRET_KEY has probably changed."
            "Re-enter the key on the Models page.") from exc


def mask(key: str) -> str:
    """回显用。`sk-…cdef`——够认出「是不是我上次填的那把」，不够拿去用。"""
    if not key:
        return ""
    if len(key) < 12:
        # 短 key 按「留头留尾」露出来的比例太高，不如什么都不说。
        return "(set)"
    return f"{key[:3]}...{key[-4:]}"
