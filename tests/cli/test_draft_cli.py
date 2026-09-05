from typer.testing import CliRunner

from framework_reader.cli.main import app


def test_draft_help_exposes_all_flag():
    result = CliRunner().invoke(app, ["draft", "--help"])
    assert result.exit_code == 0
    assert "--all" in result.stdout
    assert "whole framework" in result.stdout or "no-op" in result.stdout


def test_interview_help_exposes_force_flag():
    result = CliRunner().invoke(app, ["interview", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.stdout


def test_migrate_drafts_help_explains_what_it_moves():
    result = CliRunner().invoke(app, ["migrate-drafts", "--help"])
    assert result.exit_code == 0
    assert "--delete" in result.stdout and "user library" in result.stdout


# ---------- 用户框架不该走作者的工具 ----------

import pytest


@pytest.fixture
def imported(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.userframework.store import UserFrameworkStore

    UserFrameworkStore().add_framework(
        framework_id="ACME-SEC-2026", name="ACME 制度",
        controls=[("4.1", "日志留存", None, "留存六个月。")],
    )


def test_interview_refuses_an_imported_framework(imported):
    """访谈是作者给内置框架签字的工具：signer 是作者、写的是 content/。

    对着用户导入的框架跑，会把用户自己公司的解读写进产品的内容仓。
    """
    result = CliRunner().invoke(app, ["interview", "ACME-SEC-2026:4.1"])
    assert result.exit_code != 0
    assert "imported" in result.stdout


def test_the_refusal_points_at_where_users_actually_edit(imported):
    result = CliRunner().invoke(app, ["interview", "ACME-SEC-2026:4.1"])
    assert "fr serve" in result.stdout or "网页" in result.stdout


def test_interview_refuses_before_spending_a_single_model_call(imported, monkeypatch):
    """拦在装配之前。拦在模型调用之后，钱已经花了。"""
    def explode(*_a, **_kw):
        raise AssertionError("不该走到组装 client 这一步")

    monkeypatch.setattr(
        "framework_reader.llm.registry.LLMRegistry.load", explode
    )
    assert CliRunner().invoke(app, ["interview", "ACME-SEC-2026:4.1"]).exit_code != 0


def test_the_signer_is_not_hardcoded_to_the_author():
    """谁跑这条命令就记谁的名。硬编码成作者，等于替别人签字。"""
    import inspect

    from framework_reader.cli import main

    assert 'signer: str = "jc"' not in inspect.getsource(main.interview)


def test_proofread_finds_an_imported_frameworks_interpretations(
    imported, monkeypatch, tmp_path
):
    """校对也按框架选存储，否则对着用户框架永远只说「没有可校对的解读」。"""
    import sqlite3

    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation,
    )
    from framework_reader.interpret.user_store import UserInterpretationStore
    from framework_reader.llm.registry import LLMRegistry, MissingApiKeyError
    from framework_reader.pack.db import create_schema

    UserInterpretationStore().save(Interpretation(
        control_id="ACME-SEC-2026:4.1",
        fields={n: Field(value="模型写的", basis=Basis.INFERRED) for n in ALL_FIELDS},
    ))

    def no_key(*_a, **_kw):
        raise MissingApiKeyError("环境变量 DEEPSEEK_API_KEY 没设")

    monkeypatch.setattr(LLMRegistry, "build", no_key)
    # QueryAPI 只读打开内容包。CI 没有 build/content.sqlite，默认路径会
    # OperationalError；本地之所以绿是因为开发机上碰巧有这份构建产物。
    db = tmp_path / "content.sqlite"
    conn = sqlite3.connect(db)
    create_schema(conn)
    conn.close()
    result = CliRunner().invoke(
        app, ["proofread", "--framework-id", "ACME-SEC-2026", "--db", str(db)]
    )
    assert "No interpretations to proofread" not in result.stdout
    assert "DEEPSEEK_API_KEY" in result.stdout


# ---------- fr account ----------

@pytest.fixture
def identity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    from framework_reader.identity.store import IdentityStore

    return IdentityStore()


def test_account_list_tells_you_how_to_bootstrap(identity_home):
    """一个账号都没有时，网页不锁门。得说清楚怎么建第一个。"""
    result = CliRunner().invoke(app, ["account", "list"])
    assert "does not require login" in result.stdout and "invite" in result.stdout


def test_an_invite_prints_a_link_exactly_once(identity_home):
    result = CliRunner().invoke(
        app, ["account", "invite", "boss@acme.cn", "--role", "admin"])
    assert result.exit_code == 0
    assert "/invite/" in result.stdout and "shown only once" in result.stdout


def test_the_invite_token_is_not_stored_in_the_clear(identity_home):
    import re
    import sqlite3

    result = CliRunner().invoke(app, ["account", "invite", "boss@acme.cn"])
    token = re.search(r"/invite/(\S+)", result.stdout).group(1)
    conn = sqlite3.connect(identity_home.path)
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM invite")]
    conn.close()
    assert token not in stored


def test_inviting_an_unknown_role_fails_loudly(identity_home):
    result = CliRunner().invoke(
        app, ["account", "invite", "x@acme.cn", "--role", "god"])
    assert result.exit_code != 0 and "no such role" in result.stdout


def test_the_last_admin_cannot_be_revoked_from_the_cli(identity_home):
    identity_home.create_account(email="boss@acme.cn", password="pw", roles=("admin",))
    result = CliRunner().invoke(app, ["account", "revoke", "boss@acme.cn", "admin"])
    assert result.exit_code != 0 and "last admin" in result.stdout


def test_granting_a_role_shows_the_result(identity_home):
    identity_home.create_account(email="boss@acme.cn", password="pw", roles=("admin",))
    result = CliRunner().invoke(app, ["account", "grant", "boss@acme.cn", "approver"])
    assert "approver" in result.stdout


def test_role_changes_land_in_the_audit_log(identity_home):
    identity_home.create_account(email="boss@acme.cn", password="pw", roles=("admin",))
    CliRunner().invoke(app, ["account", "grant", "boss@acme.cn", "author"])
    assert any(e["event"] == "role.grant" for e in identity_home.audit())
