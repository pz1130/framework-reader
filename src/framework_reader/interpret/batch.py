"""批量起草。W2 spec §2.1：起草与访谈分离，访谈期不等模型。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from framework_reader.interpret.drafter import draft_fields, draft_full_fields
from framework_reader.interpret.golden import few_shot_examples
from framework_reader.interpret.model import (
    Basis,
    DIFFERENTIATING_FIELDS,
    Field,
    Interpretation,
    InterpretationProvenance,
    ModelRef,
)
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import LLMClient
from framework_reader.query.api import QueryAPI


class DraftFailure(BaseModel):
    control_id: str
    reason: str


class DraftReport(BaseModel):
    """106 条里有一条模型抽风，不能把另外 105 条一起拖下水。"""

    written: list[str] = []
    failed: list[DraftFailure] = []


def _is_empty(value) -> bool:
    return value in (None, "", [], {})


def _keep_human_content(
    store: InterpretationStore, control_id: str, fresh: dict[str, Field],
    blanks_only: bool = False,
) -> tuple[dict[str, Field], "InterviewRecord"]:
    """重跑起草只覆盖 AI 写的部分。作者的原话与他改过的字段一律保留。

    闸的方向是对的（W2 spec §6：作者说过的话不能丢），但粒度应当在字段上，
    不是整个操作罢工——否则 106 条里只要有一条有 raw，就没法迭代提示词。

    `blanks_only`：只补空格。凡是已经有字的字段一概不动，不管是谁写的——
    用户点「补空缺」的意思就是「别碰我看过的那些」，包括他看过并认可的 AI 初稿。
    """
    from framework_reader.interpret.model import InterviewRecord

    if not store.exists(control_id):
        return fresh, InterviewRecord()
    previous = store.load(control_id)
    merged = dict(fresh)
    for name, field in previous.fields.items():
        if blanks_only:
            if not _is_empty(field.value):
                merged[name] = field
        elif field.basis is Basis.PRACTITIONER and field.value is not None:
            merged[name] = field          # 作者亲手写/改过的，不动
    return merged, previous.interview


def own_examples(store, framework_id: str, exclude: str, limit: int = 3) -> list:
    """拿**本组织已确认的条款**当范例。

    这是「用户帮 AI 一起解读」的实处：他确认过的那几条就是他公司的口径与颗粒度，
    模型学它比学 CSF 的黄金样例更贴——后者教的是通用的具体程度，前者教的是
    「我们这儿把这种事叫什么、写到多细」。

    只取**确认过的**。未确认的可能本来就是模型写的，拿它当范例是让模型学自己。
    """
    from framework_reader.interpret.model import InterpretationState

    out = []
    for interp in store.by_state(InterpretationState.CONFIRMED):
        if interp.control_id == exclude:
            continue
        if not interp.control_id.startswith(f"{framework_id}:"):
            continue
        if all(_is_empty(f.value) for f in interp.fields.values()):
            continue
        out.append(interp)
        if len(out) >= limit:
            break
    return out


def _has_blank(store, control_id: str) -> bool:
    if not store.exists(control_id):
        return True
    return any(_is_empty(f.value) for f in store.load(control_id).fields.values())


def _examples_for(store, framework_id: str, control_id: str, *, own: bool) -> list:
    """本组织已确认的条款优先，不够三条用手写黄金样例补齐。

    黄金样例教的是通用颗粒度，本组织的范例教的是本组织的口径。两者不冲突，
    但用户自己那几条更贴，所以排在前面。
    """
    mine = own_examples(store, framework_id, control_id) if own else []
    if len(mine) >= 3:
        return mine
    return mine + few_shot_examples(exclude=control_id)[: 3 - len(mine)]


def _our_practice_for(documents, api, control) -> list[str]:
    """本组织制度里跟这条控制相关的几段。没上传文档就是空的。

    检索的问题用「标题 + 条款正文」拼——不是控制编号：编号在谁家的制度里
    都不会出现，拿它去检索必然一无所获。
    """
    if documents is None:
        return []
    query = " ".join(filter(None, [control.label, api.control_body(control.id) or ""]))
    return documents.excerpts(query)


def _empty_differentiating() -> dict[str, Field]:
    return {n: Field(value=None, basis=Basis.PRACTITIONER) for n in DIFFERENTIATING_FIELDS}


def draft_all(
    store: InterpretationStore,
    api: QueryAPI,
    client: LLMClient,
    *,
    framework_id: str,
    model: str,
    prompt_version: str,
    provider: str,
    jobs: int = 4,
    force: bool = False,
    only: list[str] | None = None,
    full: bool = False,
    failure_dir: Path | None = None,
    fill_blanks: bool = False,
    documents=None,
) -> "DraftReport":
    """only 非空时只起草指定的几条——厂商还没定之前，不必为 106 条付账。

    `fill_blanks`：只补空格。目标从「没有解读的条款」放宽到「还有空字段的条款」，
    落盘时凡是已经有字的字段一概不动。用户自己写了两句、其余想让 AI 补上，
    走的就是这条——否则那条控制因为「已存在解读」被整条跳过，六个空字段永远空着。

    `documents`：用户上传的配套文档（`DocumentStore`）。给了的话，每条控制会带上
    本组织自己制度里最相关的几段（设计 §8 S5）——不给的话起草出来的是通用建议。
    """
    from framework_reader.interpret.grounding import catalog_prose, grounding_lines
    from framework_reader.schema.entities import LicenseTier

    leaves = list(api.list_controls(framework_id, active_only=True, leaf_only=True))
    view = api.get_framework(framework_id)
    # Tier A 的原文是公共领域，可以直接喂给模型；其余框架的原文既不在库里，
    # 也永远不许出网——那时 label 是我们自写的短标题，不是原文。主 spec §4.1、§9
    embeddable = view is not None and view.tier == LicenseTier.A_EMBEDDABLE
    # 用户自己导入的框架：正文是他自己公司的文档，用他自己的 key 起草，
    # 与 Tier C/D 的受版权标准原文完全两回事。主 spec §7.3.5
    own = view is not None and view.tier == LicenseTier.U_USER
    prose = {} if embeddable else catalog_prose()
    if only:
        missing = sorted(set(only) - {c.id for c in leaves})
        if missing:
            raise ValueError(f"These controls are not active leaves of {framework_id}: {missing}")
        leaves = [c for c in leaves if c.id in set(only)]
    if fill_blanks:
        targets = [c for c in leaves if _has_blank(store, c.id)]
    else:
        targets = [c for c in leaves if force or not store.exists(c.id)]

    def one(control) -> str:
        # Per-call connection: sqlite3 default check_same_thread forbids sharing.
        worker_api = QueryAPI(api._db_path)
        try:
            neighbors = [
                n.control_id for n in worker_api.neighbors(control.id, exportable_only=True)
                if n.control_id.startswith("NIST-800-53-R5:")
            ]
            if full:
                # B 路线：七个字段一次写全，一律 inferred。主 spec §5
                fields = draft_full_fields(
                    client, control_id=control.id,
                    outcome=(
                        control.label if embeddable
                        else worker_api.control_body(control.id) if own
                        else ""
                    ),
                    label="" if embeddable else control.label,
                    grounding=(
                        [] if embeddable
                        else grounding_lines(worker_api, control.id, prose)
                    ),
                    practice=_our_practice_for(documents, worker_api, control),
                    neighbors=neighbors, model=model,
                    # 目标控制自己的手写版必须排除——那是抄答案，不是学颗粒度
                    examples=_examples_for(
                        store, framework_id, control.id, own=own
                    ),
                    **({"failure_dir": failure_dir} if failure_dir is not None else {}),
                )
            else:
                fields = draft_fields(
                    client, control_id=control.id, outcome=control.label,
                    neighbors=neighbors, model=model,
                )
                fields.update(_empty_differentiating())
            fields, interview = _keep_human_content(
                store, control.id, fields, blanks_only=fill_blanks
            )
            store.save(Interpretation(
                control_id=control.id,
                fields=fields,
                interview=interview,
                provenance=InterpretationProvenance(
                    drafter=ModelRef(
                        provider=provider, model=model, prompt_version=prompt_version
                    )
                ),
            ))
            return control.id
        finally:
            worker_api._conn.close()

    def guarded(control) -> tuple[str, str | None]:
        """单条失败只记账，不掀桌子。失败的条目不落盘，重跑（不加 --force）即补。"""
        try:
            return one(control), None
        except Exception as exc:
            return control.id, f"{type(exc).__name__}: {exc}"

    if jobs <= 1:
        results = [guarded(c) for c in targets]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(guarded, targets))

    report = DraftReport()
    for control_id, error in sorted(results):
        if error is None:
            report.written.append(control_id)
        else:
            report.failed.append(DraftFailure(control_id=control_id, reason=error))
    return report
