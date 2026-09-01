"""用户改自己框架的解读，以及签字。主 spec §5、§7.3.5

起草器写的是初稿。产品的价值不在初稿——在于用户的经验落进去之后，这段话
有人认领。一份没人认领的合规文档，准不准都没人敢交出去。

两件事分开记：
  - `basis` 记**谁写的这句**：AI 写的 inferred，人写的 practitioner。逐字段。
  - `state` 记**谁认领这条**：签字落在整条上，且改过之后签名作废——
    W2 spec §4.3 的原话是「签完没被改过才算数」。
"""
from datetime import datetime, timezone

from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
    InterpretationState,
    fields_digest,
)

FieldValue = str | list[str] | dict[str, str] | None


def blank(control_id: str) -> Interpretation:
    """一条谁都没写过的解读。七个字段都空着，且都算人写的——

    没让模型碰过的东西不该挂着 AI 的名。
    """
    return Interpretation(
        control_id=control_id,
        fields={n: Field(value=None, basis=Basis.PRACTITIONER) for n in ALL_FIELDS},
    )


def write_field(
    store, control_id: str, field: str, value: FieldValue,
    basis: Basis = Basis.PRACTITIONER,
) -> Interpretation:
    """写一个字段。其余字段一个都不碰。

    `basis` 记的是**谁写的这句**。用户自己敲的是 practitioner；用户提要求、
    模型执笔重写出来的是 inferred——要求是他提的，字是模型写的，
    记成 practitioner 等于替他认领了他没写过的话。
    """
    if field not in ALL_FIELDS:
        raise ValueError(f"No such field: {field}")
    interp = store.load(control_id) if store.exists(control_id) else blank(control_id)
    fields = dict(interp.fields)
    fields[field] = Field(value=value, basis=basis)

    provenance = interp.provenance.model_copy()
    state = interp.state
    if state is InterpretationState.CONFIRMED:
        # 签完又改，签名就不再覆盖这份内容。留着它比没有更危险。
        # 起草者是谁不动——那是这条的来历，不是签字。
        state = InterpretationState.DRAFT
        provenance.confirmed_by = None
        provenance.confirmed_at = None
        provenance.signed_digest = None

    updated = Interpretation(
        control_id=control_id, locale=interp.locale, state=state,
        fields=fields, interview=interp.interview, provenance=provenance,
    )
    store.save(updated)
    return updated


# Product-level signer for interpretations we ship. Not a model id (those
# are rejected as `ai:` / `model:`), and not a person's name — deployers
# should not see a 199-item review queue of unsigned AI drafts.
PUBLISHER_SIGNER = "publisher"


def confirm(store, control_id: str, *, signer: str) -> Interpretation:
    """认领这条。签的是当时那份内容，摘要一并记下。"""
    interp = store.load(control_id)          # 不存在就抛 FileNotFoundError
    provenance = interp.provenance.model_copy()
    provenance.confirmed_by = signer
    provenance.confirmed_at = datetime.now(timezone.utc)
    signed = Interpretation(
        control_id=interp.control_id, locale=interp.locale,
        state=InterpretationState.CONFIRMED, fields=interp.fields,
        interview=interp.interview, provenance=provenance,
    )
    # 摘要不含 provenance，所以能签完再补写进去。见 model.fields_digest
    signed.provenance.signed_digest = fields_digest(signed)
    store.save(signed)
    return signed
