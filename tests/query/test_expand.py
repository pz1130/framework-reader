"""AI 扩词：模型的输出是不可信输入。编造的条号不能出现在结果里。"""
import sqlite3

import pytest

from framework_reader.pack.db import create_schema, insert_controls, insert_frameworks
from framework_reader.query.api import QueryAPI
from framework_reader.query.expand import hits_for, parse_expansion
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier


GOOD = '{"terms": ["日志留存", "log retention"], "ids": ["DE.CM-01"]}'


def test_a_clean_expansion_parses():
    terms, ids, error = parse_expansion(GOOD)
    assert error == ""
    assert terms == ["日志留存", "log retention"]
    assert ids == ["DE.CM-01"]


def test_a_fence_is_peeled():
    terms, ids, error = parse_expansion("```json\n" + GOOD + "\n```")
    assert error == "" and terms and ids


def test_garbage_is_an_error_not_a_crash():
    _, _, error = parse_expansion("我觉得是监控那条")
    assert error


def test_empty_is_an_error():
    _, _, error = parse_expansion("")
    assert error


def test_non_strings_are_dropped():
    terms, ids, error = parse_expansion(
        '{"terms": ["日志", 3, null], "ids": [{"x": 1}, "A.8.15"]}')
    assert error == ""
    assert terms == ["日志"]
    assert ids == ["A.8.15"]


def test_blank_entries_are_dropped():
    terms, ids, error = parse_expansion(
        '{"terms": ["  日志留存  ", ""], "ids": [" DE.CM-01 ", ""]}')
    assert error == ""
    assert terms == ["日志留存"]
    assert ids == ["DE.CM-01"]


def test_too_many_entries_are_capped():
    import json

    blob = json.dumps({
        "terms": [f"t{i}" for i in range(20)],
        "ids": [f"i{i}" for i in range(20)],
    })
    terms, ids, error = parse_expansion(blob)
    assert error == ""
    assert terms == [f"t{i}" for i in range(8)]
    assert ids == [f"i{i}" for i in range(8)]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd")])
    insert_controls(conn, [FrameworkControl(
        id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
        label="Networks are monitored", label_is_original=True,
        framework_tier=LicenseTier.A_EMBEDDABLE)])
    conn.close()
    return path


def test_a_hallucinated_id_is_dropped(db):
    hits = hits_for(QueryAPI(db), terms=[], ids=["PCI-DSS:1.1", "DE.CM-01"])
    assert [h.id for h in hits] == ["NIST-CSF-2.0:DE.CM-01"]


def test_a_made_up_term_does_not_invent_a_control(db):
    assert hits_for(QueryAPI(db), terms=["量子计算"], ids=[]) == []


def test_hits_are_not_repeated(db):
    hits = hits_for(
        QueryAPI(db),
        terms=["DE.CM-01", "Networks"],
        ids=["NIST-CSF-2.0:DE.CM-01", "DE.CM-01"],
    )
    assert [h.id for h in hits] == ["NIST-CSF-2.0:DE.CM-01"]
