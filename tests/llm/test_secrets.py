"""落库的 key 要加密。见 2026-08-23 网页服务化设计 §6⑥

本地部署时 key 在用户自己机器上的环境变量里，泄漏是他自己的事。
联网之后 key 存在**我们的**服务器上，泄漏由我们担责——所以静态加密不是
锦上添花，是这一步能不能做的前提。

主密钥不在库里（在库里就等于没加密），走 `FR_SECRET_KEY`。
没配就**拒绝落库**——悄悄明文存下来是这里唯一不可接受的失败方式。
"""
import pytest

from framework_reader import crypto


@pytest.fixture
def key(monkeypatch):
    generated = crypto.new_master_key()
    monkeypatch.setenv(crypto.MASTER_ENV, generated)
    return generated


def test_a_sealed_key_comes_back_out(key):
    assert crypto.open_secret(crypto.seal("sk-live-abcd1234")) == "sk-live-abcd1234"


def test_the_ciphertext_does_not_contain_the_key(key):
    assert "abcd1234" not in crypto.seal("sk-live-abcd1234")


def test_the_same_key_seals_differently_every_time(key):
    """密文相同就等于泄漏了「这两个 provider 用的是同一把 key」。"""
    assert crypto.seal("sk-same") != crypto.seal("sk-same")


def test_another_master_key_cannot_open_it(key, monkeypatch):
    sealed = crypto.seal("sk-live-abcd1234")
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    with pytest.raises(crypto.SecretError):
        crypto.open_secret(sealed)


def test_a_tampered_ciphertext_is_refused_not_silently_wrong(key):
    sealed = crypto.seal("sk-live-abcd1234")
    broken = sealed[:-4] + ("aaaa" if not sealed.endswith("aaaa") else "bbbb")
    with pytest.raises(crypto.SecretError):
        crypto.open_secret(broken)


def test_without_a_master_key_sealing_refuses(monkeypatch):
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    with pytest.raises(crypto.SecretError):
        crypto.seal("sk-live-abcd1234")


def test_a_made_up_master_key_is_refused_not_used(monkeypatch):
    """「password123」也当密钥用的话，加密就只是个说法。"""
    monkeypatch.setenv(crypto.MASTER_ENV, "password123")
    with pytest.raises(crypto.SecretError):
        crypto.seal("sk-live-abcd1234")


def test_the_error_says_what_to_run(monkeypatch):
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    with pytest.raises(crypto.SecretError) as caught:
        crypto.seal("x")
    assert "fr secret new" in str(caught.value)


# ---------- 脱敏 ----------

def test_a_masked_key_shows_only_the_tail():
    masked = crypto.mask("sk-live-0123456789abcdef")
    assert masked.endswith("cdef")
    assert "0123456789" not in masked


def test_a_short_key_is_not_half_revealed():
    """短 key 按「留头留尾」露出来的比例太高，不如什么都不说。"""
    assert crypto.mask("abcd") == "(set)"


def test_an_empty_key_masks_to_nothing():
    assert crypto.mask("") == ""
