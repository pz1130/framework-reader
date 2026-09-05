"""QueryAPI - the one piece of W1 expected to survive long term. spec §8①

No caller (CLI, future web backend) may write raw SQL.
"""
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from framework_reader import sqlite_setup
from framework_reader.schema.mapping import EXPORTABLE_LEVELS

_EXPORTABLE = tuple(sorted(l.value for l in EXPORTABLE_LEVELS))


class FrameworkView(BaseModel):
    id: str
    name: str
    version: str
    tier: str


class ControlView(BaseModel):
    id: str
    framework_id: str
    label: str
    status: str


class ControlSummary(ControlView):
    has_interpretation: bool
    interpretation_state: str | None = None


class SupersessionView(BaseModel):
    control_id: str
    label: str
    status: str
    relation: str


class SupersessionEdge(BaseModel):
    """One supersession relation within a framework, both ends carrying titles and interpretation state.

    The supersession page must show "who can inherit from whom" in one view; single-ended
    accessors (superseded_by / supersedes) are not enough - they cannot see whether the other side has
    """

    old_id: str
    new_id: str
    relation: str
    old_label: str
    new_label: str
    old_state: str | None = None
    new_state: str | None = None


class NeighborView(BaseModel):
    control_id: str
    label: str
    relation: str
    level: str
    source: str
    exportable: bool


