import sqlite3
from pathlib import Path

SCHEMA = Path("src/framework_reader/pack/user_schema.sql")


def test_user_schema_is_valid_sql():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert tables == {
        "user_annotation", "user_document", "original_text",
        "confirmation", "answer_history", "orphaned_reference",
        "assessment", "user_framework", "user_control",
        # 导入框架的解读也是用户数据，和自评同一层。主 spec §7.3.5
        "user_interpretation", "user_interpretation_meta",
        # 上传的配套文档，切段后按段检索。设计 §8 S5
        "user_document_chunk",
        # 文档导入的预览态：确认前不写框架库，但已经花过的钱要留住。
        # 2026-08-25 AI 导入设计 §3
        "import_draft",
        # 条款页上和 AI 的对话。对话跟着条款走，签字的人要看得到
        # 「这句话当初是怎么来的」。
        "control_chat",
        # 内置条款的正文覆盖层：贴进来的原文在用户库里，官方基准不动。
        "control_body_override",
        # 网页搜索命中。首页「经常搜索」读这里。
        "search_hit",
        # 自评历史：复评对比要的就是「每次记下时是多少」。
        "assessment_history",
        # 整改台账：差距报告之后「谁、什么时候、做了没有」。
        "remediation",
    }
    conn.close()


def test_build_pipeline_never_applies_user_schema():
    """用户层与内容层物理分离——构建代码不得引用这份 schema。spec §6.1"""
    build_src = Path("src/framework_reader/pack/build.py").read_text(encoding="utf-8")
    assert "user_schema" not in build_src
