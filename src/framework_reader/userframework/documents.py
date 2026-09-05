"""Supporting documents uploaded by the user, plus the search for "which passage
relates to this control". Design §8 S5

Why it exists: the drafter writes **generic** implementation advice. But the way
this team actually implements things is written in their own policies - whether
"logs are kept for six months" or "for a year" is a line in their documents,
not something the model can guess. Feed that line to the drafter, and the
interpretation becomes this company's interpretation.

**No vectors in the search.** No embedding model means nothing leaves the
network, no new dependency, and no black box that nobody can question about
"why wasn't this passage retrieved". Chinese text is intersected as **character
bigrams**, which matches terms like "log retention" or "access control"
accurately enough, and why something hit is obvious at a glance.

**Rather give nothing than give the wrong thing.** Too small an overlap returns
empty - noisy grounding is worse than no grounding: the model will follow an
irrelevant passage and invent a policy this company does not have.
"""
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from framework_reader.userframework.extract import chunk, extract

# How many grounding excerpts one control may carry, and how long each may be.
# Raising the limits only bloats the payload and dilutes the signal - an
# interpretation of seven fields has no use for a whole chapter.
MAX_EXCERPTS = 4
MAX_CHARS = 600
# Below this bigram-overlap count, treat it as no match.
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

    # ---------- write ----------

    def add(self, filename: str, data: bytes, *, by: str = "",
            title: str = "") -> Document:
        """Parse **before** anything is stored - a file that cannot be parsed leaves not a single byte behind."""
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

    # ---------- read ----------

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
        """Every chunk cut from one document. **The page must be able to show it
        verbatim** -

        "What the model actually saw" cannot be something only we know. If it
        cannot be seen, nobody will trust it.
        """
        conn = self._conn()
        try:
            return [(r["heading"], r["text"]) for r in conn.execute(
                "SELECT heading, text FROM user_document_chunk "
                "WHERE document_id = ? ORDER BY ordinal", (doc_id,))]
        finally:
            conn.close()

    # ---------- search ----------

    def excerpts(self, query: str, *, limit: int = MAX_EXCERPTS,
                 max_chars: int = MAX_CHARS) -> list[str]:
        """The excerpts most relevant to `query`, in the form
        "'Policy' Chapter 1 Log Management: ...".
        """
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
                # Rather give nothing. Noisy grounding is worse than no grounding.
                continue
            # Divide by the square root of the length: otherwise the longest
            # excerpt always wins - it merely happens to contain more bigrams,
            # not to be more relevant.
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
