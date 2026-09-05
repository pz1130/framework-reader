"""Vendor model catalog: asks "which models do you have". See the 2026-08-24 model catalog design

**This is category-2 egress.** Egress that carries content (control clauses,
interpretations, companion-document excerpts) exists in exactly one place: assembled by
`llm/registry.py` and wrapped in `GuardedClient`. This module carries not a single word of
content — it sends one GET and one key. In the same category is the OIDC in
`identity/entra.py`. The rule is the same: the real request lives in the single function
`_default_get`, injectable and replaceable, and a test asserts that no test ever touches it.

Does not touch the database. Caching is `llm/config.py`'s business — this module is only
responsible for "what came back from the question".
"""
from collections.abc import Callable

from framework_reader.llm.registry import ProviderPreset

HttpGet = Callable[[str, dict], dict]

# If a catalog query gets no reply within 15 s, treat it as unsupported. Chat gets 120 s —
# that is time left for the model to generate, which is a different thing from listing a
# catalog.
TIMEOUT_SECONDS = 15.0

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# Filtered out by id substring. The siliconflow and openrouter catalogs carry a hundred or
# two of these models; they will never be used as a drafter, and leaving them in the
# dropdown just buries the entry a person is looking for.
#
# **This list will over-match**: someday some vendor names a chat model with `vision-ocr`
# in it, and it gets swallowed. The cost is acceptable, because the manual-entry box is
# kept forever — the consequence of over-matching is "not in the dropdown, type it in by
# hand", not "cannot be used".
NON_CHAT_MARKERS = (
    "embed", "rerank", "tts", "whisper", "audio", "moderation",
    "image", "vision-ocr", "stable-diffusion", "flux",
)


class CatalogError(Exception):
    """Fetching the catalog failed. `kind` decides what the page says.

    - `auth`        —— this key was rejected (401/403)
    - `unsupported` —— this vendor offers no catalog, or the returned shape is one we do not recognize (404 / unparseable)
    - `unreachable` —— timeout, connection failure, 5xx
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _default_get(url: str, headers: dict) -> dict:
    import httpx

    resp = httpx.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _request(preset: ProviderPreset, api_key: str) -> tuple[str, dict]:
    if preset.kind == "anthropic":
        return ANTHROPIC_MODELS_URL, {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    return f"{preset.base_url.rstrip('/')}/models", {
        "Authorization": f"Bearer {api_key}",
    }


def _status_of(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in NON_CHAT_MARKERS)


def fetch_models(
    preset: ProviderPreset, api_key: str, *, http_get: HttpGet | None = None
) -> list[str]:
    """Asks the catalog once and returns a sorted, deduplicated list of ids with
    non-chat models filtered out.

    **The key must never appear in an exception message.** That message is rendered
    verbatim on the page.
    """
    get = http_get or _default_get
    url, headers = _request(preset, api_key)
    try:
        payload = get(url, headers)
    except Exception as exc:  # noqa: BLE001 —— any failure must map to one of the three kinds
        status = _status_of(exc)
        if status in (401, 403):
            raise CatalogError(
                "auth",
                f"{preset.id} rejected this key. If you are sure it is correct, "
                "the provider's catalog API may need extra permissions - the key is saved, and drafting still works.",
            ) from None
        if status == 404:
            raise CatalogError(
                "unsupported", f"{preset.id} provides no model catalog; enter the model name manually.") from None
        raise CatalogError(
            "unreachable", f"Could not reach {preset.id}; you can click Refresh later.") from None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise CatalogError(
            "unsupported",
            f"{preset.id} returned an unrecognized catalog format; enter the model name manually.")

    ids = {
        str(row["id"]).strip()
        for row in data
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    return sorted(m for m in ids if is_chat_model(m))
