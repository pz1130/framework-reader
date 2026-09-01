from pathlib import Path

SELF = "test_no_network_in_tests.py"


def _test_sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in Path("tests").rglob("*.py")]


def test_no_test_reads_the_process_environment():
    """公有 CI 不接触 API key。测试要注入假 lookup，不许读环境。W2 spec §7

    注意：断言的是「有没有读环境」，不是「有没有出现 KEY 这个词」——
    tests/llm/test_registry.py 会把环境变量名当作假 lookup 的字典键，那是对的。
    """
    offenders = [
        str(path) for path, text in _test_sources()
        if ("os.environ" in text or "os.getenv" in text) and path.name != SELF
    ]
    assert offenders == [], f"这些测试读了进程环境：{offenders}"


def test_no_test_calls_the_live_check_command():
    """`fr llm check` / `fr entra check` 会发真实请求，测试永远不得调用它们。

    **先剥掉反引号里的内容再查。** 测试的文档字符串里说明「真实请求收在
    `_default_get` 里」是有价值的，那是文档不是调用；把提到它也算成违例，
    结果是逼人把解释删掉——检查越严，注释越少，不是我们要的。
    """
    offenders = [
        str(path) for path, text in _test_sources()
        if any(w in _without_code_spans(text)
               for w in ("_default_post", "_default_send",
                         "_default_fetch", "_default_get"))
        and path.name != SELF
    ]
    assert offenders == [], f"这些测试碰了真实出网路径：{offenders}"


def _without_code_spans(text: str) -> str:
    import re

    return re.sub(r"`[^`]*`", "``", text)


def test_no_test_imports_a_vendor_sdk_at_module_level():
    """anthropic / httpx 只在适配器内部按需 import；测试导入它们说明走错了路。"""
    offenders = [
        str(path) for path, text in _test_sources()
        if ("import anthropic" in text or "import httpx" in text) and path.name != SELF
    ]
    assert offenders == [], f"这些测试导入了厂商 SDK：{offenders}"


HTTPX_ALLOWED = {
    "openai_compat.py",      # chat，携带内容，被 GuardedClient 包住
    "anthropic_adapter.py",  # 同上
    "entra.py",              # OIDC 发现与换令牌，不携带内容
    "catalog.py",            # 模型目录，不携带内容
}


def test_only_the_declared_files_may_touch_httpx():
    """出网点必须是可数的。

    红线三真正要保的是**内容**不外流，不是「进程只准发一个请求」——
    见 2026-08-24 模型目录设计 §0。所以规矩不是「只许一个」，是
    「每一个都必须收在一个可注入替换的 _default_* 里，且清单写在这儿」。

    第五个文件里出现 httpx，这条就红。不靠人记得去翻 spec。
    """
    offenders = sorted(
        str(p) for p in Path("src").rglob("*.py")
        if "httpx" in p.read_text(encoding="utf-8") and p.name not in HTTPX_ALLOWED
    )
    assert offenders == [], f"这些文件碰了 httpx，但不在白名单里：{offenders}"


def test_content_dirs_that_must_exist_are_present():
    assert Path("content/llm_providers.yaml").exists()
    assert Path("content/lint.yaml").exists()
    assert Path("content/golden/NIST-CSF-2.0").is_dir()
