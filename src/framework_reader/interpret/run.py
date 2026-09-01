"""组装一次起草。CLI 与 Web 共用同一条路径。

分成两处写的话，两处就会各自漂——比如 CLI 记得按框架分层选存储、Web 忘了，
导入的框架在网页上起草完又一次进不了用户库。装配只此一处。
"""
import re
import sqlite3
from pathlib import Path

from framework_reader.interpret.batch import DraftReport, draft_all
from framework_reader.query.api import QueryAPI


class UnknownFrameworkError(Exception):
    """框架编号在内容包和用户库里都找不到。"""


def documents_for(view, user_db: Path | None):
    """配套文档只给**用户自己导入的**框架用，内置框架一律 None。

    内置框架（CSF / ISO / 800-53）的解读是我们要发布的内容，进 git、要评审、
    会被烘进内容包。里面出现某一家公司的内部制度，既不对，也发不出去。
    """
    from framework_reader.schema.entities import LicenseTier
    from framework_reader.userframework.documents import DocumentStore

    if view is None or view.tier != LicenseTier.U_USER:
        return None
    return DocumentStore(user_db)


def draft_framework(
    db: Path,
    framework_id: str,
    *,
    jobs: int = 4,
    force: bool = False,
    only: list[str] | None = None,
    full: bool = False,
    fill_blanks: bool = False,
    user_db: Path | None = None,
    overlay: bool = False,
) -> DraftReport:
    """起草整个框架。缺 API key 会抛 MissingApiKeyError，由调用方决定怎么说。"""
    from framework_reader.interpret.drafter import DRAFT_FAILURE_DIR
    from framework_reader.interpret.user_store import store_for
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.prompts import PROMPT_VERSIONS, full_drafter_version

    api = QueryAPI(db, user_db=user_db)
    view = api.get_framework(framework_id)
    if view is None:
        raise UnknownFrameworkError(f"No such framework: {framework_id}")

    # 管理员在网页上配的模型与 key 盖在 YAML 预设之上。
    registry, key_lookup = effective_registry()
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()

    # 清掉上一轮的失败样本，否则 build/draft_failures/ 里会混着陈货，误导诊断。
    if DRAFT_FAILURE_DIR.exists():
        for stale in DRAFT_FAILURE_DIR.glob("*.txt"):
            stale.unlink()

    return draft_all(
        store_for(view, user_db, overlay=overlay), api,
        registry.build("drafter", guard=guard, key_lookup=key_lookup),
        documents=documents_for(view, user_db),
        framework_id=framework_id, model=role.model,
        prompt_version=(
            full_drafter_version() if full else PROMPT_VERSIONS["drafter"]
        ),
        provider=role.provider,
        jobs=jobs, force=force, only=only, full=full, fill_blanks=fill_blanks,
    )


def fill_blanks_one(
    db: Path, control_id: str, user_db: Path | None = None,
    overlay: bool = False,
) -> DraftReport:
    """只补这一条的空字段。用户点某一条的「补空缺」走这里，不跑整个框架。"""
    framework_id = control_id.split(":", 1)[0]
    return draft_framework(
        db, framework_id, jobs=1, only=[control_id], full=True, fill_blanks=True,
        user_db=user_db, overlay=overlay,
    )


def rewrite_one(db: Path, control_id: str, field: str, instruction: str,
                user_db: Path | None = None):
    """按用户的一句要求重写一个字段，返回新值（不落盘，落盘由调用方决定）。"""
    from framework_reader.interpret.drafter import rewrite_field
    from framework_reader.interpret.render import FIELD_LABELS
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db

    api = QueryAPI(db, user_db=user_db)
    current = (api.interpretation(control_id).get(field) or {}).get("value")
    # 管理员在网页上配的模型与 key 盖在 YAML 预设之上。
    registry, key_lookup = effective_registry()
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()
    return rewrite_field(
        registry.build("drafter", guard=guard, key_lookup=key_lookup),
        control_id=control_id, field=field, label=dict(FIELD_LABELS).get(field, field),
        current=current, instruction=instruction, model=role.model,
        outcome=api.control_body(control_id),
    )


def rewrite_body(db: Path, control_id: str, instruction: str, current: str,
                 user_db: Path | None = None) -> str:
    """按用户要求改**他自己导入的**一条正文。只出提议稿，不落盘——
    写库永远是用户点「保存」那一步的事（和字段重写同一道闸）。

    只接用户框架：内置框架的正文是官方文本，这条路由根本不该被调到。
    """
    from framework_reader.llm.client import Message
    from framework_reader.llm.config import effective_registry
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.prompts import load_prompt

    registry, key_lookup = effective_registry()
    role = registry.role("drafter")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()
    client = registry.build("drafter", guard=guard, key_lookup=key_lookup)

    user = (f"Control: {control_id}\n\n"
            f"The user's instruction: {instruction.strip()}\n\n"
            f"Current body:\n{current}")
    raw = client.complete(
        load_prompt("body_rewrite"), [Message(role="user", content=user)],
        model=role.model)
    # 提示词说了只输出正文，但模型偶尔还是裹围栏——剥掉。剥完为空
    # 就当它没改，原样退回，别拿一个空字符串把用户的正文清了。
    text = re.sub(r"^\s*```[a-z]*\s*|\s*```\s*$", "", (raw or "").strip())
    return text if text else current


def pending_controls(
    db: Path, framework_id: str, user_db: Path | None = None
) -> list[str]:
    """这个框架里还没有解读的叶子控制。起草前要先告诉用户这一趟要花多少钱。"""
    api = QueryAPI(db, user_db=user_db)
    return [
        c.id for c in api.list_controls(framework_id, active_only=True, leaf_only=True)
        if not api.interpretation(c.id)
    ]
