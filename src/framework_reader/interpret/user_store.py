"""用户导入框架的解读存储。主 spec §7.3.5

`InterpretationStore` 把解读写进 `content/interpretations/`——那是**我们要发布
的内容**：进 git、要评审、被 `make build` 烘进内容包。用户自己公司的制度解读
一个字都不属于那里，写进去等于把别人的内部文件收编进我们的产品仓库。

所以用户层另起一处：落 `user.sqlite`，和自评做邻居。接口刻意与
`InterpretationStore` 一致（exists / save / load），`draft_all` 换一个 store
就能起草导入的框架，不必知道两者的区别。
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
        """整条替换：字段行先删后插，避免上一轮多出来的字段留成孤儿。"""
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
        """与 InterpretationStore 对等：按框架遍历的命令要能一视同仁。"""
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
    """按框架的授权分层选解读存储。

    U 层是用户自己导入的东西，只能落用户库；其余是我们自己的内容，落
    `content/interpretations/`。调用方（CLI、Web）一律走这里选，不要自己
    `InterpretationStore()`——那正是导入的框架起草完却查不到的成因。

    ``overlay=True``：网页上一键起草内置框架（800-53 等）也落用户库，
    当工作副本盖在内容包上面，不进 git。
    """
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.schema.entities import LicenseTier

    if overlay or (view is not None and view.tier == LicenseTier.U_USER):
        return UserInterpretationStore(user_db)
    return InterpretationStore()
