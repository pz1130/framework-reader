"""Structural validation and build assertions. spec §4.2⑤, §10.A"""
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from framework_reader.schema.sources import DisallowedSourceError, SourceRegistry

if TYPE_CHECKING:
    from framework_reader.interpret.model import Interpretation


class BuildAssertionError(Exception):
    """A build-time invariant was violated; the build must fail."""


class ValidationIssue(BaseModel):
    kind: str
    detail: str


def validate_graph(conn: sqlite3.Connection) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    dangling_endpoints = conn.execute(
        """
        SELECT m.from_id, m.to_id FROM mapping m
        WHERE m.from_id NOT IN (SELECT id FROM framework_control)
           OR m.to_id   NOT IN (SELECT id FROM framework_control)
        """
    ).fetchall()
    for from_id, to_id in dangling_endpoints:
        issues.append(ValidationIssue(
            kind="dangling_mapping_endpoint", detail=f"{from_id} -> {to_id}"
        ))

    dangling_parents = conn.execute(
        """
        SELECT id, parent_id FROM framework_control
        WHERE parent_id IS NOT NULL
          AND parent_id NOT IN (SELECT id FROM framework_control)
        """
    ).fetchall()
    for cid, pid in dangling_parents:
        issues.append(ValidationIssue(kind="dangling_parent", detail=f"{cid} -> {pid}"))

    dangling_supersessions = conn.execute(
        """
        SELECT old_id, new_id FROM control_supersession
        WHERE old_id NOT IN (SELECT id FROM framework_control)
           OR new_id NOT IN (SELECT id FROM framework_control)
        """
    ).fetchall()
    for old_id, new_id in dangling_supersessions:
        issues.append(ValidationIssue(
            kind="dangling_supersession_endpoint", detail=f"{old_id} -> {new_id}"
        ))

    # Deprecated entries should not have mapping edges in the first place, so they do not
    # count as orphans — otherwise the genuinely missed controls would be drowned out.
    orphans = conn.execute(
        """
        SELECT id FROM framework_control
        WHERE id NOT IN (SELECT from_id FROM mapping)
          AND id NOT IN (SELECT to_id FROM mapping)
          AND parent_id IS NOT NULL
          AND status <> 'deprecated'
        """
    ).fetchall()
    for (cid,) in orphans:
        issues.append(ValidationIssue(kind="orphan_control", detail=cid))

    return issues


def assert_build_invariants(
    conn: sqlite3.Connection,
    registry: SourceRegistry,
    baseline_path: Path | None = None,
) -> None:
    # ① The original_text table must be empty. spec §3.2②, §4.2⑤
    (count,) = conn.execute("SELECT COUNT(*) FROM original_text").fetchone()
    if count:
        raise BuildAssertionError(
            f"original_text table has {count} rows - it must be empty in a build artifact."
            f" Copyrighted original text may only be injected locally by the user."
        )

    # ② Every mapping source must be on the allowlist. spec §4.3, §10.A
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM mapping").fetchall()]
    for src in sources:
        try:
            registry.assert_allowed(src)
        except DisallowedSourceError as exc:
            raise BuildAssertionError(str(exc)) from exc

    # ③ control_id stability. spec §8②
    # R13: only checked when baseline_path is passed; skipped otherwise (comparing a
    # fixture database against the full baseline would false-positive).
    if baseline_path is None:
        return

    from framework_reader.pack.id_baseline import check_baseline

    missing = check_baseline(conn, baseline_path)
    if missing:
        raise BuildAssertionError(
            f"{len(missing)} published control_ids are missing: {missing[:5]}...\n"
            f"IDs are never reused and never change meaning. A control deleted by the framework should be marked deprecated and keep its row; "
            f"a semantic change should get a new ID linked back via supersedes."
        )


def assert_only_confirmed(items: list["Interpretation"]) -> None:
    """Every interpretation entering the pack must have been signed off by a human. Main spec §5, W2 spec §4.3

    The caller only passes in confirmed ones (during W3 many entries are still draft, and
    the build should not fail over that), so this checks both the state and the signer —
    the former guards against the caller skipping the filter, the latter is the real gate.
    """
    from framework_reader.interpret.model import InterpretationState

    bad = [i for i in items if i.state is not InterpretationState.CONFIRMED]
    if bad:
        raise BuildAssertionError(
            f"{len(bad)} interpretations unsigned (state={bad[0].state.value}), "
            f"first: {bad[0].control_id}"
        )
    unsigned = [i for i in items if not (i.provenance.confirmed_by or "").strip()]
    if unsigned:
        raise BuildAssertionError(f"{unsigned[0].control_id} has empty confirmed_by")


def assert_glossary_clean(items: list["Interpretation"], glossary) -> None:
    """The glossary covers interpretation text, not just the label. Main spec §10.B3"""
    for interp in items:
        for name, field in sorted(interp.fields.items()):
            value = field.value
            if value is None:
                continue
            if isinstance(value, list):
                text = " ".join(str(v) for v in value)
            elif isinstance(value, dict):
                text = " ".join(str(v) for v in value.values())
            else:
                text = str(value)
            hits = glossary.check_text(text)
            if hits:
                raise BuildAssertionError(
                    f"{interp.control_id}: {name} uses banned words: {hits}"
                )


def assert_signature_matches_content(items: list["Interpretation"]) -> None:
    """Anything whose content changed after signing must be signed again. W2 spec §4.3

    The comparison is over the content digest, not the file mtime — git restores files
    stamped with the current time, so using mtime to decide whether content changed would
    false-positive after any clone / checkout.
    """
    from framework_reader.interpret.model import fields_digest

    for interp in items:
        stored = interp.provenance.signed_digest
        if not stored:
            raise BuildAssertionError(
                f"{interp.control_id} has no signed_digest - please sign again"
            )
        actual = fields_digest(interp)
        if actual != stored:
            raise BuildAssertionError(
                f"{interp.control_id} was modified after signing"
                f" (signature digest {stored[:12]}..., current content {actual[:12]}...) - please sign again"
            )
