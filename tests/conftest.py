"""测试一律不碰真实的 $FRAMEWORK_READER_HOME。

自用日志是这个项目**唯一**的验证信号（主 spec §7.3.1）。测试套件跑一次
就往里灌上百条 `show` / `blindtest`，那个信号立刻失真——而且失真的方向
恰好是让人觉得「用得挺多」，是最舒服的一种自欺。

实测过：修这条之前，跑测试会在 ~/.framework_reader/usage.jsonl 里留下
112 条 show、102 条 blindtest，全是 CliRunner 的调用。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_reader_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("reader-home")
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(home))


@pytest.fixture(autouse=True)
def _plain_cli_output(monkeypatch):
    """GitHub Actions 上 Typer/Rich 会给 --help 上色，`--all` 被拆成
    `-` + ANSI + `-all`，字面断言就挂。本地终端不着色所以看不出来。"""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
