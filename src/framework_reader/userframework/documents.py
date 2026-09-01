"""用户上传的配套文档，及「哪一段跟这条控制有关」的检索。设计 §8 S5

为什么要它：起草器写出来的是**通用**的落地建议。而这个团队真正的落地方式
写在他们自己的制度里——「日志留存六个月」还是「一年」，是他们文件里的一行，
不是模型能猜出来的。把那一行喂给起草器，解读才是这家公司的解读。

**检索不用向量。** 没有嵌入模型就意味着不出网、不加依赖、不引入一个
「为什么这段没被检索到」谁也说不清的黑盒。中文按**字符二元组**取交集，
对「日志留存」「访问控制」这种术语匹配足够准，而且为什么命中一眼能看懂。

**宁可不给，也不给错的。** 交集太少就返回空——噪声接地比没有接地更糟：
模型会照着不相干的段落编出一条这家公司并不存在的制度。
"""
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from framework_reader.userframework.extract import chunk, extract

# 一条控制最多带几段接地材料、每段最多多长。
# 放宽只会把 payload 撑大、把信号稀释——七个字段的解读用不到一整章。
MAX_EXCERPTS = 4
MAX_CHARS = 600
# 二元组交集低于这个数就当没命中。
MIN_OVERLAP = 4

_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Document:
    id: str
    filename: str
    title: str
    chars: int
    uploaded_at: str
    uploaded_by: str
    chunks: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _grams(text: str) -> set[str]:
    clean = _NOISE.sub("", text)
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


class DocumentStore:
    def __init__(self, path: Path | None = None) -> None:
        from framework_reader.userframework.store import default_path

        self.path = Path(path) if path else default_path()

    def _conn(self):
        from framework_reader.userframework.store import connect

        conn = connect(self.path)
        assert conn is not None
        return conn

    # ---------- 写 ----------

    def add(self, filename: str, data: bytes, *, by: str = "",
            title: str = "") -> Document:
        """解析在**落库之前**——解析不了的文件一个字节都不留下。"""
        text = extract(filename, data)
        parts = chunk(text)
        if not parts:
            from framework_reader.userframework.extract import UnsupportedDocument

            raise UnsupportedDocument("No usable text found in this file.")

        doc_id = str(uuid.uuid4())
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO user_document "
                "(id, filename, sha256, uploaded_at, title, chars, uploaded_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc_id, filename, hashlib.sha256(data).hexdigest(), _now(),
                 title or filename, len(text), by),
            )
            conn.executemany(
                "INSERT INTO user_document_chunk "
                "(document_id, ordinal, heading, text) VALUES (?, ?, ?, ?)",
                [(doc_id, i, heading, body)
                 for i, (heading, body) in enumerate(parts)],
            )
            conn.commit()
        finally:
            conn.close()
        return self.get(doc_id)

    def delete(self, doc_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM user_document_chunk WHERE document_id = ?",
                         (doc_id,))
            conn.execute("DELETE FROM user_document WHERE id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()

    # ---------- 读 ----------

    def get(self, doc_id: str) -> Document | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT d.*, (SELECT COUNT(*) FROM user_document_chunk c "
                "WHERE c.document_id = d.id) AS n FROM user_document d "
                "WHERE d.id = ?", (doc_id,)).fetchone()
            return self._doc(row) if row else None
        finally:
            conn.close()

    def list_documents(self) -> list[Document]:
        conn = self._conn()
        try:
            return [self._doc(r) for r in conn.execute(
                "SELECT d.*, (SELECT COUNT(*) FROM user_document_chunk c "
                "WHERE c.document_id = d.id) AS n FROM user_document d "
                "ORDER BY d.uploaded_at DESC")]
        finally:
            conn.close()

    def _doc(self, row) -> Document:
        return Document(
            id=row["id"], filename=row["filename"], title=row["title"],
            chars=row["chars"], uploaded_at=row["uploaded_at"],
            uploaded_by=row["uploaded_by"], chunks=row["n"] if "n" in row.keys() else 0,
        )

    def chunks(self, doc_id: str) -> list[tuple[str, str]]:
        """一份文档切出来的全部段落。**页面要能原样显示它**——

        「模型到底看到了什么」不能只有我们知道。看不见就没人会信它。
        """
        conn = self._conn()
        try:
            return [(r["heading"], r["text"]) for r in conn.execute(
                "SELECT heading, text FROM user_document_chunk "
                "WHERE document_id = ? ORDER BY ordinal", (doc_id,))]
        finally:
            conn.close()

    # ---------- 检索 ----------

    def excerpts(self, query: str, *, limit: int = MAX_EXCERPTS,
                 max_chars: int = MAX_CHARS) -> list[str]:
        """跟 `query` 最相关的几段，形如「《制度》第一章 日志管理：……」。"""
        wanted = _grams(query)
        if len(wanted) < 2:
            return []
        conn = self._conn()
        try:
            rows = list(conn.execute(
                "SELECT d.title, c.heading, c.text FROM user_document_chunk c "
                "JOIN user_document d ON d.id = c.document_id"))
        finally:
            conn.close()

        scored = []
        for row in rows:
            body = row["text"]
            grams = _grams(f"{row['heading']} {body}")
            overlap = len(wanted & grams)
            if overlap < MIN_OVERLAP:
                # 宁可不给。噪声接地比没有接地更糟。
                continue
            # 除以长度的平方根：不这样的话，最长的那一段永远赢——
            # 它只是碰巧包含了更多二元组，不是更相关。
            scored.append((overlap / (len(grams) ** 0.5), row))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        out = []
        for _, row in scored[:limit]:
            body = row["text"]
            if len(body) > max_chars:
                body = body[:max_chars].rstrip() + "..."
            where = f"'{row['title']}' {row['heading']}".strip()
            out.append(f"{where}: {body}")
        return out
