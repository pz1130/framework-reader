"""First operator on an empty box. Docker entrypoint calls this."""
import pytest
from typer.testing import CliRunner

from framework_reader.cli.main import app
from framework_reader.identity.store import IdentityStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    return IdentityStore()


def test_bootstrap_creates_admin_author_and_approver(store):
    account = store.bootstrap(email="admin@localhost", password="changeme")
    assert account is not None
    assert account.roles == {"admin", "author", "approver"}
    assert store.configured() is True


def test_bootstrap_is_idempotent(store):
    first = store.bootstrap(email="admin@localhost", password="changeme")
    again = store.bootstrap(email="other@localhost", password="nope")
    assert first is not None
    assert again is None
    assert store.by_email("other@localhost") is None
    assert store.by_email("admin@localhost") is not None


def test_cli_bootstrap_does_not_print_the_password(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    result = CliRunner().invoke(
        app,
        ["account", "bootstrap", "--email", "admin@localhost", "--password", "s3cret-s3cret"],
    )
    assert result.exit_code == 0
    assert "s3cret-s3cret" not in result.output
    assert "admin@localhost" in result.output
    assert "login" in result.output.lower()
