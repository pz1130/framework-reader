"""结构校验与构建断言。spec §4.2⑤、§10.A"""
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from framework_reader.schema.sources import DisallowedSourceError, SourceRegistry

if TYPE_CHECKING:
    from framework_reader.interpret.model import Interpretation


class BuildAssertionError(Exception):
    """违反构建期不变式，构建必须失败。"""


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

    # 废止条目本来就不该有映射边，不计入 orphan——否则真正的漏网控制会被淹没。
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
    # ① 原文表必须为空。spec §3.2②、§4.2⑤
    (count,) = conn.execute("SELECT COUNT(*) FROM original_text").fetchone()
    if count:
        raise BuildAssertionError(
            f"original_text table has {count} rows - it must be empty in a build artifact."
            f" Copyrighted original text may only be injected locally by the user."
        )

    # ② 所有映射来源必须在白名单内。spec §4.3、§10.A
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM mapping").fetchall()]
    for src in sources:
        try:
            registry.assert_allowed(src)
        except DisallowedSourceError as exc:
            raise BuildAssertionError(str(exc)) from exc

    # ③ control_id 稳定性。spec §8②
    # R13：仅当传入 baseline_path 时检查；省略则跳过（夹具库对照全量基线会误报）。
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
    """进包的每一条都必须由人签过字。主 spec §5、W2 spec §4.3

    调用方只把 confirmed 的传进来（W3 期间大量条目还是 draft，构建不该因此失败），
    所以这里既检查 state 也检查签字人——前者防调用方漏筛，后者才是真正的闸。
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
    """术语表覆盖解读文本，不只覆盖 label。主 spec §10.B3"""
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
    """签完字又改了内容的，必须重新签。W2 spec §4.3

    比对的是内容摘要，不是文件 mtime——git 恢复文件时打的是当前时间，
    用 mtime 判断内容有没有变，在任何一次 clone / checkout 之后都会误报。
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
