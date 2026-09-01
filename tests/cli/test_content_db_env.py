"""内容包的位置可以由环境变量给。主 spec §7.3.7

`fr` 装进 PATH 之后，真实使用时的 cwd 是你正在写的那份文档所在的目录，
不是这个仓库。默认值是相对路径 `build/content.sqlite`，那时找不到——
第一次真用会死在 `no such file`，而死因清单会把它记成「太麻烦 = 壳错」。
**一个 setup 问题伪装成产品结论**，正是那套判据最怕的污染。

命名跟着已有的 `FRAMEWORK_READER_HOME` 走，不另起一套。
"""
import importlib
import os
from pathlib import Path


def _reload(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("FR_CONTENT_DB", raising=False)
    else:
        monkeypatch.setenv("FR_CONTENT_DB", value)
    import framework_reader.cli.main as cli
    import framework_reader.web.app as web
    return importlib.reload(cli), importlib.reload(web)


def test_defaults_to_relative_path_when_unset(monkeypatch):
    """没设就是老样子——仓库里 `make build` 之后直接跑，行为一个字不变。"""
    cli, web = _reload(monkeypatch, None)
    assert cli.DEFAULT_DB == Path("build/content.sqlite")
    assert web.DEFAULT_DB == Path("build/content.sqlite")


def test_env_var_wins(monkeypatch, tmp_path):
    """设了就用绝对路径，CLI 与 web 壳同一个来源——分两处读，两处就会长得不一样。"""
    target = tmp_path / "somewhere" / "content.sqlite"
    cli, web = _reload(monkeypatch, str(target))
    assert cli.DEFAULT_DB == target
    assert web.DEFAULT_DB == target
    _reload(monkeypatch, None)  # 别把改过的模块留给后面的测试
