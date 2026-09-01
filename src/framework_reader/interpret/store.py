"""解读的 YAML 存储。主 spec §9：内容以 YAML 存于 Git，SQLite 是构建产物。"""
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
    """有人要把用户导入的框架写进产品的内容仓。"""


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
        """用户导入的框架不许进 `content/interpretations/`。

        起草那条路已经按 tier 分流（interpret.user_store.store_for），但分流
        是**每个调用点各自记得**才成立的事——b971e12 就是漏了一处。门设在写
        入口本身，下一个写命令的人忘了也撞得回来。

        守的是这一处目录，不是这个类：测试与迁移都要能把用户框架的 YAML
        写到别处去。判据是「这个框架在不在用户库里」，不靠编号长相猜。
        """
        if Path(self.root).resolve() != DEFAULT_ROOT.resolve():
            return
        framework_id = control_id.split(":", 1)[0]
        try:
            from framework_reader.userframework.store import UserFrameworkStore

            mine = {f.id for f in UserFrameworkStore().list_frameworks()}
        except Exception:                                   # noqa: BLE001
            return          # 用户库读不到就不拦——这是第二道门，不是唯一一道
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
        """答完一问立刻落盘。同一问重答则覆盖，不追加。W2 spec §6"""
        interp = self.load(control_id)
        kept = [r for r in interp.interview.raw if r.n != n]
        kept.append(RawAnswer(n=n, text=text))
        interp.interview.raw = sorted(kept, key=lambda r: r.n)
        self.save(interp)
