import inspect

import pytest

from framework_reader.blindtest.packet import AnswerKey
from framework_reader.blindtest.tally import (
    MIN_ADVOCATES,
    NONE_PICK,
    PASS_RATE,
    Verdict,
    build_report,
    parse_picks,
    render_report,
    resolve,
)

KEY = AnswerKey(
    seed=42,
    order=["C1", "C2", "C3", "C4"],
    mapping={
        "C1": {"A": "product", "B": "bare", "C": "original"},
        "C2": {"A": "bare", "B": "product", "C": "original"},
        "C3": {"A": "original", "B": "bare", "C": "product"},
        "C4": {"A": "product", "B": "original", "C": "bare"},
    },
    bare_model="deepseek-chat",
    bare_prompt_version="2026.08-b1",
)


def _verdict(name: str, picks: str, note: str = "") -> Verdict:
    return Verdict(judge=name, picks=parse_picks(picks), note=note)


# ---------- 通过线不可覆盖 ----------

def test_pass_line_matches_the_spec():
    assert PASS_RATE == 0.70
    assert MIN_ADVOCATES == 2


def test_pass_line_cannot_be_passed_in_as_a_parameter():
    """spec §6：事前定死，事后不得修改。改必须改代码，从而留在 git 记录里。"""
    for func in (build_report, render_report):
        params = set(inspect.signature(func).parameters)
        assert not params & {"pass_rate", "threshold", "min_advocates"}, func.__name__


# ---------- 解析 ----------

def test_parse_picks_reads_index_to_letter():
    assert parse_picks("1=A, 2=B,3=C") == {1: "A", 2: "B", 3: "C"}


def test_parse_picks_rejects_an_unknown_letter():
    with pytest.raises(ValueError, match="丁"):
        parse_picks("1=丁")


def test_resolve_maps_letters_back_to_variants():
    assert resolve(KEY, _verdict("A", "1=A,2=B")) == {"C1": "product", "C2": "product"}


def test_resolve_rejects_out_of_range_indices():
    """0 / 负数会按下标回绕，过大则 IndexError；都必须变成明确的 ValueError。"""
    for picks in ("0=A", "99=A", "-1=A"):
        with pytest.raises(ValueError, match="out of range"):
            resolve(KEY, _verdict("A", picks))


# ---------- 统计 ----------

def test_report_counts_product_share():
    verdicts = [
        _verdict("A", "1=A,2=B,3=C,4=A"),   # 全选 product
        _verdict("B", "1=A,2=A,3=C,4=B"),   # product, bare, product, original
    ]
    report = build_report(KEY, verdicts)
    assert report.total_picks == 8
    assert report.product_picks == 6
    assert report.product_share == pytest.approx(0.75)


def test_report_also_shows_the_original_variant_count():
    """spec §5 第 3 项：(c) 的得票要列出来（仅供参考），不能不报。"""
    report = build_report(KEY, [_verdict("A", "1=C,2=C,3=A,4=B")])
    assert report.original_picks == 4


def test_product_vs_bare_ignores_original():
    """有意义的比较只有 (a) vs (b)。赢过英文原文不算成绩。spec §1.1"""
    verdicts = [_verdict("A", "1=A,2=A,3=A,4=B")]  # product, bare, original, original
    report = build_report(KEY, verdicts)
    assert report.product_vs_bare == pytest.approx(0.5)


def test_pass_needs_both_conditions():
    strong = [_verdict("A", "1=A,2=B,3=C,4=A", "auditor_asks 那几句很有用"),
              _verdict("B", "1=A,2=B,3=C,4=A", "映射能看到出处，这个有价值")]
    assert build_report(KEY, strong).passed is True


def test_high_share_but_too_few_advocates_fails():
    quiet = [_verdict("A", "1=A,2=B,3=C,4=A", "还行"),
             _verdict("B", "1=A,2=B,3=C,4=A", "都差不多")]
    report = build_report(KEY, quiet)
    assert report.product_share == 1.0
    assert report.advocates == 0
    assert report.passed is False


def test_enough_advocates_but_low_share_fails():
    weak = [_verdict("A", "1=B,2=A,3=A,4=B", "common_myth 有价值"),
            _verdict("B", "1=B,2=A,3=A,4=B", "出处标得清楚")]
    report = build_report(KEY, weak)
    assert report.advocates == 2
    assert report.passed is False


def test_report_prints_the_wording_limits():
    """结论措辞边界必须印在报告上，不能只写在 spec 里。spec §1.1、§2"""
    text = render_report(build_report(KEY, [_verdict("A", "1=A,2=B,3=C,4=A")]))
    assert "cannot claim" in text
    assert "not a win" in text


def test_report_records_seed_and_bare_model():
    text = render_report(build_report(KEY, [_verdict("A", "1=A,2=B,3=C,4=A")]))
    assert "42" in text and "deepseek-chat" in text and "2026.08-b1" in text


