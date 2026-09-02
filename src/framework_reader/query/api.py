"""QueryAPI——W1 唯一会活到最后的代码。spec §8①

任何调用方（CLI、将来的 Web 后端）都不得直接写 SQL。
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
    """同框架内的一条取代关系，两端带着标题与解读成色。

    换版对照页一次要看完「谁能继承谁」，单端接口（superseded_by / supersedes）
    不够用：它不知道对面有没有解读，而继承动作恰恰只对「旧有、新无」开放。
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
    """内容层只读、用户层可写，存储分离；**查询层把两边合起来看**。

    用户导入的框架进的是用户库（主 spec §6.1），但浏览、自评、差距报告都必须
    能看见它——否则导入进来的东西在产品里根本不存在。做法是 ATTACH 用户库，
    再建两个联合视图；本类此后一律查视图，不查原表。内容包仍以只读方式打开。
    """

    def __init__(self, db_path: Path, user_db: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        # 只读，所以不改 journal_mode；但要等锁——待会儿 ATTACH 进来的用户库
        # 是有人在写的。
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
                # 老的用户库可能还没有这两张表——探一下再决定要不要联合。
                self._conn.execute("SELECT 1 FROM userdb.user_framework LIMIT 1")
                joined = True
            except sqlite3.Error:
                joined = False
        # 落成实例属性：正文取值链（control_body）这类后查的方法也要知道
        # 用户层在不在——不在就没必要去碰 userdb.* 的表。
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
                # 导入的框架起草出来的解读也在用户库。少了这一段，`fr draft`
                # 跑完一切正常，页面上却永远是「这条还没有解读」。
                # **用户改过的字段盖住内容包的——逐字段盖。**
                #
                # 原来这里是 UNION ALL：同一个字段两边都有时两行都回来，
                # 而 `interpretation()` 用 {field: ...} 收字典，留下哪一行
                # 取决于 SQL 的返回顺序——没有定义。内置框架一旦能在网页上改，
                # 这就是个必然会踩的坑。
                #
                # 逐字段而不是整条：改了「怎么落地」不该把内容包里那六个
                # 字段一起顶掉。
                "CREATE TEMP VIEW all_interpretation AS "
                "  SELECT f.control_id, f.locale, f.field, f.value_json, "
                "    f.basis, m.state FROM userdb.user_interpretation f "
                "    JOIN userdb.user_interpretation_meta m "
                "      ON m.control_id = f.control_id AND m.locale = f.locale "
                # 空值的用户行一概不返回。`write_field` 一次写七个字段，
                # 没碰的那六个是 null；留着它们，同一个字段会回两行，
                # 而字典只留一行——留下哪一行看运气。
                # 界面上「字段缺失」和「值为空」渲染完全一样，丢掉无损。
                "   WHERE f.value_json <> 'null' "
                "  UNION ALL "
                "  SELECT i.control_id, i.locale, i.field, i.value_json, "
                "    i.basis, i.state FROM main.interpretation i "
                "   WHERE NOT EXISTS ("
                "     SELECT 1 FROM userdb.user_interpretation u "
                "      WHERE u.control_id = i.control_id "
                "        AND u.locale = i.locale AND u.field = i.field "
                # **有行不等于写过。** `write_field` 一次写七个字段，
                # 没碰的那六个是 null——拿「有行」当判据，改一个字段会把
                # 内容包里其余六个全顶成空的。
                #
                # 代价：把内置框架的某个字段清空之后，内容包那一版会回来。
                # 那是可接受的一面——「清空」在内置条款上正好读作「恢复默认」。
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
        """框架的展示名。渲染映射时要用，调用方不得自己写 SQL。"""
        row = self._conn.execute(
            "SELECT id, name, version, tier FROM all_framework WHERE id = ?",
            (framework_id,),
        ).fetchone()
        return FrameworkView(**dict(row)) if row else None

    def neighbors(self, control_id: str, exportable_only: bool = False) -> list[NeighborView]:
        # 全部用位置参数 ?，按出现顺序绑定；不要混用 ?1 编号风格。
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
        """这条旧编号现在是哪几条。手上拿着 CSF 1.1 材料的人问的就是这个。"""
        return self._supersession_rows(
            "SELECT s.new_id AS control_id, c.label, c.status, s.relation "
            "FROM control_supersession s JOIN all_control c ON c.id = s.new_id "
            "WHERE s.old_id = ? ORDER BY s.new_id",
            control_id,
        )

    def supersedes(self, control_id: str) -> list[SupersessionView]:
        """哪几条旧编号落到了这条上。"""
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
        """这个框架里全部的取代关系，整条边回来。换版对照页的数据源。

        跨框架的边不算数：继承只在同一框架的换版里成立，旧条款的解读
        流到另一家框架的条款上就是张冠李戴。解读状态取自联合视图——
        旧端有解读，这条边才有继承可谈。
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

    # 「label 即正文」的框架：CSF 2.0 的 subcategory 没有别的正文——那句话
    # 既是它的标题也是它的全部内容，官方 label 就是条款正文。反例：
    # 800-53 的 label 是控制标题（正文是另一段 should 句，未进库）；
    # ISO 的 label 是自写中文短标题。谁满足「label = 条款全部文本」
    # 谁进这个集合——新框架进库时在这里维护。
    LABEL_AS_BODY = frozenset({"NIST-CSF-2.0"})

    def _user_body(self, control_id: str) -> str | None:
        """用户层里的正文（覆盖层优先，导入行在后）。
        用户层不在、表还没建（老库）都当 None——不能连累下面的官方路。"""
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
        """内容库的官方 label 兑现的正文。只认「label 即正文」的框架。"""
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
        """这条控制的正文，取值链：用户覆盖层/导入正文 > 官方 label。

        内置条款看覆盖层（control_body_override，用户贴进来的），
        导入条款看 user_control；都没有就退到内容库的官方 label——
        但只认「label 即正文」的框架（LABEL_AS_BODY）：拿 800-53 的
        控制标题、ISO 的自写短标题充正文都是误导。用户层不在也不影响
        这条路——CLI 只拿内容库时，CSF 的正文照样要能读出来。
        内容包里的 `original_text` 永远是空的（主 spec §3.2②）。
        """
        user = self._user_body(control_id)
        if user is not None and user.strip():
            return user
        return self._official_label_body(control_id)

    def body_is_official(self, control_id: str) -> bool:
        """条款页正在展示的正文是不是官方的（label 兑现的）——
        决定那块该标「官方原文」还是「你导入的原文」。"""
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
        """每个框架的（叶子条款数，有解读的叶子条款数）。

        目录页要的是聚合数，不该先取出所有条款再逐条查解读。
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
        """框架详情页所需的数据一次取齐，避免每条控制再查两次。"""
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
        """有解读的条款。首页每天三条从这里抽，没解读的学了也是空壳。"""
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
        """成色不是 confirmed 的条款——AI 初稿、继承产物、被改动过的旧签字。

        审阅队列读这里。判定看的是合并视图里的 state：用户库的草稿盖在
        内容包的定稿上面时，看到的是草稿的成色，该审的就是草稿。
        """
        rows = self._conn.execute(
            "SELECT id, framework_id, label, status FROM all_control "
            "WHERE id IN (SELECT DISTINCT control_id FROM all_interpretation "
            "             WHERE state <> 'confirmed') ORDER BY id",
        ).fetchall()
        return [ControlView(**dict(r)) for r in rows]

    def search(self, keyword: str, limit: int = 20) -> list[ControlView]:
        """在编号、标题和解读正文里找。

        只搜标题的话中文基本搜不到——CSF 与 800-53 的标题是英文，
        而真实的入口是「日志留存这事在哪条」这种中文说法，它只出现在解读里。
        条号是另一种入口：人记得住 DE.CM-01，记不住前面那段框架前缀。
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
        """读一条控制的解读。调用方不得直接写 SQL。主 spec §8①"""
        import json

        rows = self._conn.execute(
            "SELECT field, value_json, basis FROM all_interpretation "
            "WHERE control_id = ? AND locale = ? ORDER BY field",
            (control_id, locale),
        ).fetchall()
        return {
            r["field"]: {"value": json.loads(r["value_json"]), "basis": r["basis"]}
            for r in rows
        }

    def forbidden_outbound_texts(self) -> list[str]:
        """不得进入模型 payload 的内容包原文。

        调用方只拿业务含义明确的数据，不拿底层连接去写裸 SQL。
        """
        return [
            r["body"] for r in self._conn.execute(
                "SELECT body FROM original_text"
            ).fetchall()
        ]

    def interpretation_state(
        self, control_id: str, locale: str = "zh-CN"
    ) -> str | None:
        """这条解读的成色：`draft` 是 AI 初稿、未经作者确认。主 spec §7.3.1

        自用降级后草稿也进包，成色必须能读出来——否则读的人会把初稿当定稿。
        """
        row = self._conn.execute(
            "SELECT state FROM all_interpretation "
            "WHERE control_id = ? AND locale = ? LIMIT 1",
            (control_id, locale),
        ).fetchone()
        return row["state"] if row else None
