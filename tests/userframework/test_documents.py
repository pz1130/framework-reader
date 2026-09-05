"""上传的配套文档，及「哪一段跟这条控制有关」。见网页服务化设计 §8 S5

起草器写出来的是**通用**的落地建议。这个团队真正的落地方式写在他们自己的
制度里——「日志留存六个月」还是「一年」，是他们文件里的一行。
"""
import pytest

from framework_reader.userframework.documents import DocumentStore
from framework_reader.userframework.extract import UnsupportedDocument

LOG_POLICY = """第一章 日志管理

本公司各系统的安全日志留存不少于六个月，集中存放在日志平台，
每季度由安全组复核一次覆盖范围。

第二章 访问控制

账号权限按最小必要授予，离职当日冻结，每半年复核一次。
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "home"))
    return DocumentStore()


def _add(store, name="安全管理制度.txt", text=LOG_POLICY, by="ann@acme.cn"):
    return store.add(name, text.encode("utf-8"), by=by)


# ---------- 落库 ----------

def test_an_uploaded_document_shows_up(store):
    _add(store)
    assert [d.filename for d in store.list_documents()] == ["安全管理制度.txt"]


def test_it_remembers_who_uploaded_it(store):
    """上传内部制度是有后果的动作——它会被发给模型。谁传的必须留着。"""
    assert _add(store).uploaded_by == "ann@acme.cn"


def test_it_is_split_into_sections(store):
    assert _add(store).chunks >= 2


def test_the_sections_can_be_read_back(store):
    """「模型到底看到了什么」不能只有我们知道。看不见就没人会信它。"""
    doc = _add(store)
    text = " ".join(body for _, body in store.chunks(doc.id))
    assert "六个月" in text


def test_an_unparsable_file_leaves_nothing_behind(store):
    """解析在落库之前——解析不了的文件一个字节都不留下。"""
    with pytest.raises(UnsupportedDocument):
        store.add("扫描件.pdf", b"%PDF-1.4", by="ann@acme.cn")
    assert store.list_documents() == []


def test_an_empty_document_is_refused(store):
    with pytest.raises(UnsupportedDocument):
        store.add("空.txt", "   \n\n".encode("utf-8"), by="ann@acme.cn")


def test_deleting_takes_the_sections_with_it(store):
    doc = _add(store)
    store.delete(doc.id)
    assert store.list_documents() == []
    assert store.chunks(doc.id) == []


def test_a_deleted_document_stops_grounding(store):
    doc = _add(store)
    store.delete(doc.id)
    assert store.excerpts("日志留存多久") == []


# ---------- 检索 ----------

def test_the_relevant_section_is_found(store):
    _add(store)
    found = store.excerpts("日志留存与复核")
    assert found and "六个月" in found[0]


def test_the_excerpt_says_which_document_it_came_from(store):
    _add(store)
    assert "安全管理制度" in store.excerpts("日志留存与复核")[0]


def test_an_unrelated_question_gets_nothing_rather_than_noise(store):
    """噪声接地比没有接地更糟：模型会照着不相干的段落编出一条不存在的制度。"""
    _add(store)
    assert store.excerpts("物理机房的消防喷淋验收") == []


def test_the_longest_section_does_not_always_win(store):
    """不除以长度的话，最长那段永远赢——它只是碰巧包含更多二元组。"""
    store.add("杂项.txt", ("公司简介与历史沿革。" * 200).encode("utf-8"),
              by="ann@acme.cn")
    _add(store)
    found = store.excerpts("日志留存与复核")
    assert found and "六个月" in found[0]


def test_nothing_uploaded_means_no_grounding(store):
    assert store.excerpts("日志留存") == []


def test_a_one_character_question_is_not_a_query(store):
    _add(store)
    assert store.excerpts("日") == []


def test_a_long_section_is_trimmed_not_dumped_whole(store):
    long_text = "第一章 日志\n\n" + "日志留存不少于六个月。" * 200
    store.add("长制度.txt", long_text.encode("utf-8"), by="ann@acme.cn")
    assert all(len(e) < 900 for e in store.excerpts("日志留存"))


def test_only_a_few_sections_come_back(store):
    for i in range(10):
        store.add(f"制度{i}.txt", LOG_POLICY.encode("utf-8"), by="ann@acme.cn")
    assert len(store.excerpts("日志留存与复核")) <= 4


# ---------- 老库补列 ----------

def test_an_older_user_database_gains_the_new_columns(tmp_path, monkeypatch):
    """建表语句是 IF NOT EXISTS，已有的库不会因为 schema 加了一列就跟着变。"""
    import sqlite3

    monkeypatch.setenv("FRAMEWORK_READER_HOME", str(tmp_path / "old"))
    path = tmp_path / "old" / "user.sqlite"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE user_document (id TEXT PRIMARY KEY, "
                 "filename TEXT NOT NULL, sha256 TEXT NOT NULL, "
                 "uploaded_at TEXT NOT NULL)")
    conn.commit()
    conn.close()

    doc = DocumentStore().add("制度.txt", LOG_POLICY.encode("utf-8"), by="ann@acme.cn")
    assert doc.uploaded_by == "ann@acme.cn"
