"""YAML storage for interpretations. Main spec §9: content lives as YAML in git; SQLite is a build artifact."""
import os
from collections.abc import Iterator
from pathlib import Path

import yaml

from framework_reader.interpret.model import (
    Interpretation,
    InterpretationState,
    RawAnswer,
)

DEFAULT_ROOT = Path("content/interpretations")


class UserFrameworkInContentError(Exception):
    """Someone is trying to write a user-imported framework into the product's content repo."""


class InterpretationStore:
    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def path_for(self, control_id: str) -> Path:
        framework, local = control_id.split(":", 1)
        return self.root / framework / f"{local}.yaml"

    def exists(self, control_id: str) -> bool:
        return self.path_for(control_id).exists()

    def save(self, interp: Interpretation) -> Path:
        self._guard_content_root(interp.control_id)
        path = self.path_for(interp.control_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = interp.model_dump(mode="json", exclude_none=False)
        text = yaml.safe_dump(
            payload, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def _guard_content_root(self, control_id: str) -> None:
        """User-imported frameworks may not enter `content/interpretations/`.

        The drafting path already routes by tier (interpret.user_store.store_for), but that routing
        only holds if **every call site remembers to use it** - b971e12 missed one. The gate sits at
        the write entry itself, so the next write command hits it even if its author forgot.

        What is guarded is this one directory, not the class: tests and migrations must stay able to
        write user-framework YAML elsewhere. The test is "is this framework in the user library", not the id's shape.
        """
        if Path(self.root).resolve() != DEFAULT_ROOT.resolve():
            return
        framework_id = control_id.split(":", 1)[0]
        try:
            from framework_reader.userframework.store import UserFrameworkStore

            mine = {f.id for f in UserFrameworkStore().list_frameworks()}
        except Exception:                                   # noqa: BLE001
            return          # user library unreadable: do not block here - this is the second gate, not the only one
        if framework_id not in mine:
            return
        raise UserFrameworkInContentError(
            f"{framework_id} is a framework you imported; its interpretations must not be written into {DEFAULT_ROOT}; "
            "that is content the product publishes. The proper home is the user library user.sqlite:"
            " Both drafting and rewriting go through interpret.user_store (store_for picks the storage by tier)."
        )

    def load(self, control_id: str) -> Interpretation:
        path = self.path_for(control_id)
        if not path.exists():
            raise FileNotFoundError(f"No interpretation file for {control_id}: {path}")
        return Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))

    def iter_all(self) -> Iterator[Interpretation]:
        for path in sorted(self.root.rglob("*.yaml")):
            yield Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))

    def by_state(self, state: InterpretationState) -> list[Interpretation]:
        return [i for i in self.iter_all() if i.state is state]

    def append_raw(self, control_id: str, n: int, text: str) -> None:
        """Persist immediately after each answer; re-answering the same question overwrites, never appends. W2 spec §6"""
        interp = self.load(control_id)
        kept = [r for r in interp.interview.raw if r.n != n]
        kept.append(RawAnswer(n=n, text=text))
        interp.interview.raw = sorted(kept, key=lambda r: r.n)
        self.save(interp)
