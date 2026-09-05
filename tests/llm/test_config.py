"""管理员配的模型、key、限速与预算。见网页服务化设计 §6⑤⑥、§8 S4

本地部署时这些都在环境变量里。联网之后：key 存在我们的服务器上（要加密、
要脱敏回显），而起草花的是**组织的钱**——没有上限的花钱按钮，第一个手滑
的人就能把一个月的预算点完。
"""
from datetime import timedelta

import pytest

from framework_reader import crypto
from framework_reader.llm.config import BudgetError, ModelConfig


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    return ModelConfig()


# ---------- key ----------

def test_a_stored_key_comes_back_for_the_caller(config):
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    assert config.key("deepseek") == "sk-live-0123456789abcdef"


def test_the_key_is_not_in_the_database_in_the_clear(config):
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    assert "0123456789abcdef" not in config.path.read_bytes().decode(
        "utf-8", "ignore")


def test_what_the_page_gets_is_masked_not_the_key(config):
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    shown = config.masked()
    assert shown["deepseek"]["masked"].endswith("cdef")
    assert "0123456789" not in str(shown)


def test_the_summary_says_who_set_it_and_when(config):
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    assert config.masked()["deepseek"]["set_by"] == "boss@acme.cn"
    assert config.masked()["deepseek"]["set_at"]


def test_a_key_can_be_cleared(config):
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss@acme.cn")
    config.clear_key("deepseek")
    assert config.key("deepseek") is None
    assert "deepseek" not in config.masked()


def test_without_a_master_key_the_store_refuses_instead_of_saving_plaintext(
        config, monkeypatch):
    monkeypatch.delenv(crypto.MASTER_ENV, raising=False)
    with pytest.raises(crypto.SecretError):
        config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss")
    assert config.masked() == {}


def test_a_key_that_will_not_open_is_not_a_crash(config, monkeypatch):
    """换过 FR_SECRET_KEY 的部署，起草该说人话而不是 500。"""
    config.set_key("deepseek", "sk-live-0123456789abcdef", by="boss")
    monkeypatch.setenv(crypto.MASTER_ENV, crypto.new_master_key())
    with pytest.raises(crypto.SecretError):
        config.key("deepseek")


# ---------- 角色 → 厂商、模型 ----------

def test_a_role_can_be_pointed_at_a_provider(config):
    config.set_role("drafter", provider="qwen", model="qwen-max", by="boss")
    assert config.roles()["drafter"] == {"provider": "qwen", "model": "qwen-max"}


def test_an_unset_role_is_simply_absent(config):
    """没配的角色回落到 content/llm_providers.yaml，不在这里编一个默认值。"""
    assert config.roles() == {}


def test_setting_a_role_twice_keeps_the_last(config):
    config.set_role("drafter", provider="qwen", model="qwen-max", by="boss")
    config.set_role("drafter", provider="glm", model="glm-4-plus", by="boss")
    assert config.roles()["drafter"]["provider"] == "glm"


# ---------- 限速与预算 ----------

def test_the_defaults_are_finite(config):
    """没配上限 = 没有上限，那正是这一步要消灭的状态。"""
    limits = config.limits()
    assert limits["draft_cap_hour"] > 0
    assert limits["draft_cap_month"] > 0
    assert limits["draft_max_jobs"] > 0


def test_a_normal_draft_goes_through(config):
    config.charge_draft("ann@acme.cn", 30, what="ACME-1", running_jobs=0)


def test_one_person_cannot_burn_the_hour_cap(config):
    config.set_limits(draft_cap_hour=50, by="boss")
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    with pytest.raises(BudgetError):
        config.charge_draft("ann@acme.cn", 20, what="ACME-2", running_jobs=0)


def test_the_hour_cap_is_per_person_not_shared(config):
    config.set_limits(draft_cap_hour=50, by="boss")
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    config.charge_draft("bob@acme.cn", 40, what="ACME-1", running_jobs=0)


