"""对话上下文里的官方映射段。给模型的是一份「只能照抄」的清单。

这是工具和裸问模型的分界线：裸问模型会说「通常对应 A.12.4」——编的。
这里喂给它的每一行都带着库里的出处（OLIR 等）；清单为空就要它直说没有。
"""
from framework_reader.query.api import NeighborView
from framework_reader.userframework.chat import mapping_lines


def _edge(**kw):
    base = dict(control_id="NIST-800-53-R5:AC-2",
                label="Account Management", relation="related",
                level="L1_OFFICIAL",
                source="NIST-OLIR-csf-2.0-to-sp800-53r5", exportable=True)
    base.update(kw)
    return NeighborView(**base)


def test_every_edge_shows_its_id_label_and_source():
    lines = mapping_lines([
        _edge(),
        _edge(control_id="NIST-800-53-R5:AU-12", label="Audit Record Generation"),
    ])
    assert len(lines) == 2
    assert "NIST-800-53-R5:AC-2" in lines[0]
    assert "Account Management" in lines[0]
    assert "related" in lines[0]
    assert "NIST-OLIR-csf-2.0-to-sp800-53r5" in lines[0], "出处必须在行上，模型才有得照抄"


def test_no_edges_means_say_so_not_invent():
    joined = "".join(mapping_lines([]))
    assert "none found in the library" in joined
    assert "do not invent" in joined


def test_the_prompt_tells_the_model_to_stick_to_the_list():
    from framework_reader.prompts import load_prompt

    prompt = load_prompt("clause_chat")
    assert "Official mappings" in prompt
    assert "Never invent" in prompt
