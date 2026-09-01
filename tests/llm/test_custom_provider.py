"""自定义端点：预设里没有的厂商、公司内网网关、Azure 部署、本机 vLLM/Ollama。

预设是硬编码的清单，永远追不上市面。但开这个口子意味着**管理员能指定
数据发往哪里**——payload 里是用户导入的框架正文与配套文档节选。

`GuardedClient` 那道红线管的是**内容**（受版权原文不出网），不管**目的地**。
目的地这条单独定，写在 `check_base_url` 里：

    https://  任意主机放行
    http://   只放行回环与私网段（Ollama / vLLM / 内网网关正是这一类）
    其余      拒绝

**这道校验只看字面量，不做 DNS 解析。** 一个域名此刻解析到哪、请求发出时
解析到哪，可以是两个地址（DNS rebinding），那不是一道配置期校验能解决的
问题——写在这里是免得有人以为它解决了。
"""
import pytest

from framework_reader import crypto
from framework_reader.llm.config import CustomProviderError, ModelConfig, check_base_url


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    return ModelConfig()


# ---------- 地址政策 ----------

@pytest.mark.parametrize("url", [
    "https://api.example.com/v1",
    "https://my-gateway.corp.cn:8443/openai/v1",
    "http://127.0.0.1:11434/v1",          # Ollama
    "http://localhost:8000/v1",           # vLLM
    "http://10.1.2.3/v1",                 # 内网
    "http://192.168.1.9:3000/v1",
    "http://172.16.0.8/v1",
    "http://[::1]:8080/v1",
])
def test_allowed_addresses(url):
    check_base_url(url)  # 不抛就算过


@pytest.mark.parametrize("url", [
    "http://api.example.com/v1",          # 公网明文：key 和正文都在这条连接里
    "http://8.8.8.8/v1",                  # 公网 IP 明文
    "http://172.32.0.1/v1",               # 172.32 不在 172.16/12 里
    "ftp://files.example.com",
    "file:///etc/passwd",
    "https://",                           # 没有主机
    "不是个地址",
    "",
])
def test_rejected_addresses(url):
    with pytest.raises(CustomProviderError):
        check_base_url(url)


# ---------- 增删改 ----------

def test_a_custom_provider_can_be_added_and_read_back(config):
    config.set_custom_provider("corp-gw", base_url="https://gw.acme.cn/v1",
                               default_model="qwen2.5-72b", by="boss@acme.cn")
    got = config.custom_providers()
    assert got["corp-gw"]["base_url"] == "https://gw.acme.cn/v1"
    assert got["corp-gw"]["default_model"] == "qwen2.5-72b"
    assert got["corp-gw"]["added_by"] == "boss@acme.cn"


def test_a_custom_provider_cannot_shadow_a_preset(config):
    """撞名的话，谁盖谁全看查表顺序——那种 bug 找起来最贵。"""
    with pytest.raises(CustomProviderError):
        config.set_custom_provider("deepseek", base_url="https://evil.example.com/v1",
                                   default_model="m", by="boss@acme.cn")


@pytest.mark.parametrize("bad", ["有中文", "带 空格", "UPPER", "斜/杠", ""])
def test_the_id_must_be_a_slug(config, bad):
    with pytest.raises(CustomProviderError):
        config.set_custom_provider(bad, base_url="https://x.example.com/v1",
                                   default_model="m", by="boss@acme.cn")


def test_a_bad_address_is_rejected_before_anything_is_written(config):
    with pytest.raises(CustomProviderError):
        config.set_custom_provider("leaky", base_url="http://api.example.com/v1",
                                   default_model="m", by="boss@acme.cn")
    assert "leaky" not in config.custom_providers()


def test_deleting_one_that_a_role_still_points_at_is_refused(config):
    """先改角色再删。删掉之后 drafter 指向一个不存在的厂商，
    下一次起草才会炸——那时候没人记得是这一步干的。"""
    config.set_custom_provider("corp-gw", base_url="https://gw.acme.cn/v1",
                               default_model="m", by="boss@acme.cn")
    config.set_role("drafter", provider="corp-gw", model="m", by="boss@acme.cn")
    with pytest.raises(CustomProviderError):
        config.delete_custom_provider("corp-gw")
    assert "corp-gw" in config.custom_providers()

    config.set_role("drafter", provider="deepseek", model="deepseek-chat", by="boss")
    config.delete_custom_provider("corp-gw")
    assert "corp-gw" not in config.custom_providers()


# ---------- 进 registry ----------

def test_a_custom_provider_shows_up_in_the_effective_registry(config):
    from framework_reader.llm.config import effective_registry

    config.set_custom_provider("corp-gw", base_url="https://gw.acme.cn/v1",
                               default_model="qwen2.5-72b", by="boss@acme.cn")
    registry, _ = effective_registry(config=config)
    preset = registry.preset("corp-gw")
    assert preset.base_url == "https://gw.acme.cn/v1"
    assert preset.kind == "openai_compat"     # 自定义端点一律走兼容口
    assert preset.explicit_cache is False     # 显式缓存只有 anthropic 能声称


def test_the_drafter_can_be_pointed_at_a_custom_provider(config):
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard

    config.set_custom_provider("corp-gw", base_url="https://gw.acme.cn/v1",
                               default_model="qwen2.5-72b", by="boss@acme.cn")
    config.set_key("corp-gw", "sk-corp-0123456789abcdef", by="boss@acme.cn")
    config.set_role("drafter", provider="corp-gw", model="qwen2.5-72b", by="boss")

    registry, key_lookup = effective_registry(config=config)
    client = registry.build("drafter", guard=PayloadGuard([]), key_lookup=key_lookup)
    assert client is not None          # 拿不到 key 会抛 MissingApiKeyError
