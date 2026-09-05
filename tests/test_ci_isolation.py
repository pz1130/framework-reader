from pathlib import Path


def test_no_test_depends_on_vendor_directory():
    """公有 CI 不接触 vendor/。任何测试引用 vendor/ 都会让 CI 在干净环境下挂掉。spec §10.C"""
    self = Path(__file__).resolve()
    offenders = []
    for path in Path("tests").rglob("*.py"):
        if path.resolve() == self:
            continue  # 本文件以字符串扫描该路径，不构成依赖
        if "vendor/" in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == [], f"这些测试引用了 vendor/：{offenders}"


def test_gitignore_excludes_vendor_and_keys():
    text = Path(".gitignore").read_text(encoding="utf-8")
    for pattern in ("vendor/", "*.key", "*.sqlite"):
        assert pattern in text, f".gitignore 缺 {pattern}"
