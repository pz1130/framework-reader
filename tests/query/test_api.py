import sqlite3
from pathlib import Path

import pytest

from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_mappings,
)
from framework_reader.query.api import QueryAPI
from framework_reader.schema.entities import Framework, FrameworkControl, LicenseTier
from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)
from framework_reader.schema.sources import SourceRegistry

REGISTRY = SourceRegistry.load(Path("content/allowed_sources.yaml"))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "content.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [
        Framework(id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
                  tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd"),
        Framework(id="ISO-27002-2022", name="ISO/IEC 27002:2022", version="2022",
                  tier=LicenseTier.C_PURCHASE, source_url="u", license_note="购买"),
    ])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="ISO-27002-2022:A.8.16", framework_id="ISO-27002-2022",
                         label="活动监控", label_is_original=False,
                         framework_tier=LicenseTier.C_PURCHASE),
    ])
    insert_mappings(conn, [Mapping(
        from_id="NIST-CSF-2.0:DE.CM-01", to_id="ISO-27002-2022:A.8.16",
        relation=Relation.RELATED,
        provenance=Provenance(level=ProvenanceLevel.L2_DERIVED,
                              source="derived:two-hop", source_version="1",
                              derived_via=["NIST-800-53-R5:SI-4"]),
        note="",
    )], REGISTRY)
    conn.close()
    return path


def test_get_control_returns_view(db):
    api = QueryAPI(db)
    view = api.get_control("NIST-CSF-2.0:DE.CM-01")
    assert view is not None
    assert view.framework_id == "NIST-CSF-2.0"
    assert view.status == "active"


def test_get_missing_control_returns_none(db):
    assert QueryAPI(db).get_control("NOPE:1") is None


def test_neighbors_include_derived_by_default(db):
    n = QueryAPI(db).neighbors("NIST-CSF-2.0:DE.CM-01")
    assert len(n) == 1
    assert n[0].control_id == "ISO-27002-2022:A.8.16"
    assert n[0].level == "L2_DERIVED"
    assert n[0].exportable is False


def test_exportable_only_filters_out_derived(db):
    """导出路径必须看不到未确认的推导边。spec §3.3、§10.A"""
    assert QueryAPI(db).neighbors("NIST-CSF-2.0:DE.CM-01", exportable_only=True) == []


def test_neighbors_are_bidirectional(db):
    n = QueryAPI(db).neighbors("ISO-27002-2022:A.8.16")
    assert [x.control_id for x in n] == ["NIST-CSF-2.0:DE.CM-01"]


def test_search_matches_label(db):
    hits = QueryAPI(db).search("监控")
    assert [h.id for h in hits] == ["ISO-27002-2022:A.8.16"]


def test_search_matches_a_short_control_id(db):
    """条号是人记得住的那种入口。不必带框架前缀。"""
    hits = QueryAPI(db).search("DE.CM-01")
    assert [h.id for h in hits] == ["NIST-CSF-2.0:DE.CM-01"]


def test_stats_reports_counts(db):
    s = QueryAPI(db).stats()
    assert s["frameworks"] == 2
    assert s["controls"] == 2
    assert s["mappings"] == 1
    assert s["exportable_mappings"] == 0


def _superseded_db(tmp_path):
    """DE.CM-04（废止）被拆进 DE.CM-01 与 DE.CM-09。"""
    from framework_reader.pack.db import insert_supersessions
    from framework_reader.schema.entities import (
        ControlStatus,
        SupersedeRelation,
        Supersession,
    )

    path = tmp_path / "sup.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)
    insert_frameworks(conn, [Framework(
        id="NIST-CSF-2.0", name="NIST CSF 2.0", version="2.0",
        tier=LicenseTier.A_EMBEDDABLE, source_url="u", license_note="pd",
    )])
    insert_controls(conn, [
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-04", framework_id="NIST-CSF-2.0",
                         label="Malicious code is detected", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE,
                         status=ControlStatus.DEPRECATED),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-01", framework_id="NIST-CSF-2.0",
                         label="Networks are monitored", label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
        FrameworkControl(id="NIST-CSF-2.0:DE.CM-09", framework_id="NIST-CSF-2.0",
                         label="Computing hardware and software are monitored",
                         label_is_original=True,
                         framework_tier=LicenseTier.A_EMBEDDABLE),
    ])
    insert_supersessions(conn, [
        Supersession(old_id="NIST-CSF-2.0:DE.CM-04", new_id="NIST-CSF-2.0:DE.CM-01",
                     relation=SupersedeRelation.INCORPORATED_INTO),
        Supersession(old_id="NIST-CSF-2.0:DE.CM-04", new_id="NIST-CSF-2.0:DE.CM-09",
                     relation=SupersedeRelation.INCORPORATED_INTO),
    ])
    conn.close()
    return path