class QueryAPI:
    """The content layer is read-only, the user layer writable, storage separate; **the query layer reads both as one**.

    Imported frameworks land in the user database (main spec §6.1), yet browsing, self-assessment,
    and gap reports must see them - otherwise an import simply does not exist inside the product.
    The trick: ATTACH the user database, build two union views, and from then on query the views
    """

    def __init__(self, db_path: Path, user_db: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        # Read-only, so journal_mode is left alone - but expect lock waits: the user
        # database about to be ATTACHed may have someone writing to it.
        sqlite_setup.prepare(self._conn, writable=False)
        self._attach_user_layer(user_db)

    def _attach_user_layer(self, user_db: Path | None) -> None:
        from framework_reader.userframework.store import connect, default_path

        path = Path(user_db) if user_db else default_path()
        joined = False
        if connect(path, create=False) is not None:
            try:
                self._conn.execute(
                    "ATTACH DATABASE ? AS userdb", (f"file:{path}?mode=ro",)
                )
                # An older user database may not have these two tables yet - probe before unioning.
                self._conn.execute("SELECT 1 FROM userdb.user_framework LIMIT 1")
                joined = True
            except sqlite3.Error:
                joined = False
        # Keep as instance attributes: late-lookup methods (control_body) also need to
        # know whether the user layer is present - if not, never touch userdb.* tables.
        self._joined = joined
        if joined:
            self._conn.executescript(
                "CREATE TEMP VIEW all_framework AS "
                "  SELECT id, name, version, tier FROM main.framework "
                "  UNION ALL SELECT id, name, version, 'U' AS tier "
                "    FROM userdb.user_framework;"
                "CREATE TEMP VIEW all_control AS "
                "  SELECT id, framework_id, label, status, parent_id, 0 AS sort_key "
                "    FROM main.framework_control "
                "  UNION ALL SELECT id, framework_id, label, 'active' AS status, "
                "    parent_id, sort_key FROM userdb.user_control;"
                # Interpretations drafted for imported frameworks also live in the user database.
                # Without this branch, `fr draft` reports success while the page forever shows
                # "no interpretation yet". **User-edited fields override the content pack - per field.**
                #
                # This used to be UNION ALL: when both sides had the same field, two rows came
                # back and interpretation() collected them into a dict - which row survived
                # depended on SQL return order. Undefined. The moment built-in frameworks became
                # editable on the web, that was a pit guaranteed to be stepped on.
                #
                # Per field, not per record: editing "how to implement" must not wipe the other
                # six fields from the content pack.
                "CREATE TEMP VIEW all_interpretation AS "
                "  SELECT f.control_id, f.locale, f.field, f.value_json, "
                "    f.basis, m.state FROM userdb.user_interpretation f "
                "    JOIN userdb.user_interpretation_meta m "
                "      ON m.control_id = f.control_id AND m.locale = f.locale "
                # User rows with empty values are never returned. write_field writes all seven
                # fields at once; the six untouched ones are null. Keep them and one field
                # returns two rows while the dict keeps one - which one is luck.
                # The UI renders "field missing" and "value empty" identically; dropping is lossless.
                "   WHERE f.value_json <> 'null' "
                "  UNION ALL "
                "  SELECT i.control_id, i.locale, i.field, i.value_json, "
                "    i.basis, i.state FROM main.interpretation i "
                "   WHERE NOT EXISTS ("
                "     SELECT 1 FROM userdb.user_interpretation u "
                "      WHERE u.control_id = i.control_id "
                "        AND u.locale = i.locale AND u.field = i.field "
                # **A row does not mean written.** write_field writes all seven fields at once;
                # the six untouched ones are null. Using "has a row" as the test, editing one
                # field would blank the other six from the content pack.
                #
                # The price: clear a built-in field and the content-pack version resurfaces.
                # Acceptable - on a built-in control, "clear" reads exactly as "restore default".
                "        AND u.value_json <> 'null');"
            )
        else:
            self._conn.executescript(
                "CREATE TEMP VIEW all_framework AS "
                "  SELECT id, name, version, tier FROM main.framework;"
                "CREATE TEMP VIEW all_control AS "
                "  SELECT id, framework_id, label, status, parent_id, 0 AS sort_key "
                "    FROM main.framework_control;"
                "CREATE TEMP VIEW all_interpretation AS "
                "  SELECT control_id, locale, field, value_json, basis, state "
                "    FROM main.interpretation;"
            )

    def get_control(self, control_id: str) -> ControlView | None:
        row = self._conn.execute(
            "SELECT id, framework_id, label, status FROM all_control WHERE id = ?",
            (control_id,),
        ).fetchone()
        return ControlView(**dict(row)) if row else None

    def get_framework(self, framework_id: str) -> FrameworkView | None:
        """A framework's display name. Needed to render mappings; callers write no SQL."""
        row = self._conn.execute(
            "SELECT id, name, version, tier FROM all_framework WHERE id = ?",
            (framework_id,),
        ).fetchone()
        return FrameworkView(**dict(row)) if row else None

    def neighbors(self, control_id: str, exportable_only: bool = False) -> list[NeighborView]:
        # Positional ? placeholders only, bound in order of appearance; never mix in ?1 numbering.
        params: list[str] = [control_id, control_id, control_id, control_id]
        level_clause = ""
        if exportable_only:
            placeholders = ",".join("?" for _ in _EXPORTABLE)
            level_clause = f"AND m.level IN ({placeholders})"
            params.extend(_EXPORTABLE)

        rows = self._conn.execute(
            f"""
            SELECT
                CASE WHEN m.from_id = ? THEN m.to_id ELSE m.from_id END AS control_id,
                c.label AS label, m.relation, m.level, m.source
            FROM mapping m
            JOIN all_control c
              ON c.id = CASE WHEN m.from_id = ? THEN m.to_id ELSE m.from_id END
            WHERE (m.from_id = ? OR m.to_id = ?) {level_clause}
            ORDER BY control_id
            """,
            params,
        ).fetchall()

        return [
            NeighborView(
                control_id=r["control_id"], label=r["label"], relation=r["relation"],
                level=r["level"], source=r["source"],
                exportable=r["level"] in _EXPORTABLE,
            )
            for r in rows
        ]

    def superseded_by(self, control_id: str) -> list[SupersessionView]:
        """Where this old number lives now. The question everyone holding CSF 1.1 material asks."""
        return self._supersession_rows(
            "SELECT s.new_id AS control_id, c.label, c.status, s.relation "
            "FROM control_supersession s JOIN all_control c ON c.id = s.new_id "
            "WHERE s.old_id = ? ORDER BY s.new_id",
            control_id,
        )

    def supersedes(self, control_id: str) -> list[SupersessionView]:
        """Which old numbers landed on this control."""
        return self._supersession_rows(
            "SELECT s.old_id AS control_id, c.label, c.status, s.relation "
            "FROM control_supersession s JOIN all_control c ON c.id = s.old_id "
            "WHERE s.new_id = ? ORDER BY s.old_id",
            control_id,
        )

    def _supersession_rows(self, sql: str, control_id: str) -> list[SupersessionView]:
        rows = self._conn.execute(sql, (control_id,)).fetchall()
        return [SupersessionView(**dict(r)) for r in rows]

    def supersessions_in(self, framework_id: str) -> list[SupersessionEdge]:
        """Every supersession edge in this framework, returned whole. Data source for the supersession page.

        Cross-framework edges do not count: inheritance only exists within one framework's supersession -
        an old control's interpretation flowing onto another framework's control is misattribution.
        The state comes from the union view: an edge is inheritable only when the old end has an interpretation.
        """
        rows = self._conn.execute(
            "SELECT s.old_id, s.new_id, s.relation,"
            "       oc.label AS old_label, nc.label AS new_label,"
            "       (SELECT state FROM all_interpretation i"
            "         WHERE i.control_id = s.old_id LIMIT 1) AS old_state,"
            "       (SELECT state FROM all_interpretation i"
            "         WHERE i.control_id = s.new_id LIMIT 1) AS new_state "
            "FROM control_supersession s "
            "JOIN all_control oc ON oc.id = s.old_id "
            "JOIN all_control nc ON nc.id = s.new_id "
            "        AND nc.framework_id = oc.framework_id "
            "WHERE oc.framework_id = ? ORDER BY s.old_id, s.new_id",
            (framework_id,),
        ).fetchall()
        return [SupersessionEdge(**dict(r)) for r in rows]

    # "Label is the body" frameworks: a CSF 2.0 subcategory has no other body - that one sentence
    # is both its title and its entire content, so the official label IS the control body.
    # Counterexamples: 800-53's label is the control title (the body is a separate should-statement, not loaded);
    # ISO's label is a self-written short title. Whoever satisfies "label = entire text" joins this
    # set - maintain it here when new frameworks are imported.
    LABEL_AS_BODY = frozenset({"NIST-CSF-2.0"})

    def _user_body(self, control_id: str) -> str | None:
        """The body as it exists in the user layer (override first, imported rows after).
        A missing user layer or missing tables (old database) reads as None - never break the official path below."""
        if not self._joined:
            return None
        try:
            row = self._conn.execute(
                "SELECT body FROM userdb.control_body_override "
                "WHERE control_id = ?", (control_id,)
            ).fetchone()
            if row:
                return row["body"]
            row = self._conn.execute(
                "SELECT body FROM userdb.user_control WHERE id = ?",
                (control_id,)).fetchone()
            return row["body"] if row else None
        except sqlite3.Error:
            return None

    def _official_label_body(self, control_id: str) -> str:
        """The body realised from the content library's official label. Only for "label is body" frameworks."""
        try:
            row = self._conn.execute(
                "SELECT label FROM main.framework_control "
                "WHERE id = ? AND label_is_original = 1 "
                f"AND framework_id IN ({','.join('?' * len(self.LABEL_AS_BODY))})",
                (control_id, *self.LABEL_AS_BODY),
            ).fetchone()
        except sqlite3.Error:
            return ""
        return (row["label"] if row else "") or ""

    def control_body(self, control_id: str) -> str:
        """This control's body, in order: user override / imported body > official label.

        Built-in controls read the override layer (control_body_override, pasted by the user);
        imported controls read user_control; with neither, fall back to the content library's
        official label - but only for LABEL_AS_BODY frameworks: passing off 800-53's control
        title or ISO's self-written short title as a body misleads. The user layer being
        absent does not affect this path - the CLI with only the content library must still
        read CSF bodies. The pack's original_text is always empty (main spec §3.2②).
        """
        user = self._user_body(control_id)
        if user is not None and user.strip():
            return user
        return self._official_label_body(control_id)

    def body_is_official(self, control_id: str) -> bool:
        """Is the body currently shown the official one (label-realised) -
        decides whether that block is labelled "official text" or "your imported text"."""
        user = self._user_body(control_id)
        if user is not None and user.strip():
            return False
        return bool(self._official_label_body(control_id))

    def list_frameworks(self) -> list[FrameworkView]:
        rows = self._conn.execute(
            "SELECT id, name, version, tier FROM all_framework ORDER BY id"
        ).fetchall()
        return [FrameworkView(**dict(r)) for r in rows]

    def list_controls(
        self, framework_id: str, *, active_only: bool = True, leaf_only: bool = False
    ) -> list[ControlView]:
        clauses = ["framework_id = ?"]
        params: list[object] = [framework_id]
        if active_only:
            clauses.append("status <> 'deprecated'")
        if leaf_only:
            clauses.append("id NOT IN (SELECT parent_id FROM all_control "
                           "WHERE parent_id IS NOT NULL)")
        rows = self._conn.execute(
            "SELECT id, framework_id, label, status FROM all_control "
            f"WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        ).fetchall()
        return [ControlView(**dict(r)) for r in rows]

    def framework_progress(self) -> dict[str, tuple[int, int]]:
        """Per framework: (leaf control count, leaves with interpretations).

        The frameworks page wants aggregates - never fetch every control and query interpretations one by one.
        """
        rows = self._conn.execute(
            "SELECT c.framework_id, COUNT(DISTINCT c.id) AS controls, "
            "       COUNT(DISTINCT i.control_id) AS interpreted "
            "FROM all_control c "
            "LEFT JOIN all_interpretation i ON i.control_id = c.id "
            "WHERE c.status <> 'deprecated' "
            "  AND c.id NOT IN (SELECT parent_id FROM all_control "
            "                   WHERE parent_id IS NOT NULL) "
            "GROUP BY c.framework_id"
        ).fetchall()
        return {
            r["framework_id"]: (r["controls"], r["interpreted"])
            for r in rows
        }

    def control_summaries(self, framework_id: str) -> list[ControlSummary]:
        """Fetch everything the framework detail page needs in one pass, instead of two more queries per control."""
        rows = self._conn.execute(
            "SELECT c.id, c.framework_id, c.label, c.status, "
            "       COUNT(i.control_id) > 0 AS has_interpretation, "
            "       MAX(i.state) AS interpretation_state "
            "FROM all_control c "
            "LEFT JOIN all_interpretation i ON i.control_id = c.id "
            "WHERE c.framework_id = ? AND c.status <> 'deprecated' "
            "  AND c.id NOT IN (SELECT parent_id FROM all_control "
            "                   WHERE parent_id IS NOT NULL) "
            "GROUP BY c.id, c.framework_id, c.label, c.status "
            "ORDER BY c.id",
            (framework_id,),
        ).fetchall()
        return [ControlSummary(**dict(r)) for r in rows]

    def list_interpreted(self, *, leaf_only: bool = True) -> list[ControlView]:
        """Controls with interpretations. The home page's daily three draw from here - studying a shell with no interpretation teaches nothing."""
        clauses = ["id IN (SELECT DISTINCT control_id FROM all_interpretation)"]
        if leaf_only:
            clauses.append(
                "id NOT IN (SELECT parent_id FROM all_control "
                "WHERE parent_id IS NOT NULL)")
        rows = self._conn.execute(
            "SELECT id, framework_id, label, status FROM all_control "
            f"WHERE {' AND '.join(clauses)} ORDER BY id",
        ).fetchall()
        return [ControlView(**dict(r)) for r in rows]

    def pending_review(self) -> list[ControlView]:
        """Controls whose state is not confirmed - AI drafts, inheritance products, old signatures disturbed by edits.

        The review queue reads here. Judgement uses the merged view's state: when a user-layer draft
        overlays a confirmed pack row, what you see is the draft - so what you review is the draft.
        """
        rows = self._conn.execute(
            "SELECT id, framework_id, label, status FROM all_control "
            "WHERE id IN (SELECT DISTINCT control_id FROM all_interpretation "
            "             WHERE state <> 'confirmed') ORDER BY id",
        ).fetchall()
        return [ControlView(**dict(r)) for r in rows]

    def search(self, keyword: str, limit: int = 20) -> list[ControlView]:
        """Search control numbers, titles, and interpretation bodies.

        Titles alone barely match Chinese - CSF and 800-53 titles are English, while the real
        entry is a phrase like "which control covers log retention", which only exists in the bodies.
        Control numbers are the other entry: people remember DE.CM-01, not the framework prefix in front of it.
        """
        needle = keyword.strip()
        if not needle:
            return []
        like = f"%{needle}%"
        rows = self._conn.execute(
            "SELECT id, framework_id, label, status FROM all_control "
            "WHERE id LIKE ? OR label LIKE ? OR id IN ("
            "  SELECT control_id FROM all_interpretation WHERE value_json LIKE ?"
            ") ORDER BY id LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        return [ControlView(**dict(r)) for r in rows]

    def stats(self) -> dict[str, int]:
        def one(sql: str) -> int:
            return self._conn.execute(sql).fetchone()[0]

        placeholders = ",".join("?" for _ in _EXPORTABLE)
        exportable = self._conn.execute(
            f"SELECT COUNT(*) FROM mapping WHERE level IN ({placeholders})", _EXPORTABLE
        ).fetchone()[0]
        return {
            "frameworks": one("SELECT COUNT(*) FROM all_framework"),
            "controls": one("SELECT COUNT(*) FROM all_control"),
            "mappings": one("SELECT COUNT(*) FROM mapping"),
            "exportable_mappings": exportable,
        }

    def interpretation(self, control_id: str, locale: str = "zh-CN") -> dict[str, dict]:
        """Read one control's interpretation. Callers write no SQL. Main spec §8①

        `locale` is a preference, not a hard filter: each control's rows live in
        exactly one language, but the label is not trustworthy across tiers — the
        signed 800-53 rows keep their zh-CN label (the signature digest covers
        locale, so relabelling would break every signature) while the translated
        CSF/ISO rows are labelled en. Ask for the preferred locale; if the control
        has no rows there, serve the language it actually has.
        """
        import json

        rows = self._conn.execute(
            "SELECT field, value_json, basis FROM all_interpretation "
            "WHERE control_id = ? AND locale = ? ORDER BY field",
            (control_id, locale),
        ).fetchall()
        if not rows:
            rows = self._conn.execute(
                "SELECT field, value_json, basis FROM all_interpretation "
                "WHERE control_id = ? ORDER BY field",
                (control_id,),
            ).fetchall()
        return {
            r["field"]: {"value": json.loads(r["value_json"]), "basis": r["basis"]}
            for r in rows
        }

    def forbidden_outbound_texts(self) -> list[str]:
        """Content-pack original text that must never enter a model payload.

        Callers receive only business-level data, never a raw connection to write SQL against.
        """
        return [
            r["body"] for r in self._conn.execute(
                "SELECT body FROM original_text"
            ).fetchall()
        ]

    def interpretation_state(
        self, control_id: str, locale: str = "zh-CN"
    ) -> str | None:
        """This interpretation's state: `draft` means an AI first draft, unconfirmed. Main spec §7.3.1

        After the self-use downgrade drafts ship in the pack; the state must stay readable - otherwise readers take drafts as final.
        Locale is a preference here too — see interpretation().
        """
        row = self._conn.execute(
            "SELECT state FROM all_interpretation "
            "WHERE control_id = ? AND locale = ? LIMIT 1",
            (control_id, locale),
        ).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT state FROM all_interpretation "
                "WHERE control_id = ? LIMIT 1",
                (control_id,),
            ).fetchone()
        return row["state"] if row else None
