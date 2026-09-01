from pathlib import Path

import pytest

from framework_reader.llm.anthropic_adapter import AnthropicClient
from framework_reader.llm.guard import GuardedClient, PayloadGuard
from framework_reader.llm.openai_compat import OpenAICompatClient
from framework_reader.llm.retry import RetryingClient
from framework_reader.llm.registry import (
    DEFAULT_REGISTRY_PATH,
    LLMRegistry,
    MissingApiKeyError,
    UnknownProviderError,
)

REG = LLMRegistry.load(DEFAULT_REGISTRY_PATH)

# roles 是配置，会随手上有哪家的 key 变。测试断言的是「组装行为」，
# 不是「此刻 drafter 指向谁」——把当前配置写进断言，每换一次厂商就红一次。
ANY_KEY = lambda name: f"sk-test-{name.lower()}"  # noqa: E731


def test_ships_the_presets_the_spec_names():
    """预设清单是产品承诺的一部分，写死在测试里：少一家是回归，多一家要有人点头。"""
    assert {p.id for p in REG.providers} == {
        # 国内
        "deepseek", "qwen", "glm", "kimi", "doubao", "hunyuan",
        "minimax", "baichuan", "siliconflow",
        "ernie", "spark", "stepfun", "yi",
        # 国外
        "anthropic", "openai", "gemini", "grok", "mistral", "openrouter", "groq",
    }


def test_newly_added_presets_are_marked_unverified():
    """端点与模型名会漂，而我们没有这些家的 key，验不了活。

    验不了就**不许假装验过**——`fr llm check` 要真 key 才能发请求。
    note 里标明「未验活」，配了 key 的人自己跑一遍；通不过的按 YAML 头上
    那句规矩标灰保留、不删除。
    """
    unverified = {"ernie", "spark", "stepfun", "yi",
                  "gemini", "grok", "mistral", "openrouter", "groq"}
    assert {p.id for p in REG.providers if not p.verified} == unverified


def test_verified_is_a_field_not_a_word_inside_the_note():
    """原来这个标记是拼进 note 的句子里的（「百度千帆；未验活」），
    于是页面上没法把它单独成一列，只能整句塞进 <option> 里——
    那正是把下拉框撑到盖住半个屏幕的原因。"""
    for preset in REG.providers:
        assert "未验活" not in preset.note, preset.id


def test_the_preset_known_to_write_badly_says_so_where_people_choose(_=None):
    """minimax 的默认模型实测起草质量不合格——那是 drafter 当初从它换走的原因。

    这个事实原来只写在 YAML 的注释里，而**注释不进页面**：在 /models 上挑
    厂商的人看到的是一个和别家没有区别的选项。警告要出现在被选择的地方。
    """
    note = next(p.note for p in REG.providers if p.id == "minimax")
    assert "不建议" in note and "drafter" in note


def test_only_anthropic_claims_explicit_cache():
    """prompt caching 是 best-effort，其余厂商不得声称显式缓存。W2 spec §3.4①"""
    assert [p.id for p in REG.providers if p.explicit_cache] == ["anthropic"]


def test_every_preset_declares_a_key_env_and_default_model():
    for preset in REG.providers:
        assert preset.api_key_env, preset.id
        assert preset.default_model, preset.id


def test_openai_compat_presets_have_a_base_url():
    for preset in REG.providers:
        if preset.kind == "openai_compat":
            assert preset.base_url.startswith("https://"), preset.id


def test_three_roles_are_configured():
    assert {"drafter", "questioner", "extractor"} == set(REG.roles)


def test_every_configured_role_points_at_a_preset_that_exists():
    """换厂商时最容易写错的地方：roles 指了一个 providers 里没有的 id。"""
    for name in REG.roles:
        preset = REG.preset(REG.role(name).provider)
        assert preset.id == REG.role(name).provider
        assert REG.role(name).model, name


def test_build_wraps_every_configured_role_in_the_guard():
    """出网只有一条路径：registry 组装的 client 一律被红线包住。W2 spec §3.4③"""
    for name in REG.roles:
        client = REG.build(name, guard=PayloadGuard([]), key_lookup=ANY_KEY)
        assert isinstance(client, GuardedClient), name
        assert isinstance(client._inner, RetryingClient), name


def test_build_picks_the_adapter_matching_the_preset_kind(tmp_path: Path):
    """按 kind 选适配器。用自造的两条 role，不依赖当前 roles 指向谁。"""
    path = tmp_path / "p.yaml"
    path.write_text(
        "providers:\n"
        "  - {id: a, kind: anthropic, base_url: '', api_key_env: A_KEY,"
        " default_model: m, explicit_cache: true}\n"
        "  - {id: o, kind: openai_compat, base_url: 'https://x/v1',"
        " api_key_env: O_KEY, default_model: m}\n"
        "roles:\n"
        "  native: {provider: a, model: m}\n"
        "  compat: {provider: o, model: m}\n",
        encoding="utf-8",
    )
    reg = LLMRegistry.load(path)
    native = reg.build("native", guard=PayloadGuard([]), key_lookup=ANY_KEY)
    compat = reg.build("compat", guard=PayloadGuard([]), key_lookup=ANY_KEY)
    assert isinstance(native._inner._inner, AnthropicClient)
    assert isinstance(compat._inner._inner, OpenAICompatClient)


def test_missing_api_key_fails_loudly_and_names_the_env_var():
    """报的必须是当前配置那家的变量名，不是某个写死的名字。"""
    expected = REG.preset(REG.role("drafter").provider).api_key_env
    with pytest.raises(MissingApiKeyError, match=expected):
        REG.build("drafter", guard=PayloadGuard([]), key_lookup=lambda name: None)


def test_role_pointing_at_an_unknown_provider_is_rejected(tmp_path: Path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "providers: []\nroles:\n  drafter: {provider: nope, model: m}\n", encoding="utf-8"
    )
    with pytest.raises(UnknownProviderError, match="nope"):
        LLMRegistry.load(path).build("drafter", guard=PayloadGuard([]), key_lookup=ANY_KEY)


def test_role_may_override_the_preset_default_model():
    """role 可以指定与 preset 默认不同的 model；这里只要求它是该厂商的合法配置。"""
    for name in REG.roles:
        role = REG.role(name)
        preset = REG.preset(role.provider)
        assert role.model.strip(), name
        # 与默认相同或不同都合法——断言的是「显式写了」，不是写了哪个。
        assert isinstance(preset.default_model, str) and preset.default_model.strip()
