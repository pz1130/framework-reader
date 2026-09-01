import pytest

from framework_reader.interpret.lint import (
    bigram_overlap,
    field_scores,
    flag_low_fidelity,
    suggest_threshold,
)
from framework_reader.interpret.model import Basis, Field, RawAnswer

ANSWERS = [
    RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了"),
    RawAnswer(n=2, text="审计员会问上次复核是谁签的字"),
]


def test_verbatim_extraction_scores_one():
    assert bigram_overlap("他们以为有张权限矩阵表", "他们以为有张权限矩阵表就算做到了") == 1.0


def test_wholesale_invention_scores_near_zero():
    score = bigram_overlap(
        "权限管理体系应当健全并定期开展合规性评估", "他们以为有张权限矩阵表就算做到了"
    )
    assert score < 0.2


def test_paraphrase_scores_in_between_and_is_not_caught():
    """诚实标注这条检查的能力边界：换近义词它拦不住。W2 spec §2.3"""
    score = bigram_overlap("他们觉得有个权限矩阵表就行了", "他们以为有张权限矩阵表就算做到了")
    assert 0.2 < score < 1.0


def test_empty_field_scores_one_not_zero():
    """留空是信号，不该被 lint 当成不忠实。"""
    assert bigram_overlap("", "任意原话") == 1.0


def test_list_field_is_scored_against_all_answers_joined():
    scores = field_scores(
        {
            "common_myth": Field(value="他们以为有张权限矩阵表", basis=Basis.PRACTITIONER),
            "auditor_asks": Field(value=["上次复核是谁签的字"], basis=Basis.PRACTITIONER),
            "regional_note": Field(value=None, basis=Basis.PRACTITIONER),
        },
        ANSWERS,
    )
    assert scores["common_myth"] == pytest.approx(1.0)
    assert scores["auditor_asks"] == pytest.approx(1.0)
    assert scores["regional_note"] == pytest.approx(1.0)


def test_flag_low_fidelity_returns_field_names_below_threshold():
    assert flag_low_fidelity({"common_myth": 0.2, "auditor_asks": 0.9}, 0.5) == ["common_myth"]


def test_suggest_threshold_sits_below_the_worst_faithful_sample():
    """标定法：取人工判定为忠实的最低重合度再下调一档。W2 spec §2.3"""
    assert suggest_threshold([0.9, 0.75, 0.82], margin=0.05) == pytest.approx(0.70)


def test_suggest_threshold_never_goes_negative():
    assert suggest_threshold([0.01], margin=0.05) == 0.0


def test_unplaced_answer_is_reported():
    """实测（DE.CM-01）：答 3「接入siem，写usecase」没进任何字段，还被静默丢弃。

    这是三句里最有从业味道的一句。落不进字段可以，静默消失不行。
    """
    from framework_reader.interpret.lint import unplaced_answers

    fields = {
        "common_myth": Field(value="他们以为有张权限矩阵表就算做到了", basis=Basis.PRACTITIONER),
        "auditor_asks": Field(value=["审计员会问上次复核是谁签的字"], basis=Basis.PRACTITIONER),
        "regional_note": Field(value=None, basis=Basis.PRACTITIONER),
    }
    answers = ANSWERS + [RawAnswer(n=3, text="接入siem，写usecase")]
    assert unplaced_answers(fields, answers) == [3]


def test_no_unplaced_when_every_answer_landed():
    from framework_reader.interpret.lint import unplaced_answers

    fields = {
        "common_myth": Field(value="他们以为有张权限矩阵表就算做到了", basis=Basis.PRACTITIONER),
        "auditor_asks": Field(value=["审计员会问上次复核是谁签的字"], basis=Basis.PRACTITIONER),
        "regional_note": Field(value=None, basis=Basis.PRACTITIONER),
    }
    assert unplaced_answers(fields, ANSWERS) == []


def test_partially_absorbed_answer_is_not_flagged():
    """抽取器只取了半句是正常的删减，不算丢。"""
    from framework_reader.interpret.lint import unplaced_answers

    fields = {
        "common_myth": Field(value="他们以为有张权限矩阵表", basis=Basis.PRACTITIONER),
        "auditor_asks": Field(value=None, basis=Basis.PRACTITIONER),
        "regional_note": Field(value=None, basis=Basis.PRACTITIONER),
    }
    answers = [RawAnswer(n=1, text="他们以为有张权限矩阵表就算做到了")]
    assert unplaced_answers(fields, answers) == []


def test_empty_answer_is_never_flagged():
    from framework_reader.interpret.lint import unplaced_answers

    fields = {n: Field(value=None, basis=Basis.PRACTITIONER)
              for n in ("common_myth", "auditor_asks", "regional_note")}
    assert unplaced_answers(fields, [RawAnswer(n=1, text="   ")]) == []


def test_citation_flags_catches_legal_article_numbers():
    """提示词明令「不要编造具体的法规条号」，DeepSeek 仍写出了 GDPR 第32条。

    那次恰好是对的（第32条确实是安全处理），但**碰巧对不等于可靠**——
    106 条里没人有精力逐条核。凡是条号、百分比、年份，一律标出来待核。
    """
    from framework_reader.interpret.lint import citation_flags

    fields = {
        "regional_note": Field(
            value="欧盟审计员常把最小权限与《通用数据保护条例》第32条挂钩追问",
            basis=Basis.INFERRED,
        ),
    }
    assert citation_flags(fields) == {"regional_note": ["第32条"]}


def test_citation_flags_catches_percentages_and_years():
    from framework_reader.interpret.lint import citation_flags

    fields = {
        "practice": Field(value={"1": "覆盖率需达到 95%", "2": "自 2023年 起要求",
                                 "3": "无"}, basis=Basis.INFERRED),
    }
    hits = citation_flags(fields)["practice"]
    assert "95%" in hits and "2023年" in hits


def test_clean_content_produces_no_flags():
    from framework_reader.interpret.lint import citation_flags

    fields = {
        "common_myth": Field(value="以为有张权限矩阵表就算做到了", basis=Basis.INFERRED),
        "auditor_asks": Field(value=["上次复核是谁签的字"], basis=Basis.INFERRED),
    }
    assert citation_flags(fields) == {}


def test_control_numbers_are_not_mistaken_for_citations():
    """A.5.22 / AC-2 / DE.CM-01 是控制编号，不是法规条号，不该被标。"""
    from framework_reader.interpret.lint import citation_flags

    fields = {
        "evidence": Field(value="参见 ISO-27002 A.5.22 与 800-53 AC-2 的证据形态",
                          basis=Basis.INFERRED),
    }
    assert citation_flags(fields) == {}
