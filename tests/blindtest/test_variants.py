from framework_reader.blindtest.variants import (
    LEAK_WORDS,
    leak_hits,
    render_original,
    render_product,
)
from framework_reader.interpret.model import (
    ALL_FIELDS,
    Basis,
    Field,
    Interpretation,
)


def _interp() -> Interpretation:
    fields = {n: Field(value=f"{n} 的内容", basis=Basis.INFERRED) for n in ALL_FIELDS}
    fields["practice"] = Field(
        value={"1": "一档", "2": "二档", "3": "三档"}, basis=Basis.INFERRED
    )
    fields["auditor_asks"] = Field(value=["追问一", "追问二"], basis=Basis.INFERRED)
    fields["regional_note"] = Field(value=None, basis=Basis.INFERRED)
    return Interpretation(control_id="NIST-CSF-2.0:DE.CM-01", fields=fields)


def test_product_render_shows_every_non_empty_field():
    text = render_product(_interp())
    assert "intent 的内容" in text
    assert "一档" in text and "三档" in text
    assert "追问一" in text and "追问二" in text


def test_empty_field_is_omitted_not_rendered_as_null():
    """留空的字段不该以「None」「null」的样子出现在评委眼前。"""
    text = render_product(_interp())
    assert "None" not in text and "null" not in text


def test_product_render_leaks_nothing():
    assert leak_hits(render_product(_interp())) == []


def test_leak_words_cover_the_spec_list():
    for word in ("interpretation", "basis", "provenance", "inferred", "practitioner"):
        assert word in LEAK_WORDS


def test_leak_detection_is_case_insensitive():
    assert leak_hits("这里有 Provenance 字样") == ["provenance"]


def test_leak_detection_finds_multiple():
    assert sorted(leak_hits("basis 与 inferred 都在")) == ["basis", "inferred"]


def test_control_id_is_not_a_leak():
    """三份变体共享同一控制编号，它不泄露来源，必须保留——评委要知道在评哪条。"""
    assert leak_hits("NIST-CSF-2.0:DE.CM-01") == []


def test_original_render_is_just_the_outcome_text():
    text = render_original("Networks and network services are monitored")
    assert "Networks and network services are monitored" in text
    assert leak_hits(text) == []


# ---------- 映射与出处（spec §7.3 第二条通过线的 OR 分支） ----------

def _refs() -> list:
    from framework_reader.blindtest.variants import MappingRef

    return [
        MappingRef(
            control_id="NIST-800-53-R5:AC-4", label="Information Flow Enforcement",
            framework="NIST SP 800-53 Rev. 5", relation="related",
            source="NIST-OLIR-csf-2.0-to-sp800-53r5", level="L1_OFFICIAL",
        ),
        MappingRef(
            control_id="NIST-800-53-R5:CA-7", label="Continuous Monitoring",
            framework="NIST SP 800-53 Rev. 5", relation="related",
            source="NIST-OLIR-csf-2.0-to-sp800-53r5", level="L1_OFFICIAL",
        ),
    ]


def test_no_mapping_section_when_there_is_nothing_to_show():
    """一条边都没有的控制不该出现一个空标题。"""
    text = render_product(_interp(), mappings=[])
    assert "Mappings to other frameworks" not in text


def test_mappings_are_rendered_with_id_and_label():
    text = render_product(_interp(), mappings=_refs())
    assert "AC-4 Information Flow Enforcement" in text
    assert "CA-7 Continuous Monitoring" in text


def test_the_target_framework_prefix_is_stripped_from_each_id():
    """`NIST-800-53-R5:AC-4` 在 800-53 那一组里写成 `AC-4` 就够了。"""
    text = render_product(_interp(), mappings=_refs())
    assert "NIST-800-53-R5:AC-4" not in text
    assert "NIST SP 800-53 Rev. 5" in text


def test_the_source_of_the_mapping_is_shown():
    """通过线要的是「对应关系标了出处」，不是光有对应关系。"""
    text = render_product(_interp(), mappings=_refs())
    assert "NIST-OLIR-csf-2.0-to-sp800-53r5" in text


def test_level_and_relation_are_translated_not_shown_as_enum_names():
    text = render_product(_interp(), mappings=_refs())
    assert "Official mapping" in text and "Related" in text
    assert "L1_OFFICIAL" not in text and "related" not in text


def test_the_count_is_shown_so_the_reader_knows_the_list_is_complete():
    text = render_product(_interp(), mappings=_refs())
    assert "2 controls" in text


def test_mappings_are_grouped_by_framework_and_relation():
    from framework_reader.blindtest.variants import MappingRef

    refs = _refs() + [MappingRef(
        control_id="NIST-800-53-R5:AU-12", label="Audit Record Generation",
        framework="NIST SP 800-53 Rev. 5", relation="equivalent",
        source="NIST-OLIR-csf-2.0-to-sp800-53r5", level="L1_OFFICIAL",
    )]
    text = render_product(_interp(), mappings=refs)
    assert "Related, 2 controls" in text
    assert "Equivalent, 1 control" in text


def test_an_unknown_relation_or_level_falls_back_to_the_raw_value():
    from framework_reader.blindtest.variants import MappingRef

    text = render_product(_interp(), mappings=[MappingRef(
        control_id="X:1", label="L", framework="X", relation="weird",
        source="s", level="L9_NEW",
    )])
    assert "weird" in text and "L9_NEW" in text


def test_the_mapping_section_leaks_nothing():
    assert leak_hits(render_product(_interp(), mappings=_refs())) == []


def test_mappings_default_to_empty_so_old_callers_still_work():
    assert render_product(_interp()) == render_product(_interp(), mappings=[])
