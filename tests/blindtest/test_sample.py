import pytest

from framework_reader.blindtest.packet import AnswerKey
from framework_reader.blindtest.sample import (
    eligible_for_sample,
    frame_drift,
    frame_fingerprint,
    function_of,
    quotas,
    stratified_sample,
)

# 106 条的真实构成（2026-08-21 实测）
REAL_COUNTS = {"GV": 31, "PR": 22, "ID": 21, "RS": 13, "DE": 11, "RC": 8}


def _corpus() -> list[str]:
    out = []
    for func, count in REAL_COUNTS.items():
        out += [f"NIST-CSF-2.0:{func}.XX-{i:02d}" for i in range(1, count + 1)]
    return sorted(out)


def test_function_is_the_two_letters_before_the_dot():
    assert function_of("NIST-CSF-2.0:DE.CM-01") == "DE"
    assert function_of("NIST-CSF-2.0:GV.SC-07") == "GV"


def test_quotas_match_the_spec_table():
    """spec §3：GV 3 / PR 2 / ID 2 / RS 1 / DE 1 / RC 1，合计 10。"""
    assert quotas(REAL_COUNTS, 10) == {"GV": 3, "PR": 2, "ID": 2, "RS": 1, "DE": 1, "RC": 1}


def test_quotas_always_sum_to_n():
    assert sum(quotas(REAL_COUNTS, 10).values()) == 10
    assert sum(quotas(REAL_COUNTS, 7).values()) == 7
    assert sum(quotas({"A": 1, "B": 1, "C": 1}, 2).values()) == 2


def test_quotas_are_not_hardcoded_to_the_current_corpus():
    """106 条的构成会变（补入替代条），配额必须跟着变，不能写死。"""
    assert quotas({"GV": 50, "DE": 50}, 10) == {"GV": 5, "DE": 5}


def test_sample_is_reproducible_for_a_seed():
    corpus = _corpus()
    assert stratified_sample(corpus, 10, seed=42) == stratified_sample(corpus, 10, seed=42)


def test_different_seeds_give_different_samples():
    corpus = _corpus()
    assert stratified_sample(corpus, 10, seed=42) != stratified_sample(corpus, 10, seed=43)


def test_sample_respects_the_quota_per_function():
    from collections import Counter

    picked = stratified_sample(_corpus(), 10, seed=42)
    assert len(picked) == 10
    assert Counter(function_of(c) for c in picked) == {
        "GV": 3, "PR": 2, "ID": 2, "RS": 1, "DE": 1, "RC": 1
    }


def test_sample_has_no_duplicates():
    picked = stratified_sample(_corpus(), 10, seed=7)
    assert len(set(picked)) == 10


def test_asking_for_more_than_the_corpus_fails_loudly():
    with pytest.raises(ValueError, match="Not enough"):
        stratified_sample(["NIST-CSF-2.0:GV.A-01"], 10, seed=1)


# CSF 原文里碰巧含 LEAK_WORDS 的三条（实测会让 build_packet 拒出题）
_LEAKY_ORIGINALS = {
    "NIST-CSF-2.0:RC.RP-05": (
        "The integrity of restored assets is verified, systems and services "
        "are restored, and normal operating status is confirmed"
    ),
    "NIST-CSF-2.0:RS.AN-06": (
        "Actions performed during an investigation are recorded, "
        "and the records' integrity and provenance are preserved"
    ),
    "NIST-CSF-2.0:RS.AN-07": (
        "Incident data and metadata are collected, "
        "and their integrity and provenance are preserved"
    ),
}
_CLEAN_ORIGINALS = {
    "NIST-CSF-2.0:GV.OC-01": "The organizational mission is understood",
    "NIST-CSF-2.0:DE.CM-01": "Networks and network services are monitored",
}


def test_eligible_for_sample_drops_ids_whose_original_contains_leak_words():
    ids = list(_LEAKY_ORIGINALS) + list(_CLEAN_ORIGINALS)
    outcomes = {**_LEAKY_ORIGINALS, **_CLEAN_ORIGINALS}
    assert eligible_for_sample(ids, outcomes) == list(_CLEAN_ORIGINALS)


def test_eligible_for_sample_keeps_ids_with_clean_originals():
    ids = list(_CLEAN_ORIGINALS)
    assert eligible_for_sample(ids, _CLEAN_ORIGINALS) == ids


def test_eligible_for_sample_is_deterministic():
    ids = list(_LEAKY_ORIGINALS) + list(_CLEAN_ORIGINALS)
    outcomes = {**_LEAKY_ORIGINALS, **_CLEAN_ORIGINALS}
    assert eligible_for_sample(ids, outcomes) == eligible_for_sample(ids, outcomes)


# ---------- 抽样框指纹 ----------

def test_fingerprint_ignores_input_order():
    assert frame_fingerprint(["B", "A", "C"]) == frame_fingerprint(["A", "B", "C"])


def test_fingerprint_changes_when_the_frame_changes():
    """少一条、多一条都必须换指纹——同一个 seed 会抽出另一批题。"""
    base = frame_fingerprint(["A", "B", "C"])
    assert frame_fingerprint(["A", "B"]) != base
    assert frame_fingerprint(["A", "B", "C", "D"]) != base


def _key(fingerprint: str) -> AnswerKey:
    return AnswerKey(
        seed=42, order=["A"], mapping={"A": {"A": "product", "B": "bare", "C": "original"}},
        frame_fingerprint=fingerprint,
    )


def test_no_drift_when_the_frame_is_unchanged():
    frame = ["A", "B", "C"]
    assert frame_drift(_key(frame_fingerprint(frame)), frame) is None


def test_drift_is_reported_when_the_frame_changed():
    """spec §3：seed 记进报告是为了防止重抽。抽样框变了，seed 就不再保证复现。"""
    message = frame_drift(_key(frame_fingerprint(["A", "B", "C"])), ["A", "B"])
    assert message and "Sampling frame" in message


def test_a_key_without_a_fingerprint_cannot_be_verified():
    """加指纹之前出的题，不能因为「没记录」就当成对得上。"""
    message = frame_drift(_key(""), ["A", "B", "C"])
    assert message and "cannot verify" in message
