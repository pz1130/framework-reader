"""Visual skin layered over the shell in views.page().

Presentation only: extra CSS/JS and decorative chrome. No routes, permissions or data
are decided here; `may` is passed in so navigation mirrors what the top bar already shows.
Animations are on by default and controlled only by the in-app toggle (localStorage
"fr-motion"); the OS reduce-motion setting is intentionally not consulted.
"""
from __future__ import annotations

from collections.abc import Callable

_MOTION_BOOT = (
    '<script>try{if(localStorage.getItem("fr-motion")==="off")'
    'document.documentElement.classList.add("motion-off")}catch(e){}</script>'
)

CSS = ""
HEAD_JS = ""
TAIL_JS = ""


def head() -> str:
    return _MOTION_BOOT + (f"<script>{HEAD_JS}</script>" if HEAD_JS else "")


def chrome(*, bare: bool, may: Callable[[str], bool], who: str, title: str) -> str:
    return ""


def tail() -> str:
    return (f"<style>{CSS}</style>" if CSS else "") + (
        f"<script>{TAIL_JS}</script>" if TAIL_JS else "")
