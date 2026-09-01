"""提示词与版本号。W2 spec §3.4② —— prompt_version 必须随解读一起留痕。"""
from pathlib import Path

PROMPT_DIR = Path(__file__).parent

PROMPT_VERSIONS = {
    "drafter": "2026.08-d2",
    "drafter_full": "2026.08-f2",
    "proofreader": "2026.08-p3",
    "questioner": "2026.08-q2",
    "extractor": "2026.08-x1",
    "bare_llm": "2026.08-b1",
    "rewriter": "2026.08-r1",
    "search_expand": "2026.08-s1",
}


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def golden_digest() -> str:
    """手写黄金样例内容的短指纹。

    few-shot 变了，提示词就变了，provenance 必须能区分——否则无法回答
    「这批解读是在哪版范例下生成的」。
    """
    import hashlib

    from framework_reader.interpret.golden import GOLDEN_ROOT

    blob = b"".join(
        path.read_bytes() for path in sorted(GOLDEN_ROOT.rglob("*.yaml"))
    )
    return hashlib.sha256(blob).hexdigest()[:8]


def full_drafter_version() -> str:
    return f"{PROMPT_VERSIONS['drafter_full']}+golden:{golden_digest()}"