def test_an_old_charge_no_longer_counts_against_the_hour(config, monkeypatch):
    from framework_reader.llm import config as module

    config.set_limits(draft_cap_hour=50, by="boss")
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    later = module._now() + timedelta(hours=2)
    monkeypatch.setattr(module, "_now", lambda: later)
    config.charge_draft("ann@acme.cn", 40, what="ACME-2", running_jobs=0)


def test_the_month_cap_is_the_whole_organisation(config):
    config.set_limits(draft_cap_month=60, by="boss")
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    with pytest.raises(BudgetError):
        config.charge_draft("bob@acme.cn", 40, what="ACME-1", running_jobs=0)


def test_too_many_jobs_at_once_is_refused(config):
    config.set_limits(draft_max_jobs=2, by="boss")
    with pytest.raises(BudgetError):
        config.charge_draft("ann@acme.cn", 5, what="ACME-1", running_jobs=2)


def test_a_refused_draft_is_not_charged(config):
    """拒了还扣，等于第二次更容易被拒。"""
    config.set_limits(draft_cap_hour=50, by="boss")
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    with pytest.raises(BudgetError):
        config.charge_draft("ann@acme.cn", 20, what="ACME-2", running_jobs=0)
    assert config.spent_this_month() == 40


def test_the_refusal_says_the_number_not_just_no(config):
    config.set_limits(draft_cap_month=60, by="boss")
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    with pytest.raises(BudgetError) as caught:
        config.charge_draft("ann@acme.cn", 40, what="ACME-2", running_jobs=0)
    assert "60" in str(caught.value) and "40" in str(caught.value)


def test_spending_is_visible(config):
    config.charge_draft("ann@acme.cn", 30, what="ACME-1", running_jobs=0)
    config.charge_draft("bob@acme.cn", 12, what="ACME-2", running_jobs=0)
    assert config.spent_this_month() == 42


def test_a_zero_control_draft_is_free(config):
    """一条待起草都没有的框架，点了不该扣额度。"""
    config.set_limits(draft_cap_month=10, by="boss")
    config.charge_draft("ann@acme.cn", 0, what="ACME-1", running_jobs=0)
    assert config.spent_this_month() == 0


def test_remaining_draft_shrinks_after_a_charge(config):
    config.set_limits(draft_cap_hour=50, draft_cap_month=100, by="boss")
    assert config.remaining_draft("ann@acme.cn") == 50
    config.charge_draft("ann@acme.cn", 40, what="ACME-1", running_jobs=0)
    assert config.remaining_draft("ann@acme.cn") == 10


# ---------- 配好的东西真的被用上（否则这一页只是个漂亮的表单） ----------

def test_a_stored_key_is_what_the_drafter_gets(config):
    config.set_key("deepseek", "sk-from-the-web-page", by="boss")
    lookup = config.key_lookup(env_lookup={"DEEPSEEK_API_KEY": "sk-from-the-env"}.get)
    assert lookup("DEEPSEEK_API_KEY") == "sk-from-the-web-page"


def test_without_a_stored_key_the_environment_still_works(config):
    """本地部署与刚搭好的服务器：库里什么都没有，原来那套环境变量照跑。"""
    lookup = config.key_lookup(env_lookup={"DEEPSEEK_API_KEY": "sk-from-the-env"}.get)
    assert lookup("DEEPSEEK_API_KEY") == "sk-from-the-env"


def test_the_configured_role_overrides_the_yaml_preset(config):
    from framework_reader.llm.config import effective_registry

    config.set_role("drafter", provider="qwen", model="qwen-max", by="boss")
    registry, _ = effective_registry(config=config)
    assert registry.role("drafter").provider == "qwen"
    assert registry.role("drafter").model == "qwen-max"


def test_an_unconfigured_role_keeps_the_yaml_preset(config):
    from framework_reader.llm.config import effective_registry

    registry, _ = effective_registry(config=config)
    assert registry.role("drafter").provider == "deepseek"
