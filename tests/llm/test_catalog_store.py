"""目录缓存。页面从缓存读，不在渲染时出网。"""
import pytest

from framework_reader import crypto
from framework_reader.llm.config import ModelConfig


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    return ModelConfig()


def test_nothing_fetched_yet_is_none_not_an_empty_list(config):
    """「没拉过」和「拉到了但是空的」是两件事，页面上说的话也不一样。"""
    assert config.catalog("deepseek") is None


def test_a_catalog_comes_back_with_when_it_was_fetched(config):
    config.set_catalog("deepseek", ["deepseek-chat", "deepseek-reasoner"])
    got = config.catalog("deepseek")
    assert got["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert got["error"] == ""
    assert got["fetched_at"]        # 一份不知道多旧的清单和没有清单一样危险


def test_a_failure_is_stored_too(config):
    """失败也要记：页面要说清楚「为什么这儿没有下拉」。"""
    config.set_catalog("qwen", [], error="qwen 不提供模型目录，模型名手填。")
    got = config.catalog("qwen")
    assert got["models"] == []
    assert "不提供模型目录" in got["error"]


def test_fetching_again_replaces_the_old_one(config):
    config.set_catalog("deepseek", ["old"])
    config.set_catalog("deepseek", ["new"])
    assert config.catalog("deepseek")["models"] == ["new"]


def test_a_successful_fetch_clears_a_previous_error(config):
    config.set_catalog("deepseek", [], error="没能连上")
    config.set_catalog("deepseek", ["deepseek-chat"])
    assert config.catalog("deepseek")["error"] == ""


def test_catalogs_lists_every_provider(config):
    config.set_catalog("deepseek", ["a"])
    config.set_catalog("qwen", [], error="x")
    assert set(config.catalogs()) == {"deepseek", "qwen"}


def test_clearing_the_key_clears_the_catalog(config):
    """key 没了，那份清单也失去意义——留着只会让人以为还能选。"""
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    config.set_catalog("deepseek", ["deepseek-chat"])
    config.clear_key("deepseek")
    assert config.catalog("deepseek") is None


def test_deleting_a_custom_provider_clears_its_catalog(config):
    config.set_custom_provider("corp-gw", base_url="https://gw.acme.cn/v1",
                               default_model="m", by="boss@acme.cn")
    config.set_catalog("corp-gw", ["m"])
    config.delete_custom_provider("corp-gw")
    assert config.catalog("corp-gw") is None