# ---------- 评语必须人眼可核 ----------

def test_report_prints_every_note_verbatim():
    """advocates 是子串匹配，分不清褒贬。最容易自欺的那条通过线必须留下证据。"""
    verdicts = [
        _verdict("老王", "1=A,2=B,3=C,4=A", "追问那几句有用"),
        _verdict("小李", "1=A,2=B,3=C,4=A", "看不出误解那段有什么用"),
    ]
    text = render_report(build_report(KEY, verdicts))
    assert "追问那几句有用" in text
    assert "看不出误解那段有什么用" in text
    assert "老王" in text and "小李" in text


def test_a_negative_mention_still_counts_so_the_report_says_to_confirm_by_hand():
    verdicts = [
        _verdict("老王", "1=A,2=B,3=C,4=A", "看不出误解那段有什么用"),
        _verdict("小李", "1=A,2=B,3=C,4=A", "追问那两句也没什么用"),
    ]
    report = build_report(KEY, verdicts)
    assert report.advocates == 2          # 子串匹配就是分不清
    assert "confirm by hand" in render_report(report)


def test_a_judge_who_left_no_note_still_shows_up():
    text = render_report(build_report(KEY, [_verdict("老王", "1=A")]))
    assert "老王" in text


# ---------- 漏答 ----------

def test_report_records_how_many_picks_each_judge_submitted():
    report = build_report(
        KEY, [_verdict("A", "1=A,2=B"), _verdict("B", "1=A,2=B,3=C,4=A")]
    )
    assert report.picks_by_judge == {"A": 2, "B": 4}
    assert report.expected_picks == 4


def test_report_flags_an_incomplete_submission():
    """某位只交 3 条，分母静默变小——这件事必须写在脸上。"""
    text = render_report(build_report(KEY, [_verdict("A", "1=A,2=B")]))
    assert "missing picks" in text


def test_a_complete_submission_is_not_flagged():
    text = render_report(build_report(KEY, [_verdict("A", "1=A,2=B,3=C,4=A")]))
    assert "missing picks" not in text


# ---------- 篇幅这个混淆变量 ----------

def test_report_states_the_length_asymmetry_as_a_known_confound():
    key = KEY.model_copy(
        update={"lengths": {"product": 6472, "bare": 20222, "original": 1186}}
    )
    text = render_report(build_report(key, [_verdict("A", "1=A,2=B,3=C,4=A")]))
    assert "3.1x" in text
    assert "confound" in text


def test_report_without_lengths_still_renders():
    text = render_report(build_report(KEY, [_verdict("A", "1=A")]))
    assert "Verdict:" in text


# ---------- 评委名不能当路径用 ----------

def test_judge_name_cannot_escape_the_verdicts_directory():
    from framework_reader.blindtest.tally import safe_judge_filename

    for hostile in ("../../etc/passwd", "a/b", r"c\d", "."):
        cleaned = safe_judge_filename(hostile)
        assert "/" not in cleaned and "\\" not in cleaned
        assert cleaned not in ("", ".", "..")


def test_a_normal_chinese_name_survives_unchanged():
    from framework_reader.blindtest.tally import safe_judge_filename

    assert safe_judge_filename("老王") == "老王"


# ---------- 「三份都没用」 ----------

def test_parse_picks_accepts_none_of_them():
    """逼评委三选一，三份都是垃圾时产品照样能赢。必须有逃生口。"""
    assert parse_picks("1=A,2=none") == {1: "A", 2: NONE_PICK}


def test_resolve_maps_none_to_its_own_bucket():
    assert resolve(KEY, _verdict("A", "1=none")) == {"C1": "none"}


def test_none_counts_in_the_denominator_but_not_as_a_product_pick():
    """算进分母、不算产品票——最严的算法。弃权不能替产品抬分。"""
    report = build_report(KEY, [_verdict("A", "1=A,2=B,3=C,4=none")])
    assert report.total_picks == 4
    assert report.product_picks == 3
    assert report.none_picks == 1
    assert report.product_share == pytest.approx(0.75)


def test_none_stays_out_of_the_head_to_head():
    """(a) vs (b) 是两两对比，「都没用」不是投给任何一方的票。"""
    report = build_report(KEY, [_verdict("A", "1=A,2=A,3=none,4=none")])
    assert report.product_vs_bare == pytest.approx(0.5)


def test_all_none_fails_the_pass_line():
    verdicts = [
        _verdict("A", "1=none,2=none,3=none,4=none", "auditor_asks 那段没用"),
        _verdict("B", "1=none,2=none,3=none,4=none", "映射的出处也没用"),
    ]
    report = build_report(KEY, verdicts)
    assert report.product_share == pytest.approx(0.0)
    assert report.passed is False


def test_report_prints_the_none_count():
    """弃权票必须出现在报告里，不能悄悄消失在分母中。"""
    text = render_report(build_report(KEY, [_verdict("A", "1=A,2=none")]))
    assert "none" in text
