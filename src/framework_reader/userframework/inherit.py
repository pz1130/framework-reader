"""换版继承：把旧条款上写好的解读复制到新条款名下。

三个不跟随（换版继承计划 Global Constraints）：
- **签字不跟随**——签字是对着旧条款的原文与字段签的，换条款就得重签，
  继承产物一律 `draft`，构建闸门因此天然不受影响；
- **访谈原文不跟随**——RawAnswer 是作者对着旧条款说的话，带过去就是
  新条款页上的无关问答；字段的 `value` 是抽出来的字面文本，可以带；
- **旧条款不跟随**——复制不是搬移，旧解读原样保留，无损、可重复继承。

校验与落库分成两个函数：路由层先 `check()` 拿到拒绝原因，`inherit()`
只在通过后动手。模型不参与本流程——继承是纯代码复制。
"""
from framework_reader.interpret.model import (
    Interpretation,
    InterpretationProvenance,
    InterpretationState,
    InterviewRecord,
)
from framework_reader.query.api import QueryAPI


class InheritDenied(Exception):
    """继承被拒绝。message 是给用户看的中文，直接渲到页面上。"""


def check(old_id: str, new_id: str, store, api: QueryAPI) -> None:
    """三条硬校验：边存在、旧端有解读、新端没有。"""
    successors = {v.control_id for v in api.superseded_by(old_id)}
    if new_id not in successors:
        raise InheritDenied(
            f"{old_id} and {new_id} have no supersession relation; cannot inherit"
        )
    if not store.exists(old_id):
        raise InheritDenied(f"{old_id} has no interpretation; nothing to inherit")
    if store.exists(new_id):
        raise InheritDenied(f"{new_id} already has an interpretation; inheriting would overwrite it - not supported yet")


def inherit(old_id: str, new_id: str, store, api: QueryAPI) -> Interpretation:
    """复制并落库，返回落在新条款名下的那条。"""
    check(old_id, new_id, store, api)
    old = store.load(old_id)
    interp = Interpretation(
        control_id=new_id,
        locale=old.locale,
        state=InterpretationState.DRAFT,
        fields={name: field.model_copy(deep=True)
                for name, field in old.fields.items()},
        interview=InterviewRecord(),
        provenance=InterpretationProvenance(
            drafter=old.provenance.drafter,
            extractor=old.provenance.extractor,
            inherited_from=old_id,
        ),
    )
    store.save(interp)
    return interp
