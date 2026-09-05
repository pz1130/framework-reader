"""条款页的对话记录。

**对话跟着条款走，不跟着人走**——这个产品是一个安全团队协作一套材料，
签字的人要能看到「这句话当初是怎么来的」。

模型的提议存下来，但**写库要人点头**：`applied_at` 有值才算写过。
"""
from framework_reader.userframework.chat import ChatStore


def _store(tmp_path):
    return ChatStore(tmp_path / "user.sqlite")


def test_a_turn_comes_back_in_the_order_it_was_said(tmp_path):
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="user", text="这条太笼统了", actor="ann")
    store.say("ACME-1:3.1", role="ai", text="可以写成……")
    turns = store.history("ACME-1:3.1")
    assert [t.role for t in turns] == ["user", "ai"]
    assert turns[0].text == "这条太笼统了"


def test_turns_are_kept_per_control(tmp_path):
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="user", text="甲")
    store.say("ACME-1:3.2", role="user", text="乙")
    assert [t.text for t in store.history("ACME-1:3.1")] == ["甲"]


def test_who_said_it_is_recorded(tmp_path):
    """一个团队共用一套材料，得知道是谁问的。"""
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="user", text="问一句", actor="ann@acme.cn")
    assert store.history("ACME-1:3.1")[0].actor == "ann@acme.cn"


def test_a_proposal_starts_unapplied(tmp_path):
    """模型说的话永远不会自己进库。"""
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="ai", text="建议这么写",
              proposal=[{"field": "practice", "value": "新的落地建议"}])
    turn = store.history("ACME-1:3.1")[0]
    assert turn.proposal == [{"field": "practice", "value": "新的落地建议"}]
    assert not turn.applied


def test_applying_marks_it(tmp_path):
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="ai", text="建议",
              proposal=[{"field": "practice", "value": "x"}])
    turn_id = store.history("ACME-1:3.1")[0].turn_id
    store.mark_applied(turn_id)
    assert store.history("ACME-1:3.1")[0].applied


def test_an_applied_proposal_cannot_be_applied_twice(tmp_path):
    """点两次「确定」不该写两次库、记两条审计。"""
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="ai", text="建议",
              proposal=[{"field": "practice", "value": "x"}])
    turn_id = store.history("ACME-1:3.1")[0].turn_id
    assert store.mark_applied(turn_id) is True
    assert store.mark_applied(turn_id) is False


def test_the_recent_turns_are_capped_for_the_model(tmp_path):
    """每一句都要把历史重新喂一遍。不封顶的话聊得越久每句越贵。"""
    store = _store(tmp_path)
    for n in range(20):
        store.say("ACME-1:3.1", role="user", text=f"第 {n} 句")
    assert len(store.recent("ACME-1:3.1", turns=6)) == 6
    assert store.recent("ACME-1:3.1", turns=6)[-1].text == "第 19 句"


def test_recent_keeps_them_in_order(tmp_path):
    store = _store(tmp_path)
    for n in range(10):
        store.say("ACME-1:3.1", role="user", text=f"第 {n} 句")
    got = [t.text for t in store.recent("ACME-1:3.1", turns=3)]
    assert got == ["第 7 句", "第 8 句", "第 9 句"]


def test_no_history_is_an_empty_list_not_a_crash(tmp_path):
    assert _store(tmp_path).history("ACME-1:9.9") == []


def test_a_turn_id_that_does_not_exist_is_not_a_crash(tmp_path):
    assert _store(tmp_path).mark_applied("no-such-turn") is False


def test_finding_one_turn_by_its_id(tmp_path):
    store = _store(tmp_path)
    store.say("ACME-1:3.1", role="ai", text="建议",
              proposal=[{"field": "practice", "value": "x"}])
    turn_id = store.history("ACME-1:3.1")[0].turn_id
    assert store.turn(turn_id).text == "建议"
    assert store.turn("no-such") is None
