"""Assemble one drafting run. CLI and web share this single path.

Written twice, the two copies drift apart - say the CLI remembers to pick the store by framework
tier and the web forgets: an imported framework drafted on the web lands outside the user library
"""
import re
import sqlite3
from pathlib import Path

from framework_reader.interpret.batch import DraftReport, draft_all
from framework_reader.query.api import QueryAPI


class UnknownFrameworkError(Exception):
    """The framework id exists in neither the content pack nor the user library."""


def documents_for(view, user_db: Path | None):
    """Companion documents serve **frameworks the user imported themselves** only; built-ins always get None.

    Built-in frameworks (CSF / ISO / 800-53) are interpretations we publish: into git, reviewed, and
    baked into the content pack. Some company's internal policy inside them is both wrong and unpublishable.
    """
    from framework_reader.schema.entities import LicenseTier
    from framework_reader.userframework.documents import DocumentStore

    if view is None or view.tier != LicenseTier.U_USER:
        return None
    return DocumentStore(user_db)


def draft_framework(
    db: Path,
    framework_id: str,
    *,
    jobs: int = 4,
    force: bool = False,
    only: list[str] | None = None,
    full: bool = False,
    fill_blanks: bool = False,
    user_db: Path | None = None,
    overlay: bool = False,
) -> DraftReport:
    """Draft a whole framework. A missing API key raises MissingApiKeyError; the caller words it."""
    from framework_reader.interpret.drafter import DRAFT_FAILURE_DIR
    from framework_reader.interpret.user_store import store_for
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.prompts import PROMPT_VERSIONS, full_drafter_version

    api = QueryAPI(db, user_db=user_db)
    view = api.get_framework(framework_id)
    if view is None:
        raise UnknownFrameworkError(f"No such framework: {framework_id}")

    # The model and keys the admin configured on the web overlay the YAML presets.
    registry, key_lookup = effective_registry()
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()

    # Clear the previous round's failure samples, or build/draft_failures/ mixes stale entries in and misleads diagnosis.
    if DRAFT_FAILURE_DIR.exists():
        for stale in DRAFT_FAILURE_DIR.glob("*.txt"):
            stale.unlink()

    return draft_all(
        store_for(view, user_db, overlay=overlay), api,
        registry.build("drafter", guard=guard, key_lookup=key_lookup),
        documents=documents_for(view, user_db),
        framework_id=framework_id, model=role.model,
        prompt_version=(
            full_drafter_version() if full else PROMPT_VERSIONS["drafter"]
        ),
        provider=role.provider,
        jobs=jobs, force=force, only=only, full=full, fill_blanks=fill_blanks,
    )


def fill_blanks_one(
    db: Path, control_id: str, user_db: Path | None = None,
    overlay: bool = False,
) -> DraftReport:
    """Fill just this control's blank fields. The per-control "Fill the blanks" button lands here - not a whole-framework run."""
    framework_id = control_id.split(":", 1)[0]
    return draft_framework(
        db, framework_id, jobs=1, only=[control_id], full=True, fill_blanks=True,
        user_db=user_db, overlay=overlay,
    )


def rewrite_one(db: Path, control_id: str, field: str, instruction: str,
                user_db: Path | None = None):
    """Rewrite one field per the user's one-line instruction; returns the new value (persisting is the caller's call)."""
    from framework_reader.interpret.drafter import rewrite_field
    from framework_reader.interpret.render import FIELD_LABELS
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db

    api = QueryAPI(db, user_db=user_db)
    current = (api.interpretation(control_id).get(field) or {}).get("value")
    # The model and keys the admin configured on the web overlay the YAML presets.
    registry, key_lookup = effective_registry()
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()
    return rewrite_field(
        registry.build("drafter", guard=guard, key_lookup=key_lookup),
        control_id=control_id, field=field, label=dict(FIELD_LABELS).get(field, field),
        current=current, instruction=instruction, model=role.model,
        outcome=api.control_body(control_id),
    )


def rewrite_body(db: Path, control_id: str, instruction: str, current: str,
                 user_db: Path | None = None) -> str:
    """Revise **the user's own imported** control body per their instruction. Proposes only, never persists -
    writing happens when the user clicks Save (same gate as field rewrites).

    User frameworks only: a built-in control's body is official text; this route must never even be reached.
    """
    from framework_reader.llm.client import Message
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.prompts import load_prompt

    registry, key_lookup = effective_registry()
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()
    client = registry.build("drafter", guard=guard, key_lookup=key_lookup)

    user = (f"Control: {control_id}\n\n"
            f"The user's instruction: {instruction.strip()}\n\n"
            f"Current body:\n{current}")
    raw = client.complete(
        load_prompt("body_rewrite"), [Message(role="user", content=user)],
        model=role.model)
    # The prompt says body-only, but the model occasionally wraps it in fences anyway - strip them. If
    # nothing survives stripping, treat it as no change and return the original: an empty string must
    text = re.sub(r"^\s*```[a-z]*\s*|\s*```\s*$", "", (raw or "").strip())
    return text if text else current


def pending_controls(
    db: Path, framework_id: str, user_db: Path | None = None
) -> list[str]:
    """Leaf controls in this framework still without interpretations. Say what a drafting run will cost before it starts."""
    api = QueryAPI(db, user_db=user_db)
    return [
        c.id for c in api.list_controls(framework_id, active_only=True, leaf_only=True)
        if not api.interpretation(c.id)
    ]
