"""解析对话里模型的回答。

模型的输出是不可信输入。它会裹围栏、会把 updates 写成对象、会改一个
不存在的字段、会把整段新内容抄进 reply 里。这里一条都不信。
"""
from framework_reader.userframework.chat_reply import parse_reply

GOOD = ('{"reply":"我把「怎么落地」按 Okta 重写了",'
        ' "updates":[{"field":"intent","value":"防的是账号共用"}]}')


def test_a_clean_answer_parses():
    reply, updates, error = parse_reply(GOOD)
    assert error == ""
    assert "Okta" in reply
    assert updates == [{"field": "intent", "value": "防的是账号共用"}]


def test_a_fence_is_peeled():
    reply, updates, error = parse_reply("```json\n" + GOOD + "\n```")
    assert error == "" and updates


def test_an_answer_with_no_updates_is_just_a_reply():
    """他在问，不是在让改。"""
    reply, updates, error = parse_reply(
        '{"reply":"这条的证据一般准备三样……","updates":[]}')
    assert error == "" and updates == []
    assert "三样" in reply


def test_garbage_is_an_error_not_a_crash():
    _, _, error = parse_reply("我不知道该说什么")
    assert error


def test_an_empty_reply_is_an_error():
    """模型什么都没说。那不是「没有建议」，是这次调用没成。"""
    _, _, error = parse_reply("")
    assert error


def test_an_update_to_a_field_that_does_not_exist_is_dropped():
    """写进一个不存在的字段，那一段就永远看不见了。"""
    _, updates, _ = parse_reply(
        '{"reply":"改好了","updates":[{"field":"没这个字段","value":"x"}]}')
    assert updates == []


def test_a_good_update_survives_a_bad_one_beside_it():
    _, updates, _ = parse_reply(
        '{"reply":"改好了","updates":['
        '{"field":"没这个字段","value":"x"},'
        '{"field":"plain_zh","value":"大白话"}]}')
    assert [u["field"] for u in updates] == ["plain_zh"]


def test_an_update_with_no_value_is_dropped():
    _, updates, _ = parse_reply(
        '{"reply":"改好了","updates":[{"field":"plain_zh"}]}')
    assert updates == []


def test_updates_that_are_not_a_list_are_dropped_not_fatal():
    reply, updates, error = parse_reply(
        '{"reply":"说点什么","updates":{"field":"plain_zh","value":"x"}}')
    assert error == ""
    assert updates == []
    assert "说点什么" in reply


def test_the_practice_field_keeps_its_three_rungs():
    _, updates, _ = parse_reply(
        '{"reply":"改了","updates":[{"field":"practice",'
        '"value":{"1":"一档","2":"二档","3":"三档"}}]}')
    assert updates[0]["value"] == {"1": "一档", "2": "二档", "3": "三档"}


def test_auditor_asks_keeps_its_list_shape():
    _, updates, _ = parse_reply(
        '{"reply":"改了","updates":[{"field":"auditor_asks",'
        '"value":["上次复核是谁签的字","留存期限的依据是什么"]}]}')
    assert len(updates[0]["value"]) == 2


def test_a_reply_that_is_not_a_string_does_not_crash():
    reply, _, error = parse_reply('{"reply":123,"updates":[]}')
    assert error == "" and reply == "123"


def test_the_prompt_names_the_fields_the_code_actually_has():
    """提示词里写错一个字段名，模型会照着写，然后被校验静默丢掉——
    页面上显示「改了」，而什么都没变。实测 plain 就该叫 plain_zh。"""
    from framework_reader.interpret.render import FIELD_LABELS
    from framework_reader.prompts import load_prompt

    prompt = load_prompt("clause_chat")
    for name, _ in FIELD_LABELS:
        assert f"`{name}`" in prompt, f"提示词里没提 {name}"
