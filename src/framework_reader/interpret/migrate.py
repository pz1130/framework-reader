"""把落在内容仓里的用户框架解读搬回用户库。主 spec §7.3.5

存储层已经按 tier 分流（`interpret/user_store.store_for`），但**已经写在
`content/interpretations/` 里的**不会自己长脚：那些解读起草得好好的，
查询层却从来不读那儿，等于起草了个寂寞。

判据只有一条：**这个框架现在在用户库里**。它是用户导入的东西，它的解读
就该跟它待在一起。内容仓里其余的目录一概不碰——那是我们要发布的内容。
"""
from pathlib import Path

import yaml
from pydantic import BaseModel

from framework_reader.interpret.model import Interpretation
from framework_reader.interpret.store import DEFAULT_ROOT
from framework_reader.interpret.user_store import UserInterpretationStore


class MigrationReport(BaseModel):
    moved: list[str] = []
    # (control_id 或文件名, 原因)。跳过的必须说得出原因——静默跳过之后
    # 用户会以为全搬完了，而少的那几条要等到出报告时才发现。
    skipped: list[tuple[str, str]] = []
    deleted: list[str] = []


def migrate_user_drafts(
    root: Path = DEFAULT_ROOT, *, force: bool = False, delete: bool = False
) -> MigrationReport:
    """把用户框架的解读 YAML 搬进 `user.sqlite`。

    默认不覆盖用户库里已有的那份（可能是他自己改过的），也不删原件
    （原件被 git 追踪，删不删是另一个决定）。
    """
    from framework_reader.userframework.store import UserFrameworkStore

    report = MigrationReport()
    root = Path(root)
    if not root.exists():
        return report

    frameworks = UserFrameworkStore()
    mine = {f.id for f in frameworks.list_frameworks()}
    store = UserInterpretationStore()

    for framework_id in sorted(mine):
        folder = root / framework_id
        if not folder.is_dir():
            continue
        known = frameworks.control_ids(framework_id)
        for path in sorted(folder.glob("*.yaml")):
            _migrate_one(path, known, store, report, force=force, delete=delete)
    return report


def _migrate_one(
    path: Path,
    known: set[str],
    store: UserInterpretationStore,
    report: MigrationReport,
    *,
    force: bool,
    delete: bool,
) -> None:
    try:
        interp = Interpretation(**yaml.safe_load(path.read_text(encoding="utf-8")))
    except Exception as exc:                          # noqa: BLE001
        # 坏一份不能把另外一百份一起拖下水。
        report.skipped.append((path.name, f"could not read: {type(exc).__name__}: {exc}"))
        return

    if interp.control_id not in known:
        # 重新导入过表格、编号变了。搬进去也没有页面够得着，与其留个孤儿行，
        # 不如摆到报告上让人自己看一眼。
        report.skipped.append((interp.control_id, "control no longer exists in the user library"))
        return

    if store.exists(interp.control_id) and not force:
        report.skipped.append((interp.control_id, "already exists in the user library (use --force to overwrite)"))
        return

    store.save(interp)
    report.moved.append(interp.control_id)
    if delete:
        # 只在确实落地之后才删。顺序反了就是一次数据丢失。
        path.unlink()
        report.deleted.append(str(path))