def test_superseded_by_answers_where_did_this_control_go(tmp_path):
    """手上拿着旧编号的人问的是「它现在是哪条」。"""
    api = QueryAPI(_superseded_db(tmp_path))
    moved = api.superseded_by("NIST-CSF-2.0:DE.CM-04")
    assert [m.control_id for m in moved] == [
        "NIST-CSF-2.0:DE.CM-01", "NIST-CSF-2.0:DE.CM-09",
    ]
    assert moved[0].label == "Networks are monitored"
    assert moved[0].relation == "incorporated_into"


def test_supersedes_answers_which_old_numbers_land_here(tmp_path):
    api = QueryAPI(_superseded_db(tmp_path))
    old = api.supersedes("NIST-CSF-2.0:DE.CM-01")
    assert [o.control_id for o in old] == ["NIST-CSF-2.0:DE.CM-04"]


def test_active_control_has_no_successors(tmp_path):
    assert QueryAPI(_superseded_db(tmp_path)).superseded_by("NIST-CSF-2.0:DE.CM-01") == []


def test_get_framework_returns_the_display_name(db):
    """映射渲染要拿框架的展示名，调用方不得自己写 SQL。"""
    api = QueryAPI(db)
    view = api.get_framework("ISO-27002-2022")
    assert view is not None
    assert view.name == "ISO/IEC 27002:2022"
    assert view.tier == LicenseTier.C_PURCHASE.value


def test_get_framework_returns_none_for_an_unknown_id(db):
    assert QueryAPI(db).get_framework("NOPE") is None


# ---------- 解读的成色（主 spec §7.3.1） ----------

def _put(db, state: str):
    import sqlite3
    from datetime import datetime, timezone

    from framework_reader.interpret.model import (
        ALL_FIELDS, Basis, Field, Interpretation, InterpretationProvenance,
        InterpretationState,
    )
    from framework_reader.pack.db import insert_interpretations

    prov = InterpretationProvenance()
    if state == "confirmed":
        prov = InterpretationProvenance(
            confirmed_by="jc", confirmed_at=datetime.now(timezone.utc)
        )
    interp = Interpretation(
        control_id="NIST-CSF-2.0:DE.CM-01",
        state=InterpretationState(state),
        fields={
            name: Field(
                value="防的是没人看网络" if name == "intent" else None,
                basis=Basis.INFERRED,
            )
            for name in ALL_FIELDS
        },
        provenance=prov,
    )
    conn = sqlite3.connect(db)
    insert_interpretations(conn, [interp])
    conn.close()


def test_interpretation_state_is_readable(db):
    _put(db, "draft")
    assert QueryAPI(db).interpretation_state("NIST-CSF-2.0:DE.CM-01") == "draft"


def test_a_control_without_an_interpretation_has_no_state(db):
    assert QueryAPI(db).interpretation_state("NIST-CSF-2.0:DE.CM-01") is None


def test_reading_the_interpretation_still_works(db):
    _put(db, "confirmed")
    fields = QueryAPI(db).interpretation("NIST-CSF-2.0:DE.CM-01")
    assert fields["intent"]["value"] == "防的是没人看网络"


def test_search_also_looks_inside_the_interpretation(db):
    """条号记不住、英文标题搜不到中文——真实的入口是解读正文。"""
    _put(db, "draft")
    hits = QueryAPI(db).search("没人看网络")
    assert [c.id for c in hits] == ["NIST-CSF-2.0:DE.CM-01"]


def test_a_control_is_not_returned_twice_when_both_sides_match(db):
    _put(db, "draft")
    hits = QueryAPI(db).search("网络")
    assert len(hits) == len({c.id for c in hits})
