"""有解读的框架才能导出 PDF。空字段、没解读的条款、推导边都不进。"""
import sqlite3
from io import BytesIO

import pytest
from pypdf import PdfReader

from framework_reader.interpret.model import (
    ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
    InterpretationState,
)
from framework_reader.pack.db import (
    create_schema, insert_controls, insert_frameworks, insert_interpretations,
)
from framework_reader.publish.pdf import render_framework_pdf
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier

CID = "NIST-CSF-2.0:DE.CM-01"
OTHER = "NIST-CSF-2.0:DE.CM-02"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [
        Framework(id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
        Framework(id="NIST-800-53-R5", name="NIST SP 800-53 Rev. 5", version="5",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
    ])
    insert_controls(conn, [
        FrameworkControl(id=CID, framework_id="NIST-CSF-2.0",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id=OTHER, framework_id="NIST-CSF-2.0",
                         label="No interpretation yet", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-800-53-R5:AC-1", framework_id="NIST-800-53-R5",
                         label="Policy and procedures", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    insert_interpretations(conn, [Interpretation(
        control_id=CID, state=InterpretationState.DRAFT,
        fields={
            name: Field(
                value=("防的是没人看网络" if name == "intent"
                       else {"1": "有探针", "2": "有清单", "3": "自动化"}
                       if name == "practice"
                       else ["留存期限的依据是什么"] if name == "auditor_asks"
                       else None),
                basis=Basis.INFERRED)
            for name in ALL_FIELDS
        },
        provenance=InterpretationProvenance(),
    )])
    conn.close()
    return path


def _text(blob: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(blob)).pages)


def test_a_framework_without_interpretations_cannot_be_exported(db):
    with pytest.raises(LookupError):
        render_framework_pdf(QueryAPI(db), "NIST-800-53-R5")


def test_an_unknown_framework_cannot_be_exported(db):
    with pytest.raises(LookupError):
        render_framework_pdf(QueryAPI(db), "NOPE")


def test_the_pdf_contains_the_draft_mark_and_the_written_fields(db):
    blob = render_framework_pdf(QueryAPI(db), "NIST-CSF-2.0")
    assert blob.startswith(b"%PDF")
    text = _text(blob)
    assert "DE.CM-01" in text
    assert "AI draft" in text
    assert "What it defends against" in text
    assert "防的是没人看网络" in text
    assert "How to implement" in text
    assert "有探针" in text
    assert "What auditors will probe" in text or "留存期限" in text


def test_a_control_without_an_interpretation_is_absent(db):
    text = _text(render_framework_pdf(QueryAPI(db), "NIST-CSF-2.0"))
    assert "DE.CM-02" not in text
    assert "No interpretation yet" not in text
