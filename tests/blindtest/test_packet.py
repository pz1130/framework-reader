import json

import pytest

from framework_reader.blindtest.packet import (
    NONE_PICK,
    PacketItem,
    PacketLeakError,
    build_packet,
    load_cached_items,
)

LETTERS = ("A", "B", "C")


def _items(n: int = 3) -> list[PacketItem]:
    return [
        PacketItem(
            control_id=f"NIST-CSF-2.0:GV.OC-0{i}",
            product=f"产品解读 {i}",
            bare=f"裸问回答 {i}",
            original=f"Original outcome {i}",
        )
        for i in range(1, n + 1)
    ]


def test_packet_contains_every_control_and_all_three_variants():
    text, _ = build_packet(_items(), seed=42)
    for i in (1, 2, 3):
        assert f"GV.OC-0{i}" in text
        assert f"产品解读 {i}" in text
        assert f"裸问回答 {i}" in text
        assert f"Original outcome {i}" in text


def test_packet_labels_variants_as_letters_only():
    text, _ = build_packet(_items(1), seed=42)
    for letter in LETTERS:
        assert letter in text
    assert "product" not in text and "bare" not in text and "original" not in text


def test_answer_key_maps_every_letter_for_every_control():
    _, key = build_packet(_items(), seed=42)
    for control_id in key.order:
        assert set(key.mapping[control_id]) == set(LETTERS)
        assert sorted(key.mapping[control_id].values()) == ["bare", "original", "product"]


def test_letter_order_differs_across_controls():
    """同一份 packet 里逐条独立随机——否则评委从第一条就能推出后面九条。"""
    _, key = build_packet(_items(10), seed=42)
    layouts = {tuple(key.mapping[c][l] for l in LETTERS) for c in key.order}
    assert len(layouts) > 1


def test_same_seed_gives_the_same_packet():
    assert build_packet(_items(), seed=42)[0] == build_packet(_items(), seed=42)[0]


def test_answer_key_records_the_seed():
    _, key = build_packet(_items(), seed=99)
    assert key.seed == 99


def test_leaking_content_refuses_to_produce_a_packet():
    """断言不过就不产出 packet——不是产出后再警告。spec §4"""
    items = _items(1)
    items[0].product = "这段里混进了 provenance 字样"
    with pytest.raises(PacketLeakError, match="provenance"):
        build_packet(items, seed=42)


def test_leak_check_covers_the_bare_variant_too():
    items = _items(1)
    items[0].bare = "模型自己吐出了 practitioner 这个词"
    with pytest.raises(PacketLeakError, match="practitioner"):
        build_packet(items, seed=42)


def test_packet_has_answer_instructions_for_judges():
    text, _ = build_packet(_items(1), seed=42)
    assert "which one helps you most" in text


def test_packet_offers_a_way_out_of_choosing():
    """三份都是垃圾时也得选一份的话，通过线量不出东西来。"""
    text, _ = build_packet(_items(1), seed=42)
    assert NONE_PICK in text


# ---------- 位置平衡（seed=42 实测甲有 8/10 是同一个变体） ----------

def test_letter_positions_are_balanced_across_controls():
    """逐条独立 shuffle 在 n=10 时会把位置效应和变体混在一起。spec §4"""
    from collections import Counter

    _, key = build_packet(_items(10), seed=42)
    counts = Counter(
        (key.mapping[cid][letter], letter) for cid in key.order for letter in LETTERS
    )
    assert max(counts.values()) <= 4, counts


def test_balance_holds_for_every_seed_not_just_a_lucky_one():
    from collections import Counter

    for seed in range(50):
        _, key = build_packet(_items(10), seed=seed)
        counts = Counter(
            (key.mapping[cid][letter], letter) for cid in key.order for letter in LETTERS
        )
        assert max(counts.values()) <= 4, (seed, counts)


