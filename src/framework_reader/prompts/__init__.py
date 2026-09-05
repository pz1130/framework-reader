"""Prompts and their versions. W2 spec §3.4② - prompt_version must be recorded alongside every interpretation."""
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
    """A short fingerprint of the handwritten golden-sample content used.

    Change the few-shot and the prompt has changed; provenance must tell them apart - otherwise
    nobody can answer "under which version of the examples was this batch generated".
    """
    import hashlib

    from framework_reader.interpret.golden import GOLDEN_ROOT

    blob = b"".join(
        path.read_bytes() for path in sorted(GOLDEN_ROOT.rglob("*.yaml"))
    )
    return hashlib.sha256(blob).hexdigest()[:8]


def full_drafter_version() -> str:
    return f"{PROMPT_VERSIONS['drafter_full']}+golden:{golden_digest()}"
