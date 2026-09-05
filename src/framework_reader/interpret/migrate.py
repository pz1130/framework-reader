"""Move user-framework interpretations that landed in the content repo back into the user library. Main spec §7.3.5

The storage layer already routes by tier (`interpret/user_store.store_for`), but what was **already
written into `content/interpretations/`** does not walk there on its own: those interpretations were
drafted fine, yet the query layer never reads there - drafting them achieved nothing.

The criterion is exactly one: **this framework is now in the user library**. It is user-imported; its
interpretations belong next to it. Every other directory in the content repo is untouched - that is what we publish.
"""
from pathlib import Path

import yaml
from pydantic import BaseModel

from framework_reader.interpret.model import Interpretation
from framework_reader.interpret.store import DEFAULT_ROOT
from framework_reader.interpret.user_store import UserInterpretationStore


class MigrationReport(BaseModel):
    moved: list[str] = []
    # (control_id or filename, reason). Every skip must state its reason - a silent skip reads as
    # "everything moved", and the missing ones surface only later, in the report.
    skipped: list[tuple[str, str]] = []
    deleted: list[str] = []


def migrate_user_drafts(
    root: Path = DEFAULT_ROOT, *, force: bool = False, delete: bool = False
) -> MigrationReport:
    """Move user-framework interpretation YAML into `user.sqlite`.

    By default it does not overwrite a copy already in the user library (the user may have edited it),
    and does not delete the originals (they are git-tracked; deleting them is a separate decision).
    """
    from framework_reader.userframework.store import UserFrameworkStore

    report = MigrationReport()
    root = Path(root)
    if not root.exists():
        return report

    frameworks = UserFrameworkStore()
    mine = {f.id for f in frameworks.list_frameworks()}
    store = UserInterpretationStore()

    for framework_id in sorted(mine):
        folder = root / framework_id
        if not folder.is_dir():
            continue
        known = frameworks.control_ids(framework_id)
        for path in sorted(folder.glob("*.yaml")):
            _migrate_one(path, known, store, report, force=force, delete=delete)
    return report


def _migrate_one(
    path: Path,
    known: set[str],
    store: UserInterpretationStore,
    report: MigrationReport,
    *,
    force: bool,
    delete: bool,
) -> None:
    try:
        interp = Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))
    except Exception as exc:                          # noqa: BLE001
        # One bad file must not drag the other hundred down with it.
        report.skipped.append((path.name, f"could not read: {type(exc).__name__}: {exc}"))
        return

    if interp.control_id not in known:
        # The spreadsheet was re-imported and ids changed. No page could reach a moved row anyway -
        # better on the report where the user can see it than kept as an orphan row.
        report.skipped.append((interp.control_id, "control no longer exists in the user library"))
        return

    if store.exists(interp.control_id) and not force:
        report.skipped.append((interp.control_id, "already exists in the user library (use --force to overwrite)"))
        return

    store.save(interp)
    report.moved.append(interp.control_id)
    if delete:
        # Delete only after the move has truly landed. Reversed order is a data-loss incident.
        path.unlink()
        report.deleted.append(str(path))
