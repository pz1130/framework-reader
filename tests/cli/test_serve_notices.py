"""`fr serve` 起来那几行字。

起服务时最该一眼看见的是**门锁没锁**。默认绑 127.0.0.1 时门开着无所谓——
能上这台机器的人本来就能读这些文件；绑到别的地址上就完全是另一回事：
在第一个管理员建出来之前，同网段的任何人都能抢先建，而他建完门就锁上了，
锁在外面的是你。
"""
import pytest
from typer.testing import CliRunner

from framework_reader import crypto
from framework_reader.cli.main import app


@pytest.fixture
def serve(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    # 主密钥要由每条测试自己决定有没有，不能捡开发机上的。
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)

    def run(*args: str):
        return CliRunner().invoke(app, ["serve", *args])

    return run


def test_binding_to_the_world_without_any_account_is_shouted_about(serve):
    result = serve("--host", "0.0.0.0")
    assert result.exit_code == 0
    assert "race" in result.output
    assert "0.0.0.0" in result.output


def test_the_local_default_says_nothing_about_being_grabbed(serve):
    assert "race" not in serve().output


def test_the_local_default_points_at_the_page_not_just_the_cli(serve):
    """第一个管理员现在网页上就能建，别只教人去敲 CLI。"""
    assert "/members" in serve().output


# ---------- 主密钥 ----------
#
# 没配 FR_SECRET_KEY 时，模型页上填的 API key 一个都存不进去（crypto 拒绝
# 落库，宁可不收也不明文存）。而这件事的症状是「key 存不进去」，离原因
# 「环境变量没加载」很远——2026-08-26 就这么绕了一圈。
#
# 起服务时本来就会报告「门锁没锁」「SSO 通没通」，主密钥是同一类的东西。


def test_a_missing_master_key_is_shouted_about(serve):
    out = serve().output
    assert crypto.MASTER_ENV in out
    assert "cannot be stored" in out


def test_a_configured_master_key_says_nothing_alarming(serve, monkeypatch):
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    assert "cannot be stored" not in serve().output


# ---------- --reload 下也要报 ----------


def test_reload_still_reports_the_state(serve):
    """原先 `--reload` 那条路径 return 得太早，门锁、Entra、主密钥、
    绑址警告一句都不打印——而改代码时用的正是这条路径。"""
    out = serve("--reload").output
    assert "Hot reload" in out
    assert "No accounts yet" in out
    assert crypto.MASTER_ENV in out


def test_reload_does_not_swallow_the_bind_warning(serve):
    assert "race" in serve("--reload", "--host", "0.0.0.0").output
