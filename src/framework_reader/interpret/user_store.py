"""Interpretation storage for user-imported frameworks. Main spec §7.3.5

`InterpretationStore` writes interpretations into `content/interpretations/` - that is **content we
publish**: into git, reviewed, baked into the content pack by `make build`. A user company's
own policy interpretations do not belong there - writing them in would absorb their internal documents.

So the user layer lives elsewhere: `user.sqlite`, next door to self-assessment. The interface is
deliberately identical to `InterpretationStore` (exists / save / load): `draft_all` can swap the
store and draft imported frameworks without knowing the difference.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from framework_reader.interpret.model import (
    Basis,
    Field,
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    InterviewRecord,
)


class UserInterpretationStore:
    def __init__(self, path: Path | None = None, locale: str = "zh-CN") -> None:
        self.path = path
        self.locale = locale

    def _conn(self):
        from framework_reader.userframework.store import connect

        conn = connect(self.path)
        assert conn is not None
        return conn

    def exists(self, control_id: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM user_interpretation_meta "
                "WHERE control_id = ? AND locale = ?",
                (control_id, self.locale),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def save(self, interp: Interpretation) -> None:
        """Replace whole: field rows are deleted then re-inserted, so fields dropped since last time leave no orphans."""
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM user_interpretation WHERE control_id = ? AND locale = ?",
                (interp.control_id, interp.locale),
            )
            conn.executemany(
                "INSERT INTO user_interpretation "
                "(control_id, locale, field, value_json, basis) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        interp.control_id, interp.locale, name,
                        json.dumps(field.value, ensure_ascii=False), field.basis.value,
                    )
                    for name, field in interp.fields.items()
                ],
            )
            conn.execute(
                "INSERT INTO user_interpretation_meta "
                "(control_id, locale, state, provenance, interview, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(control_id, locale) DO UPDATE SET "
                "state=excluded.state, provenance=excluded.provenance, "
                "interview=excluded.interview, updated_at=excluded.updated_at",
                (
                    interp.control_id, interp.locale, interp.state.value,
                    interp.provenance.model_dump_json(),
                    interp.interview.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def iter_all(self):
        """Parity with InterpretationStore: per-framework commands must treat both stores alike."""
        conn = self._conn()
        try:
            ids = [
                r[0] for r in conn.execute(
                    "SELECT control_id FROM user_interpretation_meta "
                    "WHERE locale = ? ORDER BY control_id", (self.locale,)
                )
            ]
        finally:
            conn.close()
        for control_id in ids:
            yield self.load(control_id)

    def by_state(self, state) -> list[Interpretation]:
        return [i for i in self.iter_all() if i.state is state]

    def load(self, control_id: str) -> Interpretation:
        conn = self._conn()
        try:
            meta = conn.execute(
                "SELECT state, provenance, interview FROM user_interpretation_meta "
                "WHERE control_id = ? AND locale = ?",
                (control_id, self.locale),
            ).fetchone()
            if meta is None:
                raise FileNotFoundError(f"No interpretation in the user library for {control_id}")
            rows = conn.execute(
                "SELECT field, value_json, basis FROM user_interpretation "
                "WHERE control_id = ? AND locale = ?",
                (control_id, self.locale),
            ).fetchall()
        finally:
            conn.close()
        return Interpretation(
            control_id=control_id,
            locale=self.locale,
            state=InterpretationState(meta["state"]),
            fields={
                r["field"]: Field(value=json.loads(r["value_json"]), basis=Basis(r["basis"]))
                for r in rows
            },
            interview=(
                InterviewRecord(**json.loads(meta["interview"]))
                if meta["interview"] else InterviewRecord()
            ),
            provenance=(
                InterpretationProvenance(**json.loads(meta["provenance"]))
                if meta["provenance"] else InterpretationProvenance()
            ),
        )


def store_for(view, user_db: Path | None = None, *, overlay: bool = False):
    """Pick the interpretation store by the framework's licensing tier.

    U-tier frameworks are user-imported: they can only land in the user library. Everything else is
    our own content and goes to `content/interpretations/`. Callers (CLI, web) always route through
    here - never construct InterpretationStore() yourself: that is exactly why a drafted imported

    ``overlay=True``: one-click drafting of built-in frameworks (800-53 etc.) on the web also lands
    in the user library as a working copy overlaid on the content pack, never in git.
    """
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.schema.entities import LicenseTier

    if overlay or (view is not None and view.tier == LicenseTier.U_USER):
        return UserInterpretationStore(user_db)
    return InterpretationStore()