def test_order_is_still_unpredictable_across_seeds():
    """平衡不等于固定——两个 seed 的牌面必须不同。"""
    _, a = build_packet(_items(10), seed=42)
    _, b = build_packet(_items(10), seed=43)
    assert a.mapping != b.mapping


# ---------- 标题层级 ----------

def _headings_after_the_instructions(text: str):
    import re

    body = text[text.index("## 1. "):]
    return [
        (len(m.group(1)), m.group(2))
        for m in re.finditer(r"^(#+)\s+(.*)$", body, flags=re.M)
    ]


def test_no_body_heading_competes_with_the_letter_headings():
    """裸问的回答带 ### 小标题，与 `### 甲` 同级——正文会和另外两份材料平起平坐。"""
    items = _items(1)
    items[0].bare = "# 一级\n\n## 二级\n\n### 三级\n\n#### 四级\n\n正文"
    text, _ = build_packet(items, seed=42)
    for level, title in _headings_after_the_instructions(text):
        if title in LETTERS:
            assert level == 3, title
        elif title.startswith("1. "):
            assert level == 2, title
        else:
            assert level >= 4, (level, title)


def test_demotion_keeps_the_heading_text_verbatim():
    items = _items(1)
    items[0].bare = "### 一、审计员在找什么"
    text, _ = build_packet(items, seed=42)
    assert "一、审计员在找什么" in text
    assert "\n### 一、审计员在找什么" not in text


def test_headings_inside_a_code_fence_are_left_alone():
    items = _items(1)
    items[0].bare = "```sh\n# 这是注释不是标题\ngrep x y\n```"
    text, _ = build_packet(items, seed=42)
    assert "\n# 这是注释不是标题" in text


# ---------- answer_key 的留痕 ----------

def test_answer_key_records_which_controls_were_excluded_and_why():
    """排除清单也是可复现性的一部分：事后要能回答「为什么这条不可能被抽中」。"""
    _, key = build_packet(
        _items(1), seed=42, excluded={"NIST-CSF-2.0:RS.AN-06": ["provenance"]}
    )
    assert key.excluded == {"NIST-CSF-2.0:RS.AN-06": ["provenance"]}


def test_answer_key_records_the_length_of_each_variant():
    """篇幅差是本轮最大的混淆变量，必须量出来记下来。"""
    _, key = build_packet(_items(1), seed=42)
    assert key.lengths == {
        "product": len("产品解读 1"),
        "bare": len("裸问回答 1"),
        "original": len("Original outcome 1"),
    }


# ---------- 复用已生成的变体 ----------

def _write_cache(path, items):
    path.write_text(
        json.dumps([i.model_dump() for i in items], ensure_ascii=False),
        encoding="utf-8",
    )


def test_cached_variants_are_reused_when_the_sample_is_unchanged(tmp_path):
    """裸问那份要花钱调模型，重调还会拿到不同的文字——等于换了一份题。"""
    items = _items(3)
    path = tmp_path / "variants.json"
    _write_cache(path, items)
    assert load_cached_items(path, [i.control_id for i in items]) == items


def test_a_different_sample_ignores_the_cache(tmp_path):
    path = tmp_path / "variants.json"
    _write_cache(path, _items(3))
    assert load_cached_items(path, ["NIST-CSF-2.0:GV.OC-09"]) is None


def test_a_reordered_sample_ignores_the_cache(tmp_path):
    """顺序决定甲乙丙的排布，顺序变了就不是同一份题。"""
    items = _items(3)
    path = tmp_path / "variants.json"
    _write_cache(path, items)
    ids = [i.control_id for i in items]
    assert load_cached_items(path, list(reversed(ids))) is None


def test_a_missing_or_corrupt_cache_is_not_an_error(tmp_path):
    assert load_cached_items(tmp_path / "nope.json", ["C1"]) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{ 不是 json", encoding="utf-8")
    assert load_cached_items(broken, ["C1"]) is None
